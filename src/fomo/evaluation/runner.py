"""Two-index evaluation: direct Search top-k, classification accuracy and agent side-by-side.

Every measured run records the exact index, query type, filter and scoring profile used.
Nothing here decides a winner: numbers are computed against corpus/ground_truth only.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..agents.prompt import CONTRACT_FILTER, QUERY_TYPE, TOP_K, AgentAnswer, PromptAgents, agent_specs
from ..capacity import MODEL_USD_PER_M
from ..config import Environment
from ..data.corpus import load_eval_queries, load_ground_truth
from ..naming import ParticipantNames
from ..search.client import SearchClient
from ..search.schema import SCORING_PROFILE
from .metrics import (
    classification_scores,
    distinct_docs,
    false_exclusions,
    labels_in_filter,
    mean,
    precision_at_k,
    recall_at_k,
)

CHUNKS_PER_QUERY = 20  # chunk hits fetched before de-duplicating to k distinct documents


@dataclass
class RunSpec:
    name: str
    index: str
    filter: str | None
    scoring_profile: str | None = None
    scoring_parameters: list[str] | None = None
    query_type: str = "hybrid (BM25 + vector, RRF) = Foundry vector_simple_hybrid"


@dataclass
class QueryResult:
    query_id: str
    run: str
    index: str
    filter: str | None
    scoring_profile: str | None
    top_docs: list[dict[str, Any]]
    precision: float
    recall: float
    false_exclusions: list[str] = field(default_factory=list)


def run_specs(names: ParticipantNames, query: dict[str, Any]) -> list[RunSpec]:
    desired = query.get("desired_filter")
    boost_label = (labels_in_filter(desired) or {"contract"}).pop() if desired else "contract"
    return [
        RunSpec("raw", names.raw_index, None),
        RunSpec("smart_nofilter", names.smart_index, None),
        RunSpec("smart_contract", names.smart_index, CONTRACT_FILTER),
        RunSpec("smart_desired", names.smart_index, desired),
        RunSpec("smart_boost", names.smart_index, None, SCORING_PROFILE, [f"types-{boost_label}"]),
    ]


def predicted_labels(search: SearchClient, names: ParticipantNames) -> dict[str, str]:
    """document_type per doc as the SMART INDEX sees it (what filters actually act on)."""
    data = search._call("POST", f"indexes/{names.smart_index}/docs/search",
                        {"search": "*", "top": 1000, "select": "doc_id,document_type"})
    out: dict[str, str] = {}
    for row in data.get("value", []):
        if row.get("doc_id") and row.get("document_type"):
            out[row["doc_id"]] = row["document_type"]
    return out


def retrieval(search: SearchClient, names: ParticipantNames, queries: list[dict[str, Any]],
              predicted: dict[str, str], k: int) -> list[QueryResult]:
    results: list[QueryResult] = []
    for q in queries:
        relevant = q["relevant_doc_ids"]
        for spec in run_specs(names, q):
            hits = search.query(spec.index, q["question"], top=CHUNKS_PER_QUERY, filter_=spec.filter,
                                scoring_profile=spec.scoring_profile,
                                scoring_parameters=spec.scoring_parameters,
                                select="doc_id,title" + (",document_type" if spec.index == names.smart_index
                                                         else ""))
            docs = distinct_docs(hits, k)
            first: dict[str, dict[str, Any]] = {}
            for h in hits:
                first.setdefault(h["doc_id"], h)
            top = [{"doc_id": d, "title": first[d].get("title"), "score": round(first[d].get("@search.score", 0), 5),
                    "label": predicted.get(d), "relevant": d in relevant} for d in docs]
            allowed = labels_in_filter(spec.filter)
            excluded = false_exclusions(relevant, predicted, allowed) if allowed is not None else []
            results.append(QueryResult(q["id"], spec.name, spec.index, spec.filter, spec.scoring_profile, top,
                                       precision_at_k(docs, relevant, k), recall_at_k(docs, relevant, k),
                                       excluded))
    return results


def _answer_dict(ans: AgentAnswer, relevant: list[str]) -> dict[str, Any]:
    cited = ans.cited_doc_ids
    chat = MODEL_USD_PER_M.get("gpt-5-mini")
    usd = None
    if chat and ans.input_tokens is not None and ans.output_tokens is not None:
        usd = round((ans.input_tokens * chat["input"] + ans.output_tokens * chat["output"]) / 1e6, 5)
    return {
        "agent": ans.agent,
        "answer": ans.text,
        "cited_doc_ids": cited,
        "cited_relevant": [d for d in cited if d in relevant],
        "cited_not_relevant": [d for d in cited if d not in relevant],
        "unresolved_citations": [c.title for c in ans.citations if not c.doc_id],
        "citation_precision": round(len([d for d in cited if d in relevant]) / len(cited), 3) if cited else None,
        "tool_calls": ans.tool_calls,
        "filters": ans.filters,
        "retrieved_doc_ids": ans.retrieved,
        "input_tokens": ans.input_tokens,
        "output_tokens": ans.output_tokens,
        "latency_ms": ans.latency_ms,
        "est_usd": usd,
        "response_id": ans.response_id,
    }


def agent_side_by_side(env: Environment, names: ParticipantNames, queries: list[dict[str, Any]],
                       *, workers: int = 4) -> list[dict[str, Any]]:
    agents = PromptAgents(env)
    specs = agent_specs(env, names)
    jobs = [(q, key) for q in queries for key in ("simple", "smart")]

    def ask(job: tuple[dict[str, Any], str]) -> dict[str, Any]:
        q, key = job
        try:
            ans = agents.ask(specs[key].name, q["question"])
            return {"query_id": q["id"], "role": key, **_answer_dict(ans, q["relevant_doc_ids"])}
        except Exception as exc:
            return {"query_id": q["id"], "role": key, "agent": specs[key].name, "error": str(exc)[:500]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(ask, jobs))


def summarize(results: list[QueryResult]) -> dict[str, dict[str, float]]:
    by_run: dict[str, list[QueryResult]] = {}
    for r in results:
        by_run.setdefault(r.run, []).append(r)
    return {run: {"mean_precision": mean(x.precision for x in rs), "mean_recall": mean(x.recall for x in rs),
                  "false_exclusions": sum(len(x.false_exclusions) for x in rs)} for run, rs in by_run.items()}


def evaluate(env: Environment, names: ParticipantNames, *, agents: str = "canonical", k: int | None = None,
             query_ids: list[str] | None = None, log: Any = None) -> dict[str, Any]:
    log = log or (lambda msg, **kw: None)
    spec = load_eval_queries_full()
    k = k or int(spec.get("k", TOP_K))
    queries = spec["queries"]
    if query_ids:
        queries = [q for q in queries if q["id"] in set(query_ids)]
    search = SearchClient(env)
    truth = load_ground_truth()
    predicted = predicted_labels(search, names)
    log(f"smart index labels for {len(predicted)}/{len(truth)} docs")
    classification = classification_scores(truth, predicted)
    log(f"classification accuracy {classification['accuracy']:.0%} "
        f"({len(classification['errors'])} wrong, {classification['abstained']} needs_review)")
    results = retrieval(search, names, queries, predicted, k)
    summary = summarize(results)
    for run, s in summary.items():
        log(f"{run:15} P@{k}={s['mean_precision']:.2f} R@{k}={s['mean_recall']:.2f} "
            f"false-exclusions={s['false_exclusions']}")
    agent_rows: list[dict[str, Any]] = []
    if agents != "none":
        chosen = [q for q in queries if q.get("canonical")] if agents == "canonical" else queries
        log(f"asking both prompt agents {len(chosen)} question(s)")
        agent_rows = agent_side_by_side(env, names, chosen)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "participant": names.hackername,
        "corpus_version": spec.get("corpus_version"),
        "k": k,
        "settings": {
            "query_type": "hybrid BM25 + vector (RRF); Foundry agents use " + QUERY_TYPE,
            "chunks_fetched": CHUNKS_PER_QUERY,
            "agent_top_k": TOP_K,
            "indexes": {"raw": names.raw_index, "smart": names.smart_index},
            "agent_filter": {"simple": None, "smart": "document_type chosen per question from " + ", ".join(
                sorted({v for v in predicted.values() if v not in ("needs_review", "unclassified", "cannot_tell")}))},
            "scoring_profile": SCORING_PROFILE,
            "chat_deployment": env.chat_deployment,
            "embedding_deployment": env.embedding_deployment,
        },
        "classification": classification,
        "predicted": predicted,
        "summary": summary,
        "queries": [{"id": q["id"], "question": q["question"], "relevant": q["relevant_doc_ids"],
                     "desired_filter": q.get("desired_filter"), "expect": q.get("expect")} for q in queries],
        "retrieval": [asdict(r) for r in results],
        "agents": agent_rows,
    }


def load_eval_queries_full() -> dict[str, Any]:
    from ..data.corpus import EVAL_QUERIES

    data: dict[str, Any] = json.loads(Path(EVAL_QUERIES).read_text(encoding="utf-8"))
    if not data.get("queries"):
        data["queries"] = load_eval_queries()
    return data
