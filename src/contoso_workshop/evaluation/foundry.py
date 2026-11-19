"""Reproducible, version-pinned Simple/Smart RAG evaluations in the Foundry project."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import HttpResponseError

from ..agents.prompt import resolve_doc_id
from ..azclient import arm, credential
from ..config import Environment
from ..data.corpus import CORPUS_DIR, load_manifest
from ..errors import Blocked, WorkshopError
from ..naming import ParticipantNames
from ..plan import Plan
from ..search.client import SearchClient

DATASET = CORPUS_DIR / "rag_eval.json"
METRICS = ("relevance", "groundedness", "similarity", "response_completeness")
TERMINAL = {"completed", "failed", "canceled", "cancelled"}


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def load_dataset(path: Path = DATASET, *, suite: str = "smoke") -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    corpus_version, docs = load_manifest()
    if data["corpus_version"] != corpus_version:
        raise WorkshopError("RAG evaluation references a different corpus version")
    titles = {d.doc_id: d.title for d in docs}
    rows = []
    seen: set[str] = set()
    for query in data["queries"]:
        if query["id"] in seen:
            raise WorkshopError(f"duplicate evaluation query id: {query['id']}")
        seen.add(query["id"])
        if suite == "smoke" and not query.get("smoke"):
            continue
        contexts = []
        if not query["ground_truth"] or not query["expected_behavior"]:
            raise WorkshopError(f"missing reference answer/rubric: {query['id']}")
        for doc_id in query["source_doc_ids"]:
            if doc_id not in titles:
                raise WorkshopError(f"unknown reference document: {doc_id}")
            text = (CORPUS_DIR / "text" / f"{doc_id}.txt").read_text(encoding="utf-8")
            quote = query["evidence"].get(doc_id)
            if not quote or _normalized(quote) not in _normalized(text):
                raise WorkshopError(f"reference evidence is absent from {doc_id}: {query['id']}")
            contexts.append(f"[{doc_id}] {titles[doc_id]}\n{text}")
        rows.append({**query, "reference_context": "\n\n".join(contexts), "corpus_version": corpus_version,
                     "dataset_version": data["version"]})
    if not rows:
        raise WorkshopError("evaluation dataset is empty")
    return rows


def criteria(judge: str) -> list[dict[str, Any]]:
    result = []
    for metric in METRICS:
        mapping = {"query": "{{item.query}}", "response": "{{sample.output_text}}"}
        if metric == "groundedness":
            mapping["context"] = "{{item.reference_context}}"
        if metric in ("similarity", "response_completeness"):
            mapping["ground_truth"] = "{{item.ground_truth}}"
        result.append({
            "type": "azure_ai_evaluator", "name": metric, "evaluator_name": f"builtin.{metric}",
            "initialization_parameters": {"deployment_name": judge, "threshold": 4},
            "data_mapping": mapping,
        })
    return result


def target_source(agent: dict[str, Any], rows: list[dict[str, Any]], dataset_uri: str | None) -> dict[str, Any]:
    return {
        "type": "azure_ai_target_completions",
        "source": ({"type": "file_id", "id": dataset_uri} if dataset_uri else {
            "type": "file_content", "content": [{"item": row} for row in rows],
        }),
        "input_messages": {"type": "template", "template": [
            {"type": "message", "role": "user", "content": {"type": "input_text", "text": "{{item.query}}"}},
        ]},
        "target": {"type": "azure_ai_agent", "name": agent["name"], "version": agent["version"]},
    }


def inspect_targets(project: Any, env: Environment, names: ParticipantNames, rows: list[dict[str, Any]],
                    judge: str) -> dict[str, Any]:
    agents = {}
    comparable = []
    search = SearchClient(env)
    contents: dict[str, list[tuple[str, str]]] = {}
    for role, name in (("simple", names.simple_rag_agent), ("smart", names.smart_rag_agent)):
        latest = project.agents.get(name).versions.latest
        definition = latest.definition.as_dict()
        if definition.get("kind") != "prompt":
            raise Blocked(f"{name} is not a prompt RAG agent")
        indexes = [index for tool in definition.get("tools", []) if tool.get("type") == "azure_ai_search"
                   for index in tool["azure_ai_search"]["indexes"]]
        if len(indexes) != 1:
            raise Blocked(f"{name} must reference exactly one Search index")
        index = indexes[0]
        agents[role] = {"name": name, "version": str(latest.version), "definition": definition}
        normalized = copy.deepcopy(definition)
        for tool in normalized["tools"]:
            if tool.get("type") == "azure_ai_search":
                for entry in tool["azure_ai_search"]["indexes"]:
                    entry.pop("index_name", None)
                    entry.pop("filter", None)
        comparable.append(normalized)
        response = search._call("POST", f"indexes/{index['index_name']}/docs/search",
                                {"search": "*", "top": 1000, "select": "doc_id,content"})
        if response.get("@odata.nextLink"):
            raise Blocked("index snapshot exceeds 1000 chunks; paginate before comparing")
        contents[role] = sorted((hit["doc_id"], _normalized(hit["content"])) for hit in response["value"])
        by_doc: dict[str, str] = {}
        for doc_id, text in contents[role]:
            by_doc[doc_id] = by_doc.get(doc_id, "") + " " + text
        for row in rows:
            for doc_id, quote in row["evidence"].items():
                if _normalized(quote) not in by_doc.get(doc_id, ""):
                    raise Blocked(f"{row['id']}: reference evidence missing from live {index['index_name']}/{doc_id}")
    if comparable[0] != comparable[1] or contents["simple"] != contents["smart"]:
        raise Blocked("unfair comparison: agent settings or unfiltered index contents differ beyond index/filter")
    catalog = {e.name: e.as_dict() for e in project.evaluators.list(type="builtin")}
    for metric in METRICS:
        if f"builtin.{metric}" not in catalog:
            raise Blocked(f"Foundry evaluator builtin.{metric} is unavailable")
    deployments = arm("GET", f"{env.foundry_account_id}/deployments", "2025-06-01").json()["value"]
    if not any(d["name"] == judge and d["properties"]["provisioningState"] == "Succeeded"
               and d["properties"].get("capabilities", {}).get("chatCompletion") == "true" for d in deployments):
        raise Blocked(f"judge deployment {judge} is not a ready chat-completion deployment")
    return {"agents": agents, "index_content_sha256": _hash(contents["simple"]),
            "index_chunks": len(contents["simple"]),
            "evaluators": {m: {"name": f"builtin.{m}", "version": catalog[f"builtin.{m}"]["version"]}
                           for m in METRICS}}


def evaluation_plan(env: Environment, names: ParticipantNames, rows: list[dict[str, Any]], suite: str,
                    context: dict[str, Any], judge: str) -> Plan:
    plan = Plan("eval-foundry", names.hackername, env.subscription_id, env.resource_group)
    plan.add("upload", f"versioned {suite} evaluation dataset", f"{len(rows)} corpus-grounded questions")
    plan.add("create", "Foundry evaluation", "shared dataset, evaluator definitions and threshold 4/5")
    for role, agent in context["agents"].items():
        plan.add("invoke", f"{agent['name']} v{agent['version']}",
                 f"{len(rows)} questions; index/filter as deployed ({role})",
                 cost=f"agent inference + {len(rows) * len(METRICS)} judge calls on {judge}; usage-based")
    plan.add("write", ".foundry/results/", "remote IDs, full item outputs, sliced comparison and failure reasons")
    plan.notes.extend([
        "reference answers/context are sent only to evaluators, never to the agents",
        "groundedness is against curated reference documents, not a reconstruction of actual retrieval",
        "Smart RAG is contracts-scoped: report non-contract regressions separately",
        "no agents, indexes, roles or shared infrastructure are changed",
    ])
    return plan


def _metadata(root: Path, state_path: Path, state: dict[str, Any]) -> None:
    path = root / "agent-metadata.yaml"
    metadata = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    metadata = metadata or {}
    metadata.setdefault("defaultEnvironment", "workshop")
    env = metadata.setdefault("environments", {}).setdefault("workshop", {})
    env["projectEndpoint"] = state["project_endpoint"]
    suites = env.setdefault("evaluationSuites", [])
    suite = {
        "id": f"rag-{state['suite']}", "tags": {"tier": "smoke" if state["suite"] == "smoke" else "regression"},
        "generationSource": "manual-fallback-sdk", "dataset": state["dataset_name"],
        "datasetVersion": state["dataset_version"], "datasetUri": state.get("dataset_uri"),
        "datasetFile": state["dataset_file"], "datasetSha256": state["dataset_sha256"],
        "evaluators": [{**e, "threshold": 4} for e in state["context"]["evaluators"].values()],
    }
    suites[:] = [s for s in suites if s["id"] != suite["id"]] + [suite]
    env["lastEval"] = {"evalId": state["eval_id"], "runs": state["runs"], "stateFile": str(state_path),
                       "startedAt": state["started_at"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")


def start(project: Any, client: Any, env: Environment, names: ParticipantNames, rows: list[dict[str, Any]],
          context: dict[str, Any], *, suite: str, judge: str, root: Path) -> Path:
    dataset_name = f"{names.simple_rag_agent}-eval-{suite}"
    version = rows[0]["dataset_version"]
    dataset_path = root / "datasets" / f"{dataset_name}-{version}.jsonl"
    text = "".join(json.dumps(row) + "\n" for row in rows)
    if dataset_path.exists() and dataset_path.read_text(encoding="utf-8") != text:
        raise WorkshopError(f"refusing to overwrite an existing dataset: {dataset_path}")
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text(text, encoding="utf-8")
    state: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(), "project_endpoint": env.resolved_project_endpoint(),
        "participant": names.hackername, "suite": suite, "judge": judge, "dataset_name": dataset_name,
        "dataset_version": version, "dataset_file": str(dataset_path.resolve()), "dataset_sha256": _hash(rows),
        "context": context, "rows": rows, "runs": {},
    }
    try:
        dataset = project.datasets.upload_file(name=dataset_name, version=version, file_path=str(dataset_path))
        state["dataset_uri"] = dataset.id
    except HttpResponseError as exc:
        state["dataset_uri"] = None
        state["dataset_registration_error"] = str(exc)[:800]
        state["dataset_transport"] = "inline fallback; input rows persist in both evaluation runs"
        print(f"Dataset registration unavailable; using inline inputs: {str(exc)[:300]}", flush=True)
    properties = {key: {"type": "string"} for key in
                  ("id", "query", "category", "ground_truth", "expected_behavior", "reference_context")}
    evaluation = client.evals.create(
        name=f"FOMO {names.hackername} Simple vs Smart RAG ({suite})",
        data_source_config={"type": "custom", "item_schema": {
            "type": "object", "properties": properties, "required": list(properties),
        }, "include_sample_schema": True},
        testing_criteria=cast(Any, criteria(judge)),
    )
    state["eval_id"] = evaluation.id
    state_path = root / "results" / "workshop" / evaluation.id / "manifest.json"
    _save(state_path, state)
    _metadata(root, state_path, state)
    for role, agent in context["agents"].items():
        run = client.evals.runs.create(
            eval_id=evaluation.id, name=f"{role}-v{agent['version']}-{suite}",
            data_source=cast(Any, target_source(agent, rows, state.get("dataset_uri"))),
        )
        state["runs"][role] = {"id": run.id, "status": run.status, "agent": agent["name"],
                              "agent_version": agent["version"]}
        _save(state_path, state)
        _metadata(root, state_path, state)
        print(f"Foundry {role}: eval={evaluation.id} run={run.id}", flush=True)
    return state_path


def collect(client: Any, env: Environment, state_path: Path, *, timeout: float = 1800,
            interval: float = 30) -> dict[str, Any]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state["project_endpoint"] != env.resolved_project_endpoint():
        raise WorkshopError("evaluation belongs to a different project endpoint")
    if set(state["runs"]) != {"simple", "smart"}:
        raise Blocked(f"only some evaluation runs were submitted; inspect {state_path}")
    deadline = time.monotonic() + timeout
    while True:
        for role, info in state["runs"].items():
            if info.get("output_file"):
                continue
            run = client.evals.runs.retrieve(eval_id=state["eval_id"], run_id=info["id"])
            info.update(status=run.status, report_url=run.report_url)
            if run.status in TERMINAL:
                items = [item.model_dump(mode="json", warnings=False) for item in client.evals.runs.output_items.list(
                    eval_id=state["eval_id"], run_id=info["id"])]
                output_path = state_path.parent / f"{info['id']}.json"
                _save(output_path, {"run": run.model_dump(mode="json", warnings=False), "output_items": items})
                info["output_file"] = str(output_path.resolve())
                print(f"Foundry {role}: {run.status}, {len(items)} item outputs downloaded", flush=True)
            _save(state_path, state)
        if all(r.get("output_file") for r in state["runs"].values()):
            break
        if time.monotonic() >= deadline:
            raise Blocked(f"Foundry evaluation still running; resume with `fomo eval-foundry --resume {state_path}`")
        time.sleep(interval)
    report = comparison(state)
    report_path = state_path.parent / "comparison.json"
    _save(report_path, report)
    md_path = state_path.parent / "comparison.md"
    md_path.write_text(render(report), encoding="utf-8")
    report["paths"] = {"json": str(report_path), "markdown": str(md_path), "state": str(state_path)}
    root = state_path.parents[3]
    _metadata(root, state_path, state)
    return report


def _output(item: dict[str, Any]) -> tuple[dict[str, Any], str, Any]:
    data = item.get("datasource_item") or {}
    data = data.get("item", data)
    sample = item.get("sample") or {}
    text = data.get("sample.output_text") or sample.get("output_text") or ""
    output = data.get("sample.output_items") or sample.get("output_items") or sample.get("output") or []
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            output = []
    if not text:
        text = " ".join(part.get("text", "") for message in output if isinstance(message, dict)
                        for part in message.get("content", []) if isinstance(part, dict))
    return data, text, output


def _citations(value: Any, titles: dict[str, str]) -> set[str]:
    found = set()
    if isinstance(value, dict):
        if value.get("type") == "url_citation":
            doc_id = resolve_doc_id(value.get("title"), value.get("url"), titles)
            if doc_id:
                found.add(doc_id)
        for child in value.values():
            found.update(_citations(child, titles))
    elif isinstance(value, list):
        for child in value:
            found.update(_citations(child, titles))
    return found


def _search_calls(value: Any) -> int:
    if isinstance(value, dict):
        called = value.get("type") == "azure_ai_search_call" or (
            value.get("type") in ("tool_call", "function_call") and value.get("name") == "azure_ai_search")
        return int(called) + sum(_search_calls(child) for child in value.values())
    return sum(_search_calls(child) for child in value) if isinstance(value, list) else 0


def comparison(state: dict[str, Any]) -> dict[str, Any]:
    _, docs = load_manifest()
    titles = {d.title.strip().lower(): d.doc_id for d in docs}
    references = {row["id"]: row for row in state["rows"]}
    by_query = {row["query"]: row["id"] for row in state["rows"]}
    report: dict[str, Any] = {
        "eval_id": state["eval_id"], "participant": state["participant"], "suite": state["suite"],
        "dataset_sha256": state["dataset_sha256"], "context": state["context"], "runs": {}, "paired": [],
        "caveats": [
            "Groundedness uses curated reference documents, not the agent's retrieved context.",
            "Reference context contains expected sources only: valid extra sources can receive false "
            "ungroundedness flags. Inspect judge reasons before calling these hallucinations.",
            "Reference answers and evidence are judge-only; both targets receive only the same query.",
            "Single-run seed benchmark: differences are descriptive, not statistical proof.",
            "Smart RAG's fixed contract filter can improve contract precision while excluding other document types.",
        ],
    }
    for role, info in state["runs"].items():
        payload = json.loads(Path(info["output_file"]).read_text(encoding="utf-8"))
        rows = []
        for item in payload["output_items"]:
            data, answer, outputs = _output(item)
            qid = data.get("id") or by_query.get(data.get("query"))
            if qid not in references:
                raise WorkshopError(f"cannot associate Foundry output with a dataset row: {item.get('id')}")
            ref = references[qid]
            scores: dict[str, dict[str, Any]] = {
                m: next((r for r in item.get("results", []) if r.get("metric") == m
                         or r.get("name") == m), {}) for m in METRICS}
            cited = _citations(outputs, titles)
            relevant = set(ref["source_doc_ids"])
            rows.append({"id": qid, "query": ref["query"], "category": ref["category"], "answer": answer,
                         "status": item.get("status"), "scores": scores, "cited_doc_ids": sorted(cited),
                         "citation_precision": len(cited & relevant) / len(cited) if cited else None,
                         "citation_recall": (len(cited & relevant) / len(relevant)
                                             if ref["category"] != "unanswerable" else None),
                         "search_tool_calls": _search_calls(outputs), "error": item.get("error")})
        ids = [row["id"] for row in rows]
        if len(ids) != len(set(ids)):
            raise WorkshopError(f"duplicate Foundry output rows in {role}")
        slices = {}
        for category in ("all", "contract", "non_contract", "unanswerable"):
            chosen = [r for r in rows if category == "all" or r["category"] == category]
            expected = sum(category == "all" or r["category"] == category for r in state["rows"])
            metrics = {}
            for metric in METRICS:
                values = [r["scores"][metric].get("score") for r in chosen]
                numeric = [v for v in values if isinstance(v, (int, float))]
                decisions = [r["scores"][metric].get("passed") for r in chosen]
                measured = [v for v in decisions if isinstance(v, bool)]
                metrics[metric] = {
                    "mean": sum(numeric) / len(numeric) if numeric else None, "scored": len(numeric),
                    "pass_rate": sum(measured) / len(measured) if measured else None,
                    "unscored": expected - len(numeric), "unknown_pass": expected - len(measured),
                }
            citation = {}
            for name in ("citation_precision", "citation_recall"):
                values = [r[name] for r in chosen if r[name] is not None]
                citation[name] = sum(values) / len(values) if values else None
            slices[category] = {"expected": expected, "returned": len(chosen), "metrics": metrics,
                                **citation, "with_resolved_citations": sum(bool(r["cited_doc_ids"]) for r in chosen),
                                "with_search_call": sum(r["search_tool_calls"] > 0 for r in chosen)}
        failures = []
        for row in rows:
            for metric, score in row["scores"].items():
                if score.get("passed") is not True or not score.get("reason"):
                    failures.append({"id": row["id"], "category": row["category"], "metric": metric,
                                     "score": score.get("score"), "passed": score.get("passed"),
                                     "reason": score.get("reason"),
                                     "cluster": ("unscored/evaluator detail missing" if score.get("passed") is None
                                                 or not score.get("reason") else
                                                 "possible scope exclusion" if row["category"] == "non_contract"
                                                 and role == "smart" else "answer quality; inspect judge reason")})
            if row["error"] or not row["answer"]:
                failures.append({"id": row["id"], "cluster": "runtime/empty answer", "error": row["error"]})
            if not row["search_tool_calls"]:
                failures.append({"id": row["id"], "cluster": "required Search tool not called"})
        for qid in set(references) - set(ids):
            failures.append({"id": qid, "cluster": "missing output item"})
        report["runs"][role] = {**info, "slices": slices, "rows": rows, "failures": failures,
                               "latency": payload["run"].get("latency"),
                               "result_counts": payload["run"].get("result_counts")}
    for qid, ref in references.items():
        pair = {role: next((r for r in run["rows"] if r["id"] == qid), None)
                for role, run in report["runs"].items()}
        deltas = {}
        for metric in METRICS:
            a = (pair["simple"] or {}).get("scores", {}).get(metric, {}).get("score")
            b = (pair["smart"] or {}).get("scores", {}).get(metric, {}).get("score")
            deltas[metric] = b - a if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
        report["paired"].append({"id": qid, "category": ref["category"], "smart_minus_simple": deltas})
    report["complete"] = all(run["status"] == "completed" and run["slices"]["all"]["returned"] == len(references)
                             and not any(s["unscored"] or s["unknown_pass"]
                                         for s in run["slices"]["all"]["metrics"].values())
                             for run in report["runs"].values())
    return report


def render(report: dict[str, Any]) -> str:
    lines = [f"# Foundry Simple vs Smart RAG: {report['participant']} ({report['suite']})", "",
             f"Evaluation: `{report['eval_id']}`. Complete scoring: {report['complete']}.",
             "Scores are 1-5; threshold 4. Parentheses show scored/expected cases; missing grades are not passes.", "",
             "| Slice | Agent | Returned/expected | Relevance | Reference groundedness | Similarity | Completeness |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for category in ("all", "contract", "non_contract", "unanswerable"):
        for role, run in report["runs"].items():
            group = run["slices"][category]
            cells = " | ".join(
                "-" if group["metrics"][m]["mean"] is None else
                f"{group['metrics'][m]['mean']:.2f} ({group['metrics'][m]['scored']}/{group['expected']})"
                for m in METRICS)
            lines.append(f"| {category} | {role} | {group['returned']}/{group['expected']} | {cells} |")
    lines += ["", "## Runs and performance", ""]
    for role, run in report["runs"].items():
        group = run["slices"]["all"]
        lines += [f"- {role}: [{run['agent']} v{run['agent_version']}]({run.get('report_url') or ''}) "
                  f"({run['status']}); latency: "
                  f"{json.dumps(run['latency']) if run.get('latency') else 'not reported by Foundry'}",
                  f"  Resolved citations in {group['with_resolved_citations']}/{group['expected']} responses; "
                  f"citation precision={group['citation_precision']}, recall={group['citation_recall']}.",
                  f"  Search tool invoked in {group['with_search_call']}/{group['expected']} responses."]
    lines += ["", "## Failure clusters", ""]
    for role, run in report["runs"].items():
        counts = Counter(f["cluster"] for f in run["failures"])
        lines.append(f"- {role}: {dict(counts) or 'none'}")
        for failure in run["failures"]:
            reason = _normalized(str(failure.get("reason") or failure.get("error") or "No evaluator detail"))
            lines.append(f"  - {failure['id']} {failure.get('metric', '')}: {reason}")
    lines += ["", "## Caveats", "", *[f"- {c}" for c in report["caveats"]], ""]
    return "\n".join(lines)


def execute(env: Environment, names: ParticipantNames, *, suite: str, judge: str, root: Path,
            rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    with (
        AIProjectClient(endpoint=env.resolved_project_endpoint(), credential=credential()) as project,
        project.get_openai_client() as client,
    ):
        state_path = start(project, client, env, names, rows, context, suite=suite, judge=judge, root=root)
        return collect(client, env, state_path)
