"""Five-variant RAG comparison ("ladder"): which part of Smart RAG helps, which hurts, how sure we are.

Every variant uses the same model, instructions, query type, top_k and citation policy; only the
index and the document_type filter differ:

  simple          raw index, no filter                         workshop Simple RAG
  smart_nofilter  smart index, no filter                       control: re-indexing alone
  smart_contract  smart index, document_type eq 'contract'     workshop Smart RAG
  smart_routed    smart index, filter picked per question by Microsoft-Decision-1.1
  smart_oracle    smart index, the question's true target type  upper bound for routing

Answers come from the deployed Foundry prompt agents (randomized order, N repeats), are judged
in Foundry by a different judge model, and get objective metrics computed locally from answer
text, resolved citations and corpus ground truth. References never reach agents or the router.
Rules, metrics and decision criteria are pre-registered in docs/evaluation-protocol.md.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import secrets
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any, cast

from ..agents.prompt import AgentSpec, PromptAgents, agent_specs
from ..azclient import arm, credential
from ..capacity import AGENT_TOKENS_PER_QUESTION, MODEL_USD_PER_M
from ..config import Environment
from ..data.corpus import CORPUS_DIR, load_ground_truth, load_manifest
from ..decision.client import DecisionClient
from ..decision.config import ClassifierConfig
from ..errors import AgentError, Blocked, WorkshopError
from ..naming import EVAL_ROUTES, ParticipantNames
from ..plan import Plan
from ..search.client import SearchClient
from . import stats
from .metrics import distinct_docs, ndcg_at_k, precision_at_k, recall_at_k

DATASET = CORPUS_DIR / "rag_eval_v2.json"
TYPES = ("contract", "invoice", "policy", "technical_report", "correspondence", "presentation")
STRATA = ("contract_distractor", "contract_clean", "other_type", "unanswerable")
SPLITS = ("dev", "test")
VARIANTS = ("simple", "smart_nofilter", "smart_contract", "smart_routed", "smart_oracle")
JUDGE_METRICS = ("response_completeness", "groundedness")
K = 5
MIN_FACT_CHARS = 4
NO_CITATIONS = "(the response cites no documents)"
EMPTY_RESPONSE = "(empty response)"
PLACEHOLDER = re.compile(
    r"remote_functions|tool call in progress|^\W*(?:i['\u2019]ll|i will|let me)\s+(?:search|look)", re.I)
TRANSIENT = ("429", "too many requests", "rate limit", "timed out", "timeout", " 500", " 502", " 503", " 504")
TERMINAL = {"completed", "failed", "canceled", "cancelled"}

# Pre-registered (docs/evaluation-protocol.md). Do not change after seeing test results.
PRIMARY = "clean_answer"
PROTOCOL_TEST_REPEATS = 3
EQUIVALENCE_MARGIN = 0.10
TIE_MARGIN = {"response_completeness": 0.5, "groundedness": 0.5}
LOWER_IS_BETTER = {"distractor_mention", "off_target_citation", "operational_failure", "off_target_retrieved"}

ROUTER = ClassifierConfig(
    version="router-2026.1",
    question="answer_source",
    instructions=("A colleague asks a question about Contoso Oil's documents. Which kind of document is the "
                  "authoritative source for the answer? Choose 'any' when the answer needs several document "
                  "types or no single type clearly holds it."),
    criteria={
        "contract": "Signed agreements, charters, master or framework terms between Contoso and a supplier: "
                    "agreed payment terms, rates, discounts, retention, liability, service credits, warranties, "
                    "notice and termination.",
        "invoice": "Supplier bills: amounts actually charged, line items, invoice numbers, and credits or "
                   "retention applied on a specific bill.",
        "policy": "Internal company policies and standards: default rules, approval thresholds, required "
                  "procedures, travel, mobilisation and safety rules.",
        "technical_report": "Engineering findings: inspection, survey, well test, maintenance, failure and "
                            "vibration analysis results and recommendations.",
        "correspondence": "Letters, emails and memos: what someone asked, notified, proposed, disputed or "
                          "followed up on.",
        "presentation": "Slide decks: training material, town halls, campaign updates, roadmaps, targets and "
                        "ambitions.",
        "any": "The answer needs several document types, or none of the types above clearly holds it.",
    },
    abstain_label="any",
    min_probability=0.5,
    min_margin=0.0,
    max_chars=2000,
).validate()

_TRANSLATE = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                            "\u2014": "-", "\u2011": "-", "\u00a0": " "})


def norm(text: str) -> str:
    return " ".join(text.translate(_TRANSLATE).lower().split())


_ONES = {w: i for i, w in enumerate((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"))}
_TENS = {w: 10 * (i + 2) for i, w in enumerate(
    ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"))}


def canon(text: str) -> str:
    """Matching form shared by dataset validation and answer scoring: drops legal-style numeric
    parentheticals ('thirty (30) days'), maps number words to digits, '%' to 'percent' and currency
    symbols to codes, so 'first twelve (12) months', 'first 12 months' and 'first twelve months' agree."""
    t = norm(text)
    t = re.sub(r"\s*\(\s*[\d.,]+\s*%?\s*\)", "", t)
    t = t.replace("per cent", "percent").replace("%", " percent").replace("£", "gbp ").replace("€", "eur ")
    t = re.sub(rf"\b({'|'.join(_TENS)})[- ]({'|'.join(list(_ONES)[1:10])})\b",
               lambda m: str(_TENS[m[1]] + _ONES[m[2]]), t)
    t = re.sub(rf"\b({'|'.join(_TENS)})\b", lambda m: str(_TENS[m[1]]), t)
    t = re.sub(rf"\b({'|'.join(_ONES)})\b", lambda m: str(_ONES[m[1]]), t)
    t = re.sub(r"\b(\d+) hundred\b", lambda m: str(int(m[1]) * 100), t)
    return " ".join(t.split())


def contains(haystack: str, needle: str) -> bool:
    """Canonical match that respects word/number boundaries: '45 days' does not match '145 days'."""
    pattern = r"(?<![a-z0-9.])" + re.escape(canon(needle)) + r"(?![a-z0-9])"
    return re.search(pattern, canon(haystack)) is not None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _save(path: Path, value: Any) -> None:
    """Atomic replace: an interruption never leaves a truncated checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _acceptable(labels: dict[str, dict[str, Any]], doc_id: str) -> set[str]:
    gt = labels.get(doc_id) or {}
    return set(gt.get("acceptable") or [gt.get("document_type")])


# ---- dataset -----------------------------------------------------------------------------------
def load_dataset(path: Path = DATASET, *, split: str = "dev") -> list[dict[str, Any]]:
    """Validate every row mechanically against the corpus, then select a split (dev | test | all)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    corpus_version, docs = load_manifest()
    if data.get("corpus_version") != corpus_version:
        raise WorkshopError(f"{path.name} references corpus {data.get('corpus_version')}, not {corpus_version}")
    labels = load_ground_truth()
    texts = {d.doc_id: norm((CORPUS_DIR / "text" / f"{d.doc_id}.txt").read_text(encoding="utf-8")) for d in docs}
    problems: list[str] = []
    ids: set[str] = set()
    queries: set[str] = set()
    for row in data["queries"]:
        qid = str(row.get("id"))

        def bad(message: str, qid: str = qid) -> None:
            problems.append(f"{qid}: {message}")

        if qid in ids:
            bad("duplicate id")
        ids.add(qid)
        if norm(row.get("query", "")) in queries or not row.get("query"):
            bad("empty or duplicate query")
        queries.add(norm(row.get("query", "")))
        if row.get("split") not in SPLITS or row.get("stratum") not in STRATA:
            bad("invalid split or stratum")
        target = row.get("target_type")
        if target not in TYPES and not (target is None and row.get("stratum") == "unanswerable"):
            bad(f"invalid target_type {target!r}")
        sources = list(row.get("source_doc_ids") or [])
        distractors = list(row.get("distractor_doc_ids") or [])
        if any(d not in texts for d in sources + distractors):
            bad("unknown document id")
            continue
        if set(sources) & set(distractors):
            bad("a document is both source and distractor")
        if not sources and target is not None:
            bad("answerable target type without source documents")
        if set(row.get("evidence") or {}) != set(sources):
            bad("evidence keys must equal source_doc_ids")
        for doc_id, quote in (row.get("evidence") or {}).items():
            if doc_id in texts and norm(quote) not in texts[doc_id]:
                bad(f"evidence quote not found in {doc_id}")
        for doc_id in sources:
            if target not in _acceptable(labels, doc_id):
                bad(f"{doc_id} is not a {target}")
        if not row.get("ground_truth") or not row.get("expected_behavior"):
            bad("missing ground_truth or expected_behavior")
        facts = row.get("required_facts") or []
        if row.get("stratum") != "unanswerable" and not facts:
            bad("answerable question without required facts")
        required_alts: set[str] = set()
        for fact in facts:
            if not fact or any(len(a.strip()) < MIN_FACT_CHARS for a in fact):
                bad(f"required fact too short or empty: {fact}")
                continue
            required_alts.update(norm(a) for a in fact)
            if not any(contains(texts[d], fact[0]) for d in sources):
                bad(f"required fact '{fact[0]}' not found in any source document")
        reference = norm(row.get("query", "")) + " || " + norm(row.get("ground_truth", ""))
        for fact in row.get("distractor_facts") or []:
            if not fact or any(len(a.strip()) < MIN_FACT_CHARS for a in fact):
                bad(f"distractor fact too short or empty: {fact}")
                continue
            if not any(contains(texts[d], fact[0]) for d in distractors):
                bad(f"distractor fact '{fact[0]}' not found in any distractor document")
            for alt in map(norm, fact):
                if any(contains(texts[d], alt) for d in sources):
                    bad(f"distractor '{alt}' also occurs in a source document")
                if contains(reference, alt) or any(contains(r, alt) for r in required_alts):
                    bad(f"distractor '{alt}' occurs in the query, ground truth or a required fact")
        if row.get("stratum") == "contract_distractor":
            if not distractors or not row.get("distractor_facts"):
                bad("contract_distractor needs distractor documents and facts")
            if any((labels.get(d) or {}).get("document_type") == target for d in distractors):
                bad("a distractor document has the target type")
    if problems:
        raise WorkshopError(f"{path.name} failed validation ({len(problems)} problems):\n  "
                            + "\n  ".join(problems[:25]),
                            fix="fix the dataset rows; every fact must be verifiable in the corpus text")
    rows = [dict(r) for r in data["queries"] if split == "all" or r["split"] == split]
    if not rows:
        raise WorkshopError(f"no {split} questions in {path.name}")
    for r in rows:
        r["dataset_version"] = data["version"]
    return rows


# ---- variants and routing ------------------------------------------------------------------------
def route_specs(env: Environment, names: ParticipantNames) -> dict[str, AgentSpec]:
    smart = agent_specs(env, names)["smart"]
    return {route: AgentSpec(names.smart_route_agent(route), names.smart_index,
                             None if route == "any" else f"document_type eq '{route}'", smart.model)
            for route in EVAL_ROUTES}


def variant_route(variant: str, row: dict[str, Any], routes: dict[str, dict[str, Any]]) -> str | None:
    """Document type the variant filters on; None = raw index (simple), 'any' = smart index unfiltered."""
    if variant == "simple":
        return None
    if variant == "smart_nofilter":
        return "any"
    if variant == "smart_contract":
        return "contract"
    if variant == "smart_routed":
        return str(routes[row["id"]]["label"])
    if variant == "smart_oracle":
        return str(row.get("target_type") or "any")
    raise ValueError(variant)


def agent_for(variant: str, row: dict[str, Any], routes: dict[str, dict[str, Any]],
              names: ParticipantNames) -> tuple[str, str | None]:
    route = variant_route(variant, row, routes)
    return (names.simple_rag_agent if route is None else names.smart_route_agent(route)), route


def route_question(client: DecisionClient, question: str) -> dict[str, Any]:
    """Router sees only the question. Low-confidence choices fall back to the unfiltered agent."""
    result = client.classify(f"Question: {question.strip()}", ROUTER)
    probability = result.probability_of(result.choice) or result.confidence or 0.0
    confident = result.choice in TYPES and probability >= ROUTER.min_probability
    return {"label": result.choice if confident else "any", "choice": result.choice,
            "probability": round(probability, 6), "probabilities": result.probabilities,
            "model": result.model, "latency_ms": result.latency_ms,
            "reason": "accepted" if confident else ("abstained" if result.choice == "any" else "low_probability")}


# ---- objective metrics -----------------------------------------------------------------------------
def score_answer(record: dict[str, Any], row: dict[str, Any], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Deterministic metrics from the answer text and resolved citations. Harm metrics are None for
    operational failures so a crashed answer can never look 'clean' on them. Unanswerable questions
    have no objective `clean_answer` (string matching cannot verify a correct abstention); they are
    scored by the judge and their distractor (hallucinated neighbour value) rate."""
    text = record.get("answer") or ""
    cited = list(dict.fromkeys(record.get("cited_doc_ids") or []))
    failure = (bool(record.get("error")) or not text.strip() or not record.get("tool_calls")
               or (bool(PLACEHOLDER.search(text.strip())) and not cited))
    facts = row.get("required_facts") or []
    found = [any(contains(text, alt) for alt in fact) for fact in facts]
    fact_recall = sum(found) / len(found) if facts else None
    distractor = (any(contains(text, alt) for fact in row["distractor_facts"] for alt in fact)
                  if row.get("distractor_facts") else None)
    target = row.get("target_type")
    off_target = ([d for d in cited if target not in _acceptable(labels, d)] if target else [])
    sources = set(row.get("source_doc_ids") or [])
    answerable = row.get("stratum") != "unanswerable"
    return {
        "operational_failure": failure,
        "fact_recall": fact_recall,
        "missing_facts": [fact[0] for fact, ok in zip(facts, found, strict=True) if not ok],
        "distractor_mention": None if failure else distractor,
        "off_target_citation": None if failure or not cited or not target else len(off_target) / len(cited),
        "off_target_doc_ids": off_target,
        "citation_recall": len(sources & set(cited)) / len(sources) if sources and answerable else None,
        "citation_precision": len(sources & set(cited)) / len(cited) if cited and sources else None,
        "clean_answer": (not failure and fact_recall == 1.0 and not distractor) if answerable else None,
    }


# ---- Foundry judge ------------------------------------------------------------------------------------
def judge_criteria(judge: str) -> list[dict[str, Any]]:
    return [
        {"type": "azure_ai_evaluator", "name": "response_completeness",
         "evaluator_name": "builtin.response_completeness",
         "initialization_parameters": {"deployment_name": judge, "threshold": 4},
         "data_mapping": {"response": "{{item.response}}", "ground_truth": "{{item.ground_truth}}"}},
        {"type": "azure_ai_evaluator", "name": "groundedness", "evaluator_name": "builtin.groundedness",
         "initialization_parameters": {"deployment_name": judge, "threshold": 4},
         "data_mapping": {"query": "{{item.query}}", "response": "{{item.response}}",
                          "context": "{{item.context}}"}},
    ]


def retrieval_criteria() -> list[dict[str, Any]]:
    return [{"type": "azure_ai_evaluator", "name": "document_retrieval",
             "evaluator_name": "builtin.document_retrieval",
             "initialization_parameters": {"ground_truth_label_min": 0, "ground_truth_label_max": 1},
             "data_mapping": {"retrieval_ground_truth": "{{item.retrieval_ground_truth}}",
                              "retrieved_documents": "{{item.retrieved_documents}}"}}]


def cited_context(cited: list[str], titles: dict[str, str]) -> str:
    """Groundedness context = full text of the documents the answer actually cited."""
    if not cited:
        return NO_CITATIONS
    return "\n\n".join(f"[{d}] {titles.get(d, d)}\n{(CORPUS_DIR / 'text' / f'{d}.txt').read_text(encoding='utf-8')}"
                       for d in cited if d in titles)


def judge_items(records: list[dict[str, Any]], rows: dict[str, dict[str, Any]],
                titles: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    items: dict[str, list[dict[str, str]]] = {v: [] for v in VARIANTS}
    for rec in sorted(records, key=lambda r: r["key"]):
        row = rows[rec["qid"]]
        items[rec["variant"]].append({
            "key": rec["key"], "qid": rec["qid"], "variant": rec["variant"], "repeat": str(rec["repeat"]),
            "split": row["split"], "stratum": row["stratum"], "query": row["query"],
            "response": (rec.get("answer") or "").strip() or EMPTY_RESPONSE,
            "ground_truth": row["ground_truth"], "context": cited_context(rec.get("cited_doc_ids") or [], titles),
        })
    return items


def _results_by_metric(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for result in item.get("results") or []:
        name = result.get("metric") or result.get("name")
        if name:
            out[str(name)] = {"score": result.get("score"), "passed": result.get("passed"),
                              "reason": result.get("reason")}
    return out


def _item_data(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("datasource_item") or {}
    return cast(dict[str, Any], data.get("item", data))


# ---- inspection / plan ----------------------------------------------------------------------------------
def _index_snapshot(search: SearchClient, index: str, *,
                    labels: bool = False) -> tuple[dict[str, list[str]], dict[str, Any]]:
    select = "doc_id,content" + (",document_type" if labels else "")
    response = search._call("POST", f"indexes/{index}/docs/search", {"search": "*", "top": 1000, "select": select})
    if response.get("@odata.nextLink"):
        raise Blocked("index snapshot exceeds 1000 chunks; paginate before comparing")
    by_doc: dict[str, list[str]] = {}
    types: dict[str, Any] = {}
    for hit in response["value"]:
        by_doc.setdefault(hit["doc_id"], []).append(" ".join(hit["content"].split()))
        if labels:
            types[hit["doc_id"]] = hit.get("document_type")
    return {d: sorted(parts) for d, parts in sorted(by_doc.items())}, types


def index_fingerprint(search: SearchClient, names: ParticipantNames) -> tuple[str, dict[str, list[str]]]:
    """Hash of both indexes' chunks (boundaries kept) plus the smart index's document_type labels,
    so re-chunking or re-classification between resumed steps is detected."""
    raw, _ = _index_snapshot(search, names.raw_index)
    smart, types = _index_snapshot(search, names.smart_index, labels=True)
    if raw != smart:
        raise Blocked("raw and smart indexes hold different chunks; re-run the smart indexer first")
    return _hash({"chunks": smart, "document_type": types}), smart


def _check_search_tool(name: str, definition: dict[str, Any], index: str, filter_: str | None) -> None:
    tools = [t for t in definition.get("tools", []) if t.get("type") == "azure_ai_search"]
    entries = [e for t in tools for e in t["azure_ai_search"]["indexes"]]
    if len(tools) != 1 or len(entries) != 1 or len(definition.get("tools", [])) != 1:
        raise Blocked(f"{name} must have exactly one Azure AI Search tool with one index")
    if entries[0].get("index_name") != index or (entries[0].get("filter") or None) != filter_:
        raise Blocked(f"{name} searches {entries[0].get('index_name')} with filter "
                      f"{entries[0].get('filter')!r}; expected {index} with {filter_!r}")


def expected_tools(env: Environment, names: ParticipantNames) -> dict[str, tuple[str, str | None]]:
    specs = agent_specs(env, names)
    out = {s.name: (s.index, s.filter) for s in specs.values()}
    out.update({s.name: (s.index, s.filter) for s in route_specs(env, names).values()})
    return out


def _comparable(definition: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(definition)
    for tool in normalized.get("tools", []):
        if tool.get("type") == "azure_ai_search":
            for entry in tool["azure_ai_search"]["indexes"]:
                entry.pop("index_name", None)
                entry.pop("filter", None)
    return normalized


def inspect(env: Environment, names: ParticipantNames, rows: list[dict[str, Any]], judge: str) -> dict[str, Any]:
    """Read-only checks: workshop agents exist, comparable settings, identical index contents, evidence live,
    distinct judge model available, evaluators available, and which route agents need creating."""
    agents = PromptAgents(env)
    project = agents.client
    existing: dict[str, dict[str, Any]] = {}
    for name in (names.simple_rag_agent, names.smart_rag_agent):
        try:
            latest = project.agents.get(name).versions.latest
        except Exception as exc:
            raise Blocked(f"{name} not found", fix="run `fomo provision` and `fomo smart` first") from exc
        existing[name] = {"version": str(latest.version), "definition": latest.definition.as_dict()}
    tools = expected_tools(env, names)
    for name, info in existing.items():
        _check_search_tool(name, info["definition"], *tools[name])
    if _comparable(existing[names.simple_rag_agent]["definition"]) != _comparable(
            existing[names.smart_rag_agent]["definition"]):
        raise Blocked("unfair comparison: Simple and Smart RAG differ beyond index/filter")
    route_agents: dict[str, dict[str, Any]] = {}
    for route, spec in route_specs(env, names).items():
        wanted = agents.definition(spec).as_dict()
        state = {"name": spec.name, "filter": spec.filter, "action": "create", "version": None}
        try:
            latest = project.agents.get(spec.name).versions.latest
            state["version"] = str(latest.version)
            state["action"] = "exists" if latest.definition.as_dict() == wanted else "update"
        except Exception:  # noqa: S110 - absent agent: planned as create
            pass
        if _comparable(wanted) != _comparable(existing[names.smart_rag_agent]["definition"]):
            raise Blocked(f"route agent {spec.name} would differ from Smart RAG beyond the filter")
        route_agents[route] = state
    search = SearchClient(env)
    fingerprint, smart = index_fingerprint(search, names)
    for row in rows:
        for doc_id, quote in row["evidence"].items():
            if norm(quote) not in norm(" ".join(smart.get(doc_id, []))):
                raise Blocked(f"{row['id']}: evidence missing from live {names.smart_index}/{doc_id}")
    catalog = {e.name for e in project.evaluators.list(type="builtin")}
    for name in [f"builtin.{m}" for m in JUDGE_METRICS] + ["builtin.document_retrieval"]:
        if name not in catalog:
            raise Blocked(f"Foundry evaluator {name} is unavailable in this project")
    deployments = {d["name"]: d for d in arm("GET", f"{env.foundry_account_id}/deployments",
                                              "2025-06-01").json()["value"]}
    judge_dep, chat_dep = deployments.get(judge), deployments.get(env.chat_deployment)
    if not judge_dep or judge_dep["properties"]["provisioningState"] != "Succeeded":
        raise Blocked(f"judge deployment '{judge}' does not exist or is not ready",
                      fix=f"deploy a stronger chat model (e.g. gpt-5.4) as '{judge}' with approval, or pass --judge")
    judge_model = judge_dep["properties"]["model"]["name"]
    if chat_dep and judge_model == chat_dep["properties"]["model"]["name"]:
        raise Blocked(f"judge '{judge}' runs {judge_model}, the same model as the agents",
                      fix="use a different, stronger judge model to avoid self-preference")
    return {"agents": existing, "route_agents": route_agents, "judge": judge, "judge_model": judge_model,
            "index_sha256": fingerprint, "index_documents": len(smart)}


def estimate_usd(calls: int) -> float | None:
    price = MODEL_USD_PER_M.get("gpt-5-mini")
    if not price:
        return None
    per = (AGENT_TOKENS_PER_QUESTION["input"] * price["input"]
           + AGENT_TOKENS_PER_QUESTION["output"] * price["output"]) / 1e6
    return round(calls * per, 2)


def build_plan(env: Environment, names: ParticipantNames, rows: list[dict[str, Any]], context: dict[str, Any],
               *, split: str, repeats: int, judge: str, done: dict[str, int] | None = None) -> Plan:
    done = done or {}
    p = Plan("eval-ladder", names.hackername, env.subscription_id, env.resource_group)
    for state in context["route_agents"].values():
        if state["action"] != "exists":
            p.add("create" if state["action"] == "create" else "update", f"prompt agent {state['name']}",
                  f"smart index, filter {state['filter'] or 'none'}; identical model/instructions/top_k to Smart RAG "
                  "(evaluation only; removed by `fomo cleanup`)")
    if not done.get("routes"):
        p.add("invoke", f"router {env.decision_deployment}", f"{len(rows)} questions (question text only)",
              cost=f"{len(rows)} decision calls, ~60 input tokens each")
    calls = len(rows) * len(VARIANTS) * repeats - done.get("responses", 0)
    if calls > 0:
        usd = estimate_usd(calls)
        p.add("invoke", "5 RAG variants", f"{len(rows)} questions x 5 variants x {repeats} repeat(s); "
              f"{calls} agent calls in seeded random order",
              cost=f"~${usd} on {env.chat_deployment} (list-price estimate)" if usd is not None else "usage-based")
    if not done.get("retrieval"):
        p.add("run", "direct Azure AI Search retrieval", f"{len(rows) * len(VARIANTS)} queries, k={K}",
              cost="query embeddings only")
    if not done.get("judge"):
        total = len(rows) * len(VARIANTS) * repeats
        p.add("upload", "Foundry evaluation datasets", "5 answer files + 5 retrieval files")
        p.add("run", f"Foundry evaluation, judge {judge}",
              f"{total} answers x {len(JUDGE_METRICS)} metrics = {total * len(JUDGE_METRICS)} judge calls",
              cost=f"usage-based on {judge}")
        p.add("run", "Foundry builtin.document_retrieval", f"up to {len(rows) * len(VARIANTS)} rows; no model calls")
    p.add("write", ".foundry/results/ladder/", "routing, answers, retrieval, judge outputs, report.md/json")
    p.notes.extend([
        f"split={split}: " + ("headline result" if split == "test" and repeats >= PROTOCOL_TEST_REPEATS else
                              f"NOT protocol-compliant: test needs {PROTOCOL_TEST_REPEATS} repeats"
                              if split == "test" else "NOT the headline result; pipeline check"
                              if split == "dev" else "dev+test combined; hypotheses use test rows only"),
        "references never reach the agents or the router; the router sees only the question",
        "transport errors (429/5xx/timeouts) retry up to 3 times for every variant; content is never retried",
        "Simple RAG and Smart RAG are not modified; route agents differ from Smart RAG only by filter",
    ])
    return p


# ---- execution -------------------------------------------------------------------------------------------------
def _read_jsonl(path: Path, *, repair: bool = False) -> list[dict[str, Any]]:
    """Read saved answers. An interrupted final append is dropped (and, with repair, truncated so the
    next append starts on a clean line); corruption anywhere else is an error."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = []
    for i, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except ValueError as exc:
            if i != len(lines) - 1:
                raise WorkshopError(f"{path} is corrupt at line {i + 1}") from exc
            if repair:
                path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return records


def _latest_versions(agents: PromptAgents, names: list[str]) -> dict[str, str]:
    return {n: str(agents.client.agents.get(n).versions.latest.version) for n in names}


def generate(agents: PromptAgents, names: ParticipantNames, rows: list[dict[str, Any]],
             routes: dict[str, dict[str, Any]], state: dict[str, Any], out: Path, *,
             workers: int, log: Callable[[str], None]) -> list[dict[str, Any]]:
    path = out / "responses.jsonl"
    done = {r["key"] for r in _read_jsonl(path, repair=True)}
    jobs = [(v, row, rep) for rep in range(1, state["repeats"] + 1) for row in rows for v in VARIANTS]
    random.Random(state["seed"]).shuffle(jobs)
    pending = [j for j in jobs if f"{j[0]}|{j[1]['id']}|{j[2]}" not in done]
    lock = threading.Lock()
    progress = Counter[str]()

    def one(job: tuple[str, dict[str, Any], int]) -> None:
        variant, row, repeat = job
        agent, route = agent_for(variant, row, routes, names)
        rec: dict[str, Any] = {"key": f"{variant}|{row['id']}|{repeat}", "qid": row["id"], "variant": variant,
                               "repeat": repeat, "agent": agent, "agent_version": state["agent_versions"][agent],
                               "route": route, "started_at": datetime.now(UTC).isoformat()}
        for attempt in range(1, 5):
            try:
                ans = agents.ask(agent, row["query"])
                rec.update(answer=ans.text, cited_doc_ids=ans.cited_doc_ids,
                           unresolved_citations=[c.title for c in ans.citations if not c.doc_id],
                           tool_calls=ans.tool_calls, input_tokens=ans.input_tokens, output_tokens=ans.output_tokens,
                           latency_ms=ans.latency_ms, response_id=ans.response_id, error=None, attempts=attempt)
                break
            except AgentError as exc:
                rec.update(answer="", cited_doc_ids=[], tool_calls=0, error=str(exc)[:600], attempts=attempt)
                if attempt == 4 or not any(m in str(exc).lower() for m in TRANSIENT):
                    break
                time.sleep(20 * attempt)
        with lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            progress["done"] += 1
            if progress["done"] % 20 == 0 or progress["done"] == len(pending):
                log(f"answers {progress['done']}/{len(pending)}")

    if pending:
        log(f"generating {len(pending)} answers ({len(done)} already saved) with {workers} workers")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, pending))
    records = _read_jsonl(path)
    keys = [r["key"] for r in records]
    if len(keys) != len(set(keys)):
        raise WorkshopError(f"duplicate answers in {path}; remove duplicates before reporting")
    return records


def retrieval(search: SearchClient, names: ParticipantNames, rows: list[dict[str, Any]],
              routes: dict[str, dict[str, Any]], labels: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Direct Search with each variant's index/filter: what the agents could see, measured without a model."""
    out = []
    for row in rows:
        relevant = row["source_doc_ids"]
        for variant in VARIANTS:
            route = variant_route(variant, row, routes)
            index = names.raw_index if route is None else names.smart_index
            filter_ = None if route in (None, "any") else f"document_type eq '{route}'"
            hits = search.query(index, row["query"], top=K, filter_=filter_, select="doc_id,title")
            docs = distinct_docs(hits, K)
            first: dict[str, float] = {}
            for hit in hits:
                first.setdefault(hit["doc_id"], float(hit.get("@search.score") or 0.0))
            target = row.get("target_type")
            out.append({
                "key": f"{variant}|{row['id']}", "qid": row["id"], "variant": variant, "filter": filter_,
                "retrieved": docs,
                "retrieved_documents": [{"document_id": d, "relevance_score": round(first[d], 6)} for d in docs],
                "p_at_5": precision_at_k(docs, relevant, K) if relevant else None,
                "r_at_5": recall_at_k(docs, relevant, K) if relevant else None,
                "ndcg_at_5": ndcg_at_k(docs, relevant, K) if relevant else None,
                "off_target_retrieved": (sum(target not in _acceptable(labels, d) for d in docs) / len(docs)
                                         if docs and target else None),
                "distractors_retrieved": sum(d in set(row.get("distractor_doc_ids") or []) for d in docs),
            })
    return out


def _submit(project: Any, client: Any, state: dict[str, Any], key: str, title: str,
            criteria: list[dict[str, Any]], schema: dict[str, Any], items: dict[str, list[dict[str, Any]]],
            out: Path, log: Callable[[str], None]) -> None:
    if (state.get(key, {}).get("runs")
            and all((state[key]["runs"].get(v) or {}).get("id") for v in VARIANTS if items.get(v))):
        return
    group = state.setdefault(key, {"runs": {}})
    if not group.get("eval_id"):
        evaluation = client.evals.create(
            name=title, data_source_config={"type": "custom", "item_schema": schema, "include_sample_schema": False},
            testing_criteria=cast(Any, criteria))
        group["eval_id"] = evaluation.id
        _save(out / "manifest.json", state)
    for variant, rows in items.items():
        if not rows or (group["runs"].get(variant) or {}).get("id"):
            continue
        file = out / "datasets" / f"{key}-{variant}.jsonl"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        name = f"{state['participant']}-ladder-{key}-{variant}"
        try:
            dataset = project.datasets.upload_file(name=name, version=state["run_id"], file_path=str(file))
            source: dict[str, Any] = {"type": "file_id", "id": dataset.id}
        except Exception as exc:  # registration can be blocked by policy; inline keeps the run reproducible
            log(f"dataset registration failed for {variant} ({str(exc)[:160]}); using inline content")
            source = {"type": "file_content", "content": [{"item": r} for r in rows]}
        run_name = f"{variant}-{state['run_id']}"
        group["runs"][variant] = {"id": None, "status": "submitting", "rows": len(rows), "name": run_name}
        _save(out / "manifest.json", state)
        existing = next((r for r in client.evals.runs.list(eval_id=group["eval_id"])
                         if getattr(r, "name", None) == run_name), None)
        run = existing or client.evals.runs.create(eval_id=group["eval_id"], name=run_name,
                                                   data_source=cast(Any, {"type": "jsonl", "source": source}))
        group["runs"][variant].update(id=run.id, status=run.status)
        _save(out / "manifest.json", state)
        log(f"Foundry {key}/{variant}: eval={group['eval_id']} run={run.id}" + (" (reconciled)" if existing else ""))


def _collect(client: Any, state: dict[str, Any], key: str, out: Path, *, timeout: float,
             log: Callable[[str], None]) -> bool:
    group = state.get(key) or {}
    deadline = time.monotonic() + timeout
    while True:
        for variant, info in group.get("runs", {}).items():
            if info.get("output_file"):
                continue
            run = client.evals.runs.retrieve(eval_id=group["eval_id"], run_id=info["id"])
            info.update(status=run.status, report_url=getattr(run, "report_url", None))
            if run.status in TERMINAL:
                items = [i.model_dump(mode="json", warnings=False) for i in client.evals.runs.output_items.list(
                    eval_id=group["eval_id"], run_id=info["id"])]
                path = out / "foundry" / f"{key}-{variant}.json"
                _save(path, {"run": run.model_dump(mode="json", warnings=False), "output_items": items})
                info["output_file"] = str(path)
                log(f"Foundry {key}/{variant}: {run.status}, {len(items)} items")
        _save(out / "manifest.json", state)
        if all(i.get("output_file") for i in group.get("runs", {}).values()):
            failed = {v: i["status"] for v, i in group.get("runs", {}).items() if i.get("status") != "completed"}
            if failed:
                log(f"Foundry {key} runs did not complete: {failed}; the report marks them incomplete")
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(30)


def _string_schema(fields: tuple[str, ...]) -> dict[str, Any]:
    props = {f: {"type": "string"} for f in fields}
    return {"type": "object", "properties": props, "required": list(fields)}


RETRIEVAL_SCHEMA = {
    "type": "object", "required": ["key", "qid", "variant", "retrieval_ground_truth", "retrieved_documents"],
    "properties": {
        "key": {"type": "string"}, "qid": {"type": "string"}, "variant": {"type": "string"},
        "retrieval_ground_truth": {"type": "array", "items": {"type": "object"}},
        "retrieved_documents": {"type": "array", "items": {"type": "object"}},
    },
}


def execute(env: Environment, names: ParticipantNames, rows: list[dict[str, Any]], context: dict[str, Any], *,
            split: str, repeats: int, judge: str, root: Path, workers: int = 4, state_path: Path | None = None,
            seed: int = 2026, timeout: float = 3600, log: Callable[[str], None] = print) -> dict[str, Any]:
    if state_path:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        out = state_path.parent
        if state["dataset_sha256"] != _hash(rows):
            raise WorkshopError("dataset changed since this run started; start a new run instead of resuming")
        if state["index_sha256"] != context["index_sha256"]:
            raise Blocked("index chunks or document_type labels changed since this run started",
                          fix="start a new run; answers from different index states are not comparable")
    else:
        run_id = (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                  + f"-{names.hackername}-{split}-{secrets.token_hex(3)}")
        out = root / "results" / "ladder" / run_id
        state = {"run_id": run_id, "started_at": datetime.now(UTC).isoformat(),
                 "project_endpoint": env.resolved_project_endpoint(), "participant": names.hackername,
                 "split": split, "repeats": repeats, "seed": seed, "judge": judge,
                 "judge_model": context["judge_model"], "agent_model": env.chat_deployment,
                 "router": {"deployment": env.decision_deployment, "config": ROUTER.to_question(),
                            "min_probability": ROUTER.min_probability, "version": ROUTER.version},
                 "dataset_version": rows[0]["dataset_version"], "dataset_sha256": _hash(rows),
                 "question_ids": [r["id"] for r in rows], "index_sha256": context["index_sha256"]}
        _save(out / "manifest.json", state)
    if state["project_endpoint"] != env.resolved_project_endpoint():
        raise WorkshopError("this run belongs to a different Foundry project")
    lock = out / "run.lock"
    try:
        lock.open("x").close()
    except FileExistsError as exc:
        raise Blocked(f"another eval-ladder invocation holds {lock}",
                      fix="wait for it to finish; delete the lock file only if that process is gone") from exc
    try:
        return _execute(env, names, rows, state, out, judge=judge, workers=workers, timeout=timeout, log=log)
    finally:
        lock.unlink(missing_ok=True)


def _execute(env: Environment, names: ParticipantNames, rows: list[dict[str, Any]], state: dict[str, Any],
             out: Path, *, judge: str, workers: int, timeout: float, log: Callable[[str], None]) -> dict[str, Any]:
    from azure.ai.projects import AIProjectClient

    agents = PromptAgents(env)
    if "agent_versions" not in state:
        for route, spec in route_specs(env, names).items():
            version = agents.ensure(spec)
            log(f"route agent {spec.name} v{version} ({route})")
        used = [names.simple_rag_agent, names.smart_rag_agent, *names.eval_route_agents]
        definitions = {n: agents.client.agents.get(n).versions.latest.definition.as_dict() for n in used}
        tools = expected_tools(env, names)
        for name, definition in definitions.items():
            _check_search_tool(name, definition, *tools[name])
        reference = _comparable(definitions[names.smart_rag_agent])
        if any(_comparable(d) != reference for d in definitions.values()):
            raise Blocked("variant agents differ beyond index/filter; refusing an unfair comparison")
        state["agent_versions"] = _latest_versions(agents, used)
        _save(out / "manifest.json", state)
    if "routes" not in state:
        decision = DecisionClient(env.resolved_decision_endpoint(), env.decision_deployment)
        state["routes"] = {r["id"]: route_question(decision, r["query"]) for r in rows}
        _save(out / "manifest.json", state)
        log(f"routed {len(rows)} questions: {dict(Counter(r['label'] for r in state['routes'].values()))}")
    routes = state["routes"]
    records = generate(agents, names, rows, routes, state, out, workers=workers, log=log)
    after = _latest_versions(agents, list(state["agent_versions"]))
    if after != state["agent_versions"]:
        raise Blocked(f"agent versions changed during the run: {state['agent_versions']} -> {after}",
                      fix="start a new run; answers from different versions are not comparable")
    search = SearchClient(env)
    if index_fingerprint(search, names)[0] != state["index_sha256"]:
        raise Blocked("index chunks or document_type labels changed during answer generation",
                      fix="start a new run once the indexes are stable")
    labels = load_ground_truth()
    retrieval_path = out / "retrieval.json"
    if not retrieval_path.exists():
        _save(retrieval_path, retrieval(search, names, rows, routes, labels))
    retrieved = json.loads(retrieval_path.read_text(encoding="utf-8"))
    _, docs = load_manifest()
    titles = {d.doc_id: d.title for d in docs}
    by_id = {r["id"]: r for r in rows}
    with (AIProjectClient(endpoint=env.resolved_project_endpoint(), credential=credential()) as project,
          project.get_openai_client() as client):
        _submit(project, client, state, "judge_eval", f"FOMO {names.hackername} RAG ladder {state['run_id']}",
                judge_criteria(judge), _string_schema(("key", "qid", "variant", "repeat", "split", "stratum", "query",
                                                       "response", "ground_truth", "context")),
                cast(dict[str, list[dict[str, Any]]], judge_items(records, by_id, titles)), out, log)
        retrieval_items: dict[str, list[dict[str, Any]]] = {v: [] for v in VARIANTS}
        for r in retrieved:
            sources = by_id[r["qid"]]["source_doc_ids"]
            if sources and r["retrieved_documents"]:
                retrieval_items[r["variant"]].append({
                    "key": r["key"], "qid": r["qid"], "variant": r["variant"],
                    "retrieval_ground_truth": [{"document_id": d, "query_relevance_label": 1} for d in sources],
                    "retrieved_documents": r["retrieved_documents"]})
        try:
            _submit(project, client, state, "retrieval_eval",
                    f"FOMO {names.hackername} retrieval {state['run_id']}",
                    retrieval_criteria(), RETRIEVAL_SCHEMA, retrieval_items, out, log)
        except Exception as exc:  # optional: local P@5/R@5/nDCG remain authoritative
            state["retrieval_error"] = str(exc)[:600]
            log(f"Foundry document_retrieval submission failed: {str(exc)[:200]}")
        _save(out / "manifest.json", state)
        complete = _collect(client, state, "judge_eval", out, timeout=timeout, log=log)
        if state.get("retrieval_eval", {}).get("runs"):
            _collect(client, state, "retrieval_eval", out, timeout=max(60.0, timeout / 4), log=log)
    if not complete:
        raise Blocked(f"Foundry judging still running; resume with `fomo eval-ladder --resume "
                      f"{out / 'manifest.json'} --apply`")
    return finalize(out)


# ---- report -------------------------------------------------------------------------------------------------------
def _foundry_scores(out: Path, state: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    for info in (state.get(key) or {}).get("runs", {}).values():
        if not info.get("output_file"):
            continue
        payload = json.loads(Path(info["output_file"]).read_text(encoding="utf-8"))
        for item in payload["output_items"]:
            data = _item_data(item)
            if data.get("key"):
                scores[str(data["key"])] = _results_by_metric(item)
    return scores


def _mean(values: list[Any]) -> float | None:
    numeric = [float(v) for v in values if isinstance(v, int | float)]
    return round(fmean(numeric), 4) if numeric else None


ANSWER_METRICS = ("clean_answer", "fact_recall", "distractor_mention", "off_target_citation", "citation_recall",
                  "operational_failure", "response_completeness", "groundedness")
RETRIEVAL_METRICS = ("p_at_5", "r_at_5", "ndcg_at_5", "off_target_retrieved", "foundry_fidelity")

# (hypothesis, baseline, variant, stratum or None for all, metric)
PRE_REGISTERED = (
    ("H1", "simple", "smart_contract", "contract_distractor", PRIMARY),
    ("H1", "simple", "smart_routed", "contract_distractor", PRIMARY),
    ("H2", "simple", "smart_contract", "other_type", "fact_recall"),
    ("H2", "simple", "smart_routed", "other_type", "fact_recall"),
    ("H3", "simple", "smart_nofilter", None, PRIMARY),
    ("H4", "smart_routed", "smart_oracle", None, PRIMARY),
    ("H5", "simple", "smart_contract", "contract_distractor", "off_target_citation"),
    ("H5", "simple", "smart_routed", "contract_distractor", "off_target_citation"),
    ("bottom line", "simple", "smart_routed", None, PRIMARY),
)


def _slice(rows: list[dict[str, Any]], stratum: str | None) -> list[dict[str, Any]]:
    return [r for r in rows if stratum is None or r["stratum"] == stratum]


def _paired(rows: list[dict[str, Any]], baseline: str, variant: str, stratum: str | None,
            metric: str) -> stats.Paired:
    chosen = _slice(rows, stratum)
    base = stats.per_question_means([r for r in chosen if r["variant"] == baseline], metric)
    other = stats.per_question_means([r for r in chosen if r["variant"] == variant], metric)
    return stats.compare(metric, baseline, variant, base, other, higher_is_better=metric not in LOWER_IS_BETTER,
                         tie_margin=TIE_MARGIN.get(metric, 0.0))


def hypotheses(comparisons: dict[str, stats.Paired]) -> dict[str, dict[str, Any]]:
    def c(h: str, variant: str) -> stats.Paired:
        return comparisons[f"{h}:{variant}"]

    out: dict[str, dict[str, Any]] = {}
    h1 = {v: c("H1", v).verdict for v in ("smart_contract", "smart_routed")}
    out["H1"] = {"statement": "On contract questions with competing wrong-type documents, metadata filtering "
                              "raises the clean-answer rate over Simple RAG.",
                 "verdict": ("supported" if h1["smart_routed"] == "better" else "contradicted"
                             if h1["smart_routed"] == "worse" else "inconclusive"), "detail": h1}
    fixed, routed = c("H2", "smart_contract"), c("H2", "smart_routed")
    fixed_loses = fixed.ci_high is not None and fixed.ci_high < 0
    fixed_gains = fixed.ci_low is not None and fixed.ci_low > 0
    noninferior = routed.ci_low is not None and routed.ci_low >= -EQUIVALENCE_MARGIN
    inferior = routed.ci_high is not None and routed.ci_high < -EQUIVALENCE_MARGIN
    out["H2"] = {"statement": "On non-contract questions the fixed contract filter loses required facts; "
                              f"routing does not (non-inferiority margin {EQUIVALENCE_MARGIN}).",
                 "verdict": ("supported" if fixed_loses and noninferior else
                             "contradicted" if inferior or fixed_gains else
                             "partially supported" if fixed_loses or noninferior else "inconclusive"),
                 "detail": {"fixed_filter_loses": fixed_loses, "routed_noninferior": noninferior,
                            "routed_inferior": inferior}}
    h3 = c("H3", "smart_nofilter")
    equivalent = (h3.ci_low is not None and h3.ci_high is not None
                  and h3.ci_low >= -EQUIVALENCE_MARGIN and h3.ci_high <= EQUIVALENCE_MARGIN)
    outside = h3.ci_low is not None and h3.ci_high is not None and (
        h3.ci_low > EQUIVALENCE_MARGIN or h3.ci_high < -EQUIVALENCE_MARGIN)
    out["H3"] = {"statement": f"Re-indexing alone (smart index, no filter) is equivalent to Simple RAG "
                              f"(margin {EQUIVALENCE_MARGIN}).",
                 "verdict": "supported" if equivalent else "contradicted" if outside else "inconclusive"}
    h4 = c("H4", "smart_oracle")
    out["H4"] = {"statement": "The oracle filter bounds routing from above; the gap is router error.",
                 "verdict": ("supported" if h4.ci_low is not None and h4.ci_low >= -EQUIVALENCE_MARGIN
                             else "contradicted" if h4.ci_high is not None and h4.ci_high < -EQUIVALENCE_MARGIN
                             else "inconclusive"),
                 "gap": h4.diff}
    h5 = {v: c("H5", v) for v in ("smart_contract", "smart_routed")}
    routed5 = h5["smart_routed"]
    out["H5"] = {"statement": "Amendment 1 (registered after the dev check, before any test answers): on "
                              "contract_distractor, metadata filtering lowers the share of wrong-type citations.",
                 "verdict": ("supported" if routed5.ci_high is not None and routed5.ci_high < 0 else
                             "contradicted" if routed5.ci_low is not None and routed5.ci_low > 0 else "inconclusive"),
                 "detail": {v: p.verdict for v, p in h5.items()}}
    return out


def build_report(state: dict[str, Any], rows: list[dict[str, Any]], records: list[dict[str, Any]],
                 retrieved: list[dict[str, Any]], judged: dict[str, dict[str, Any]],
                 retrieval_scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    labels = load_ground_truth()
    by_id = {r["id"]: r for r in rows}
    flat: list[dict[str, Any]] = []
    for rec in records:
        row = by_id[rec["qid"]]
        scores = judged.get(rec["key"], {})
        flat.append({"key": rec["key"], "qid": rec["qid"], "variant": rec["variant"], "repeat": rec["repeat"],
                     "stratum": row["stratum"], "split": row["split"], "route": rec.get("route"),
                     **score_answer(rec, row, labels),
                     **{m: (scores.get(m) or {}).get("score") for m in JUDGE_METRICS}})
    for r in retrieved:
        r["stratum"] = by_id[r["qid"]]["stratum"]
        result = retrieval_scores.get(r["key"], {})
        r["foundry_fidelity"] = (result.get("fidelity") or {}).get("score")
    slices: dict[str, Any] = {}
    for stratum in (None, *STRATA):
        name = stratum or "all"
        chosen = _slice(flat, stratum)
        chosen_ret = _slice(retrieved, stratum)
        slices[name] = {
            "questions": len({r["qid"] for r in chosen}),
            "answers": {v: {m: _mean([r[m] for r in chosen if r["variant"] == v]) for m in ANSWER_METRICS}
                        for v in VARIANTS},
            "judged": {v: {m: sum(isinstance(r[m], int | float) for r in chosen if r["variant"] == v)
                           for m in JUDGE_METRICS} for v in VARIANTS},
            "rows": {v: sum(r["variant"] == v for r in chosen) for v in VARIANTS},
            "retrieval": {v: {m: _mean([r.get(m) for r in chosen_ret if r["variant"] == v])
                              for m in RETRIEVAL_METRICS} for v in VARIANTS},
        }
    hypothesis_split = "test" if any(r["split"] == "test" for r in flat) else "dev"
    tested = [r for r in flat if r["split"] == hypothesis_split]
    comparisons = {(f"{h}:{variant}" if h != "bottom line" else h): _paired(tested, base, variant, stratum, m)
                   for h, base, variant, stratum, m in PRE_REGISTERED}
    strata_of = {(f"{h}:{variant}" if h != "bottom line" else h): stratum or "all"
                 for h, base, variant, stratum, m in PRE_REGISTERED}
    descriptive = [_paired(flat, "simple", v, s, m).to_dict() | {"stratum": s or "all"}
                   for s in (None, *STRATA) for v in VARIANTS[1:]
                   for m in ("clean_answer", "fact_recall", "response_completeness")]
    routable = [r for r in rows if r.get("target_type")]
    routes = state["routes"]
    router = {
        "accuracy": round(sum(routes[r["id"]]["label"] == r["target_type"] for r in routable) / len(routable), 4)
        if routable else None,
        "fallback_any": sum(v["label"] == "any" for k, v in routes.items() if k in by_id),
        "confusion": {t: dict(Counter(routes[r["id"]]["label"] for r in routable if r["target_type"] == t))
                      for t in TYPES if any(r["target_type"] == t for r in routable)},
    }
    expected = len(rows) * state["repeats"]
    expected_keys = {f"{v}|{r['id']}|{n}" for v in VARIANTS for r in rows for n in range(1, state["repeats"] + 1)}
    answered = {r["key"] for r in flat}
    unjudged = sorted(k for k in expected_keys & answered
                      if not all(isinstance((judged.get(k, {}).get(m) or {}).get("score"), int | float)
                                 for m in JUDGE_METRICS))
    runs = (state.get("judge_eval") or {}).get("runs", {})
    failed_runs = {v: i.get("status") for v, i in runs.items() if i.get("status") != "completed"}
    integrity = {"missing_answers": sorted(expected_keys - answered),
                 "unexpected_answers": sorted(answered - expected_keys),
                 "unjudged": unjudged, "failed_judge_runs": failed_runs}
    complete = not (integrity["missing_answers"] or integrity["unexpected_answers"] or failed_runs or not runs)
    return {
        "complete": complete, "integrity": integrity, "hypothesis_split": hypothesis_split,
        "protocol_compliant": state["repeats"] >= PROTOCOL_TEST_REPEATS or state["split"] == "dev",
        "run_id": state["run_id"], "participant": state["participant"], "split": state["split"],
        "repeats": state["repeats"], "seed": state["seed"], "judge": state["judge"],
        "judge_model": state["judge_model"], "agent_model": state["agent_model"],
        "agent_versions": state["agent_versions"], "dataset_version": state["dataset_version"],
        "dataset_sha256": state["dataset_sha256"], "router": router, "slices": slices,
        "pre_registered": {k: v.to_dict() | {"stratum": strata_of[k]} for k, v in comparisons.items()},
        "hypotheses": hypotheses(comparisons), "descriptive": descriptive,
        "stability": {v: {m: stats.repeat_spread([r for r in flat if r["variant"] == v], m)
                          for m in ("clean_answer", "response_completeness")} for v in VARIANTS},
        "completeness": {v: {"answers": sum(r["variant"] == v for r in flat), "expected": expected,
                             **{m: sum(isinstance(r[m], int | float) for r in flat if r["variant"] == v)
                                for m in JUDGE_METRICS}} for v in VARIANTS},
        "foundry": {k: {"eval_id": (state.get(f"{k}_eval") or {}).get("eval_id"),
                        "runs": {v: {"id": i.get("id"), "status": i.get("status"), "report_url": i.get("report_url")}
                                 for v, i in (state.get(f"{k}_eval") or {}).get("runs", {}).items()}}
                    for k in ("judge", "retrieval")},
        "retrieval_error": state.get("retrieval_error"),
        "rows": flat,
        "caveats": [
            "Unit of analysis is the question; repeats are averaged before paired bootstrap CIs (5000 resamples).",
            f"Hypotheses use only the {hypothesis_split} split; other splits are shown descriptively.",
            "Unanswerable questions have no objective clean-answer score; judge completeness covers them.",
            "Judge metrics average only judged answers; check the reliability table for coverage gaps.",
            "Only the pre-registered contrasts test hypotheses; the descriptive table is exploratory and not "
            "corrected for multiple comparisons.",
            "Fact and distractor checks are string matches with listed paraphrases: a correct paraphrase not on "
            "the list counts as missing. Inspect missing_facts before concluding.",
            "Distractor mention counts any occurrence, including an answer that cites a wrong value to reject it.",
            "Groundedness is judged against the full text of the documents each answer cited.",
            "The routed variant is orchestrated by this harness (router + per-type agents), not one deployed agent.",
            "Synthetic 25-document corpus: effect sizes describe this corpus, not production collections.",
        ],
    }


def _fmt(value: Any, pct: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:.0%}" if pct else f"{value:.2f}"


def render(report: dict[str, Any]) -> str:
    pct = {"clean_answer", "fact_recall", "distractor_mention", "off_target_citation", "citation_recall",
           "operational_failure", "p_at_5", "r_at_5", "off_target_retrieved"}
    split = report["split"]
    protocol = report.get("protocol_compliant", True)
    integrity = report.get("integrity") or {}
    lines = [f"# RAG ladder: {report['participant']} ({split} split, {report['repeats']} repeat(s))", "",
             ("**Headline result (held-out test split).**" if split == "test" and protocol else
              "**Test split but NOT protocol-compliant (fewer than 3 repeats): not the headline result.**"
              if split == "test" else f"**{split} split: pipeline check, not the headline result.**"),
             *([] if report.get("complete", True) else [
                 f"**INCOMPLETE:** missing answers {len(integrity.get('missing_answers', []))}, "
                 f"unexpected answers {len(integrity.get('unexpected_answers', []))}, failed judge runs "
                 f"{integrity.get('failed_judge_runs')}, unjudged answers {len(integrity.get('unjudged', []))}."]),
             f"Agents `{report['agent_model']}`; judge `{report['judge']}` ({report['judge_model']}); router "
             f"accuracy {_fmt(report['router']['accuracy'], True)} vs true target type "
             f"({report['router']['fallback_any']} fell back to unfiltered); dataset {report['dataset_version']}, "
             f"seed {report['seed']}; hypotheses on the {report.get('hypothesis_split', split)} split.", "",
             "## Hypotheses (pre-registered)", ""]
    for name, h in report["hypotheses"].items():
        lines.append(f"- **{name}: {h['verdict']}** - {h['statement']}")
    lines += ["", "## Pre-registered contrasts", "",
              "| Contrast | Slice | Metric | Baseline | Variant | Diff [95% CI] | W/T/L | Verdict |",
              "|---|---|---|---:|---:|---|---|---|"]
    for key, c in report["pre_registered"].items():
        p = c["metric"] in pct
        lines.append(f"| {key} ({c['variant']} vs {c['baseline']}) | {c['stratum']} | {c['metric']} | "
                     f"{_fmt(c['baseline_mean'], p)} | {_fmt(c['variant_mean'], p)} | "
                     f"{_fmt(c['diff'])} [{_fmt(c['ci_low'])}, {_fmt(c['ci_high'])}] | "
                     f"{c['wins']}/{c['ties']}/{c['losses']} | {c['verdict']} |")
    for name, s in report["slices"].items():
        lines += ["", f"## {name} ({s['questions']} questions)", "",
                  "| Variant | Clean | Facts | Distractor | Off-target cit. | Cit. recall | Failures | "
                  "Completeness | Groundedness | P@5 | R@5 | nDCG@5 | Off-target retr. |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for v in VARIANTS:
            a, r = s["answers"][v], s["retrieval"][v]
            lines.append(f"| {v} | " + " | ".join(_fmt(a[m], m in pct) for m in ANSWER_METRICS) + " | "
                         + " | ".join(_fmt(r[m], m in pct) for m in ("p_at_5", "r_at_5", "ndcg_at_5",
                                                                     "off_target_retrieved")) + " |")
    lines += ["", "## Reliability", "", "| Variant | Answers | Judged completeness | Judged groundedness | "
              "Repeat spread (clean) | Repeat spread (completeness) |", "|---|---:|---:|---:|---:|---:|"]
    for v in VARIANTS:
        c, st = report["completeness"][v], report["stability"][v]
        lines.append(f"| {v} | {c['answers']}/{c['expected']} | {c['response_completeness']}/{c['answers']} | "
                     f"{c['groundedness']}/{c['answers']} | {_fmt(st['clean_answer'])} | "
                     f"{_fmt(st['response_completeness'])} |")
    lines += ["", "## Router confusion (true type -> routed label)", ""]
    lines += [f"- {t}: {dict(c)}" for t, c in report["router"]["confusion"].items()]
    runs = report["foundry"]["judge"]["runs"]
    if runs:
        lines += ["", "## Foundry runs", ""] + [f"- {v}: [{i['id']}]({i['report_url']}) ({i['status']})"
                                               for v, i in runs.items()]
    if report.get("retrieval_error"):
        lines += ["", f"Foundry document_retrieval unavailable: {report['retrieval_error'][:300]}"]
    lines += ["", "## Caveats", "", *[f"- {c}" for c in report["caveats"]], ""]
    return "\n".join(lines)


def finalize(out: Path) -> dict[str, Any]:
    """Rebuild report.json/report.md from saved files only (no Azure calls)."""
    state = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    all_rows = {r["id"]: r for r in load_dataset(split="all")}
    rows = [all_rows[q] for q in state["question_ids"]]
    if _hash(rows) != state["dataset_sha256"]:
        raise WorkshopError("dataset changed since this run; the saved answers no longer match their references")
    records = _read_jsonl(out / "responses.jsonl")
    retrieved = json.loads((out / "retrieval.json").read_text(encoding="utf-8"))
    report = build_report(state, rows, records, retrieved, _foundry_scores(out, state, "judge_eval"),
                          _foundry_scores(out, state, "retrieval_eval"))
    _save(out / "report.json", report)
    (out / "report.md").write_text(render(report), encoding="utf-8")
    report["paths"] = {"json": str(out / "report.json"), "markdown": str(out / "report.md"),
                       "manifest": str(out / "manifest.json")}
    return report


__all__ = ["DATASET", "VARIANTS", "build_plan", "execute", "finalize", "inspect", "load_dataset", "render",
           "route_question", "score_answer"]
