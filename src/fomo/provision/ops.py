"""Participant operations: provision, miner deploy/run, smart stage, cheatcode.

Each operation has a `plan_*` (read-only inspection -> Plan) and an `apply_*` that executes
exactly the planned steps with the shared building blocks. Cheatcode is the composition of
the same functions a participant runs step by step; there is no separate demo path.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import capacity
from ..agents.hosted import HostedMiner, build_zip
from ..agents.prompt import (
    PromptAgents,
    agent_specs,
    document_scopes,
    ensure_kb_connection,
    kb_connection_exists,
    smart_spec,
)
from ..config import Environment
from ..data.ingest import ingest_corpus
from ..decision.client import DecisionClient
from ..decision.config import REFERENCE_CONFIG, load_classifier_config
from ..errors import AgentError, Blocked, SearchError, WorkshopError
from ..miner.pipeline import Miner
from ..naming import ParticipantNames
from ..plan import Plan
from ..search.client import SearchClient
from ..search.knowledge import KB_API_VERSION, KnowledgeClient
from ..telemetry.logging import RunLogger, write_trace
from .participant import container_client, remember, run_dir, status, write_manifest
from .starter import write_starter

LOCAL_LABEL = ("LOCAL DEVELOPER MODE: classification runs on this machine, not in the Foundry hosted "
               "agent. Useful for learning; it does NOT satisfy the hosted Metadata Miner requirement.")


def _usd(x: float) -> str:
    return f"~${x:.3f}" if x < 1 else f"~${x:.2f}"


def _embed_cost() -> str:
    emb = capacity.MODEL_USD_PER_M["text-embedding-3-small"] or {"input": 0.0}
    return f"embeddings {_usd(capacity.EMBEDDING_TOKENS_PER_INDEX * emb['input'] / 1e6)}"


def _agent_cost(questions: int = 1) -> str:
    chat = capacity.MODEL_USD_PER_M["gpt-5-mini"] or {"input": 0.0, "output": 0.0}
    t = capacity.AGENT_TOKENS_PER_QUESTION
    usd = questions * (t['input'] * chat['input'] + t['output'] * chat['output']) / 1e6
    return f"{questions} agent answer(s) {_usd(usd)}"


def _decision_cost(docs: int) -> str:
    tokens = docs * capacity.DECISION_INPUT_TOKENS_PER_DOC
    return f"{docs} decision call(s), ~{tokens:,} input tokens (price: see Foundry)"


def canonical_question() -> str:
    from ..evaluation.runner import load_eval_queries_full

    for q in load_eval_queries_full()["queries"]:
        if q.get("canonical"):
            return str(q["question"])
    return "What payment terms apply under our supplier contracts?"


def _st(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def _agent_exists(state: dict[str, Any], key: str) -> bool:
    return bool(_st(_st(state, "agents"), key).get("exists"))


def _new_plan(op: str, env: Environment, names: ParticipantNames) -> Plan:
    return Plan(op, names.hackername, env.subscription_id, env.resource_group)


# ---- provision -------------------------------------------------------------------------------
PROVISION_STEPS = ("upload", "index", "agent")


def _steps(step: str) -> tuple[str, ...]:
    if step == "all":
        return PROVISION_STEPS
    if step not in PROVISION_STEPS:
        raise ValueError(step)
    return (step,)


def check_provision_prerequisites(state: dict[str, Any], step: str) -> None:
    """A single step (guided mode) must follow the previous one; `all` runs them in order itself."""
    if step == "index" and not _st(state, "container").get("documents"):
        raise Blocked("your container has no documents yet, so there is nothing to index",
                      fix="run `uv run fomo provision --step upload` first")
    if step == "agent" and not _st(state, "raw_index").get("chunks"):
        raise Blocked("the raw index has no chunks yet, so Simple RAG would have nothing to search",
                      fix="run `uv run fomo provision --step index` first")


def plan_provision(env: Environment, names: ParticipantNames, state: dict[str, Any], *,
                   smoke: bool = True, step: str = "all") -> Plan:
    steps = _steps(step)
    p = _new_plan("provision" if step == "all" else f"provision --step {step}", env, names)
    if "upload" in steps:
        c = _st(state, "container")
        p.add("create", f"blob container {names.container}", "participant inbox",
              state="exists" if c.get("exists") else "pending")
        docs = c.get("documents", 0)
        p.add("upload", "25 synthetic corpus documents", "idempotent, checksummed, never overwrites edits",
              state="exists" if docs >= 25 else "pending")
    if "index" in steps:
        raw_ok = bool(_st(state, "raw_index").get("chunks"))
        p.add("create", f"search {names.datasource}, {names.raw_index}, {names.raw_skillset}, {names.raw_indexer}",
              "raw baseline (no classification fields)", state="exists" if raw_ok else "pending")
        p.add("run", f"indexer {names.raw_indexer}", "extract, chunk, embed; wait for success",
              cost="" if raw_ok else _embed_cost())
        smart = _st(state, "smart_indexer")
        p.add("create", f"search {names.smart_index}, {names.smart_skillset}, {names.smart_indexer}",
              "smart schema, indexer created DISABLED (pending until classification)",
              state="exists" if smart.get("exists") else "pending")
    if "agent" in steps:
        p.add("create", f"prompt agent {names.simple_rag_agent}", f"{env.chat_deployment} + Search tool on "
              f"{names.raw_index}, no filter", state="exists" if _agent_exists(state, "simple_rag") else "pending")
        if smoke:
            p.add("invoke", f"{names.simple_rag_agent}", "smoke test: canonical contracts question",
                  cost=_agent_cost())
        starter = Path(names.run_dir) / "miner" / "classifier.yaml"
        p.add("write", f"{names.run_dir}/miner/", "editable classifier starter (local files only)",
              state="exists" if starter.exists() else "pending")
    p.notes.append("shared resources (Foundry, models, Search service, storage account) are reused, never changed")
    p.notes.append("hackername is a namespace label; isolation comes from RBAC scopes, not names")
    return p


def apply_provision(env: Environment, names: ParticipantNames, log: RunLogger, *, smoke: bool = True,
                    state: dict[str, Any] | None = None, step: str = "all") -> dict[str, Any]:
    steps = _steps(step)
    out: dict[str, Any] = {"participant": names.hackername, "step": step}
    remember(names)
    search = SearchClient(env)
    if "upload" in steps:
        _apply_upload(env, names, log, out)
    if "index" in steps:
        _apply_index(env, names, log, out, search)
    if "agent" in steps:
        _apply_agent(env, names, log, out, smoke=smoke)
    final = status(env, names)
    write_manifest(env, names, final)
    out["status"] = final
    return out


def _apply_upload(env: Environment, names: ParticipantNames, log: RunLogger, out: dict[str, Any]) -> None:
    with log.stage("ingest"):
        rep = ingest_corpus(container_client(env, names))
        log.info(f"container {names.container}: {len(rep.uploaded)} uploaded, {len(rep.unchanged)} unchanged, "
                 f"{len(rep.conflicts)} conflicts")
        if rep.conflicts:
            log.warning(f"{len(rep.conflicts)} blob(s) differ from the corpus and were NOT overwritten: "
                        + ", ".join(rep.conflicts[:5]))
        out["ingest"] = {"uploaded": rep.uploaded, "unchanged": len(rep.unchanged), "conflicts": rep.conflicts}


def _apply_index(env: Environment, names: ParticipantNames, log: RunLogger, out: dict[str, Any],
                 search: SearchClient) -> None:
    with log.stage("raw-index"):
        created = search.ensure_participant(names, smart=False)
        log.info("search raw objects: " + ", ".join(f"{k} {v}" for k, v in created.items()))
        run = search.run_and_wait(names.raw_indexer)
        log.info(f"indexer {names.raw_indexer}: {run.status}, {run.items_processed} processed, "
                 f"{run.items_failed} failed ({run.seconds:.0f}s)")
        if not run.ok:
            raise SearchError(f"raw indexer {run.status}: {'; '.join(run.errors) or 'see portal'}",
                              fix="run `fomo preflight` (Search identity -> Storage / embeddings)",
                              owner="facilitator")
        chunks = search.wait_for_count(names.raw_index)
        if chunks == 0:
            raise SearchError(f"{names.raw_index} has 0 chunks after a successful run",
                              fix="check that the container has documents (`fomo status`)")
        hits = search.query(names.raw_index, canonical_question(), top=3, select="doc_id,title,url")
        log.info(f"{names.raw_index}: {chunks} chunks; top hit for the canonical question: "
                 + (f"{hits[0].get('doc_id')} '{hits[0].get('title')}' {hits[0].get('url')}" if hits else "none"))
        out["raw"] = {"run": run.__dict__, "chunks": chunks, "top": hits}
    with log.stage("smart-pending"):
        current = search.get("indexers", names.smart_indexer)
        pending = current is None or bool(current.get("disabled"))
        search.ensure_participant(names, smart=True, pending=pending)
        log.info(f"smart index {names.smart_index}: schema ready; indexer "
                 + ("disabled (pending classification)" if pending else "already enabled, left as is"))


def _apply_agent(env: Environment, names: ParticipantNames, log: RunLogger, out: dict[str, Any], *,
                 smoke: bool) -> None:
    agents = PromptAgents(env)
    specs = agent_specs(env, names)
    with log.stage("simple-rag"):
        version = agents.ensure(specs["simple"])
        log.info(f"prompt agent {names.simple_rag_agent} v{version} (index {names.raw_index}, no filter)")
        out["simple_rag"] = {"name": names.simple_rag_agent, "version": version}
        if smoke:
            ans = agents.ask(names.simple_rag_agent, canonical_question())
            log.info(f"smoke: {ans.tool_calls} search call(s), cited {', '.join(ans.cited_doc_ids) or 'nothing'} "
                     f"({ans.latency_ms} ms)")
            if not ans.citations:
                log.warning("agent answered without citations; check the project Search connection (preflight)")
            out["simple_rag"]["smoke"] = {"cited": ans.cited_doc_ids, "latency_ms": ans.latency_ms,
                                          "answer": ans.text[:600]}
    folder, created_starter = write_starter(names)
    log.info(f"miner starter {'created' if created_starter else 'kept (your edits)'}: {folder}")


PROVISION_NEXT = """next:
  1. open {run_dir}/miner/README.md and finish the TODOs in classifier.yaml
  2. try it (read-only):   uv run python {run_dir}/miner/try_classifier.py doc-003 doc-018
  3. deploy + run hosted:  uv run fomo miner deploy --classifier {run_dir}/miner/classifier.yaml --apply
                           uv run fomo miner run --hosted --apply
  4. smart index + agent:  uv run fomo smart --apply
  5. compare:              uv run fomo eval
recover: rerun `uv run fomo provision --apply` (idempotent); `uv run fomo status` shows what exists."""


# ---- hosted miner ----------------------------------------------------------------------------
def plan_miner_deploy(env: Environment, names: ParticipantNames, classifier: Path | None) -> Plan:
    p = _new_plan("miner deploy", env, names)
    cfg = load_classifier_config(classifier)
    _, sha = build_zip(classifier)
    p.add("deploy", f"hosted agent {names.metadata_miner_agent}",
          f"source ZIP sha256 {sha[:12]}, classifier '{cfg.version}' ({'yours' if classifier else 'reference'}), "
          "python 3.13, invocations protocol", cost="hosted session compute while invoked")
    p.add("grant", "Storage Blob Data Contributor -> agent identity",
          f"scope: container {names.container} only")
    p.add("grant", "Foundry User -> agent identity", "scope: Foundry account (decision model data plane)")
    p.notes.append("the agent identity exists only after deployment, so these grants are a post-deploy step "
                   "(Bicep cannot express them); requires roleAssignments/write on those scopes")
    return p


def apply_miner_deploy(env: Environment, names: ParticipantNames, log: RunLogger, classifier: Path | None,
                       *, force: bool = False) -> dict[str, Any]:
    miner = HostedMiner(env, names)
    with log.stage("hosted-deploy"):
        dep = miner.deploy(classifier_yaml=classifier, force=force, log=log.info)
        log.info(f"{dep.name} v{dep.version} {dep.status} in {dep.elapsed_s:.0f}s; identity {dep.principal_id}")
    with log.stage("hosted-grants"):
        grants = miner.grant(dep)
        log.info("grants: " + ", ".join(f"{k} {v}" for k, v in grants.items()))
    return {"name": dep.name, "version": dep.version, "status": dep.status, "principal_id": dep.principal_id,
            "zip_sha256": dep.sha256, "grants": grants, "deploy_seconds": dep.elapsed_s}


def plan_miner_run(env: Environment, names: ParticipantNames, *, hosted: bool, mode: str,
                   only: list[str] | None, limit: int | None, dry_run: bool) -> Plan:
    p = _new_plan("miner run", env, names)
    n = len(only) if only else (limit or 25)
    where = f"hosted agent {names.metadata_miner_agent}" if hosted else "LOCAL developer mode"
    p.add("invoke", where, f"mode={mode}" + (f", only={','.join(only)}" if only else "")
          + (f", limit={limit}" if limit else ""), cost=_decision_cost(n))
    if not dry_run:
        p.add("write", f"blob metadata in {names.container}",
              "document_type, classification_confidence, classification_version (+provenance); ETag-guarded merge")
    else:
        p.notes.append("dry run: classifies but writes no metadata")
    if not hosted:
        p.notes.append(LOCAL_LABEL)
    return p


def invoke_hosted(env: Environment, names: ParticipantNames, log: RunLogger, payload: dict[str, Any], *,
                  rbac_wait: float = 360) -> dict[str, Any]:
    """Invoke; while fresh role assignments propagate (403) or the session warms up (424), retry."""
    miner = HostedMiner(env, names)
    if miner.current() is None:
        raise AgentError(f"hosted agent {names.metadata_miner_agent} does not exist",
                         fix="run `fomo miner deploy --apply` (or `fomo cheatcode --apply`)")
    start = time.monotonic()
    while True:
        try:
            with log.stage("hosted-invoke"):
                return miner.invoke(payload)
        except AgentError as exc:
            text = str(exc)
            network = ("network security perimeter" in text or "AuthorizationFailure" in text
                       or "network rules rejected" in text)
            retryable = not network and any(marker in text for marker in (
                "PermissionDenied", "AuthorizationPermissionMismatch", "Needs Storage Blob Data Contributor",
                "using this permission", "HTTP 424", "session_not_ready"))
            if not retryable or time.monotonic() - start > rbac_wait:
                raise
            log.info("waiting for role assignments / session to become effective (retry in 30s)")
            time.sleep(30)


def _log_items(log: RunLogger, names: ParticipantNames, items: list[dict[str, Any]]) -> None:
    total = len(items)
    for i, it in enumerate(items, 1):
        label = it.get("label")
        prob = it.get("probability")
        text = f"[{i}/{total}] {it.get('status')} {it.get('doc_id') or it.get('blob')}"
        if label:
            text += f" => {label}" + (f" (p={prob:.2f}" if prob is not None else " (") + f", {it.get('reason')})"
        if it.get("error"):
            text += f" [{it['error'][:160]}]"
        log.info(text, elapsed_ms=it.get("latency_ms"))


def apply_miner_run(env: Environment, names: ParticipantNames, log: RunLogger, *, hosted: bool, mode: str,
                    only: list[str] | None, limit: int | None, dry_run: bool,
                    classifier: Path | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"mode": mode, "dry_run": dry_run}
    if only:
        payload["only"] = only
    if limit:
        payload["limit"] = limit
    t0 = time.monotonic()
    if hosted:
        result = invoke_hosted(env, names, log, payload)
        if result.get("runtime") != "foundry-hosted":
            log.warning(f"runtime reported '{result.get('runtime')}', expected foundry-hosted")
    else:
        log.warning(LOCAL_LABEL)
        cfg = load_classifier_config(classifier)
        miner = Miner(container_client(env, names), DecisionClient(env.resolved_decision_endpoint(),
                                                                   env.decision_deployment),
                      cfg, participant=names.hackername, log=lambda msg, **kw: log.debug(msg))
        report = miner.run(mode=mode, only=only, limit=limit, dry_run=dry_run)
        result = report.to_dict() | {"runtime": "local-developer-mode", "ok": report.ok}
    result["client_seconds"] = round(time.monotonic() - t0, 1)
    _log_items(log, names, result.get("items", []))
    log.info(f"{result.get('runtime')}: {result.get('counts')} labels {result.get('labels')} "
             f"({result['client_seconds']}s, classifier {result.get('classifier_version')})")
    path = run_dir(names) / "miner-runs" / f"{result.get('run_id', 'run')}-{'hosted' if hosted else 'local'}.json"
    write_trace(path, result)
    result["trace"] = str(path)
    if not result.get("ok", True):
        raise WorkshopError(f"miner finished with errors: {result.get('counts')}",
                            fix=f"see {path}; rerun in delta mode retries only failed/changed documents")
    return result


# ---- smart stage -----------------------------------------------------------------------------
def plan_smart(env: Environment, names: ParticipantNames, state: dict[str, Any], *, smoke: bool = True,
               reset: bool = False, classifier: Path | None = None) -> Plan:
    p = _new_plan("smart", env, names)
    cfg = load_classifier_config(classifier)
    classified = _st(state, "container").get("classified", 0)
    if not classified:
        p.notes.append("no classified blobs yet: run the miner first, otherwise every chunk is 'unclassified'")
    p.add("update", f"indexer {names.smart_indexer}", "enable (was pending)" if _st(state, "smart_indexer")
          .get("disabled") else "ensure definition")
    p.add("run", f"indexer {names.smart_indexer}", ("reset + " if reset else "") + "index blob metadata as "
          "filterable document_type; wait and verify", cost=_embed_cost())
    kb = KnowledgeClient(env)
    p.add("update", f"index {names.smart_index}", "ensure semantic configuration (the knowledge source "
          "reranks with it; Simple RAG's raw index is unchanged)")
    p.add("create", f"knowledge source {names.knowledge_source}", f"Foundry IQ ({KB_API_VERSION}) over "
          f"{names.smart_index}; document_type query hint = classifier labels present in the index "
          f"({', '.join(cfg.labels)}) with their definitions",
          state="exists" if _kb_exists(kb, "knowledgesources", names.knowledge_source) else "pending")
    p.add("create", f"knowledge base {names.knowledge_base}", f"query planner {env.chat_deployment}, reasoning "
          f"effort {env.kb_reasoning_effort}: derives the document_type filter per question",
          cost="per question: query-planning tokens + semantic ranker (Search semantic plan)",
          state="exists" if _kb_exists(kb, "knowledgebases", names.knowledge_base) else "pending")
    p.add("create", f"project connection {names.knowledge_base_connection}",
          "RemoteTool, project managed identity -> knowledge base MCP endpoint (Search Index Data Reader "
          "already granted)", state="exists" if kb_connection_exists(env, names) else "pending")
    p.add("create", f"prompt agent {names.smart_rag_agent}", "same model and grounding rules as Simple RAG; "
          f"one MCP tool: knowledge_base_retrieve on {names.knowledge_base}",
          state="exists" if _agent_exists(state, "smart_rag") else "pending")
    if smoke:
        p.add("invoke", names.smart_rag_agent, "smoke test: canonical contracts question, plus one knowledge base "
              "retrieve to show the generated filter", cost=_agent_cost())
    p.notes.append(f"routing definitions: {classifier or REFERENCE_CONFIG.name + ' (reference)'}")
    return p


def apply_smart(env: Environment, names: ParticipantNames, log: RunLogger, *, smoke: bool = True,
                reset: bool = False, classifier: Path | None = None) -> dict[str, Any]:
    search = SearchClient(env)
    cfg = load_classifier_config(classifier)
    out: dict[str, Any] = {}
    with log.stage("smart-index"):
        search.ensure_participant(names, smart=True, pending=False)
        run = search.run_and_wait(names.smart_indexer, reset=reset)
        log.info(f"indexer {names.smart_indexer}: {run.status}, {run.items_processed} processed, "
                 f"{run.items_failed} failed ({run.seconds:.0f}s)")
        if not run.ok:
            raise SearchError(f"smart indexer {run.status}: {'; '.join(run.errors) or 'see portal'}")
        search.wait_for_count(names.smart_index)
        facets = search.facets(names.smart_index, "document_type")
        log.info("document_type facets (chunks): " + ", ".join(f"{k}={v}" for k, v in sorted(facets.items())))
        scopes = document_scopes(cfg.criteria, list(facets), exclude=(cfg.abstain_label,))
        unknown = sorted(set(facets) - set(scopes) - {cfg.abstain_label, "needs_review", "unclassified"})
        if unknown:
            log.warning(f"labels in the index but not in the classifier (not routable): {', '.join(unknown)}")
        if not scopes:
            raise SearchError(
                f"no classified document types in {names.smart_index}; Smart RAG has nothing to route to",
                fix="run the miner first (`fomo miner run --hosted --apply`), then rerun `fomo smart --apply`")
        log.info(f"routable document types: {', '.join(scopes)}")
        out["smart"] = {"run": run.__dict__, "facets": facets, "scopes": list(scopes)}
    with log.stage("knowledge-base"):
        kb = KnowledgeClient(env)
        kb.ensure(names, scopes)
        log.info(f"knowledge source {names.knowledge_source} (document_type hint: {', '.join(scopes)}) and "
                 f"knowledge base {names.knowledge_base} ({env.chat_deployment}, effort {env.kb_reasoning_effort})")
        if ensure_kb_connection(env, names):
            log.info(f"created project connection {names.knowledge_base_connection}")
        out["knowledge_base"] = {"knowledge_source": names.knowledge_source, "knowledge_base": names.knowledge_base,
                                 "connection": names.knowledge_base_connection}
    with log.stage("smart-rag"):
        agents = PromptAgents(env)
        version = agents.ensure(smart_spec(env, names))
        log.info(f"prompt agent {names.smart_rag_agent} v{version} (knowledge base {names.knowledge_base})")
        out["smart_rag"] = {"name": names.smart_rag_agent, "version": version, "scopes": list(scopes)}
        if smoke:
            question = canonical_question()
            retrieval = kb.retrieve(names, question)
            chosen = ", ".join(dict.fromkeys(f or "no filter" for f in retrieval.filters)) or "no search call"
            log.info(f"smoke: knowledge base filter {chosen} ({len(retrieval.filters)} subqueries); "
                     f"retrieved {', '.join(retrieval.doc_ids) or 'nothing'}")
            ans = agents.ask(names.smart_rag_agent, question)
            log.info(f"smoke: Smart RAG cited {', '.join(ans.cited_doc_ids) or 'nothing'} ({ans.latency_ms} ms)")
            out["smart_rag"]["smoke"] = {"filters": retrieval.filters, "retrieved": retrieval.doc_ids,
                                         "cited": ans.cited_doc_ids, "latency_ms": ans.latency_ms}
    return out


def _kb_exists(kb: KnowledgeClient, kind: str, name: str) -> bool:
    try:
        return kb.get(kind, name) is not None
    except WorkshopError:
        return False


# ---- evaluation ------------------------------------------------------------------------------
def run_eval(env: Environment, names: ParticipantNames, log: RunLogger, *, agents: str = "canonical",
             k: int | None = None, query_ids: list[str] | None = None) -> dict[str, Any]:
    from ..evaluation.report import markdown, terminal_table
    from ..evaluation.runner import evaluate

    with log.stage("evaluate"):
        report = evaluate(env, names, agents=agents, k=k, query_ids=query_ids, log=log.info)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = run_dir(names) / "eval"
    json_path = write_trace(folder / f"eval-{stamp}.json", report)
    md_path = folder / f"eval-{stamp}.md"
    md_path.write_text(markdown(report), encoding="utf-8")
    report["paths"] = {"json": str(json_path), "markdown": str(md_path)}
    report["table"] = terminal_table(report)
    return report


# ---- cheatcode -------------------------------------------------------------------------------
def plan_cheatcode(env: Environment, names: ParticipantNames, state: dict[str, Any],
                   classifier: Path | None) -> Plan:
    p = _new_plan("cheatcode", env, names)
    if not _st(state, "raw_index").get("chunks") or not _agent_exists(state, "simple_rag"):
        for a in plan_provision(env, names, state, smoke=False).actions:
            p.actions.append(a)
    for a in plan_miner_deploy(env, names, classifier).actions:
        p.actions.append(a)
    p.add("invoke", f"hosted agent {names.metadata_miner_agent}", "classify all participant blobs (delta)",
          cost=_decision_cost(25))
    p.add("write", f"blob metadata in {names.container}", "classification fields, ETag-guarded merge")
    for a in plan_smart(env, names, state, smoke=False, classifier=classifier).actions:
        p.actions.append(a)
    p.add("invoke", "evaluation", "direct Search top-k for 8 queries x 5 runs + both agents on the canonical "
          "question", cost=_agent_cost(2))
    p.notes.append("same code paths as provision / miner / smart; nothing is simulated")
    p.notes.append(f"classifier: {classifier or REFERENCE_CONFIG.name + ' (reference)'}")
    p.notes.append("resources stay in place afterwards; cleanup is a separate, explicit command")
    return p


def apply_cheatcode(env: Environment, names: ParticipantNames, log: RunLogger, state: dict[str, Any],
                    classifier: Path | None, *, force_deploy: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"participant": names.hackername, "timings_s": {}}

    def timed(key: str, fn: Any) -> Any:
        t = time.monotonic()
        result = fn()
        out["timings_s"][key] = round(time.monotonic() - t, 1)
        return result

    if not _st(state, "raw_index").get("chunks") or not _agent_exists(state, "simple_rag"):
        out["provision"] = timed("provision", lambda: apply_provision(env, names, log, smoke=False))
    else:
        remember(names)
        write_starter(names)
    try:
        out["miner_deploy"] = timed("miner_deploy", lambda: apply_miner_deploy(env, names, log, classifier,
                                                                               force=force_deploy))
    except AgentError as exc:
        raise Blocked(
            f"hosted Metadata Miner could not be deployed: {exc}",
            fix="this is a real blocker for the hosted requirement. For learning continuity only: "
            "`fomo miner run --local --apply` then `fomo smart --apply` (clearly labeled local mode)",
            owner=exc.owner) from exc
    out["miner_run"] = timed("miner_run", lambda: apply_miner_run(env, names, log, hosted=True, mode="delta",
                                                                   only=None, limit=None, dry_run=False))
    out["smart"] = timed("smart", lambda: apply_smart(env, names, log, smoke=False, classifier=classifier))
    out["evaluation"] = timed("evaluation", lambda: run_eval(env, names, log))
    final = status(env, names)
    write_manifest(env, names, final)
    out["status"] = final
    write_trace(run_dir(names) / "cheatcode" / f"cheatcode-{log.run_id}.json",
                {k: v for k, v in out.items() if k != "evaluation"} | {"evaluation": out["evaluation"]["paths"]})
    return out


def links(env: Environment, names: ParticipantNames) -> list[str]:
    return [
        f"agents (Foundry project): {env.resolved_project_endpoint()}  -> {names.simple_rag_agent}, "
        f"{names.metadata_miner_agent}, {names.smart_rag_agent}",
        f"indexes: {env.resolved_search_endpoint()}/indexes/{names.raw_index} | /indexes/{names.smart_index}",
        f"container: {env.resolved_blob_endpoint()}{names.container}",
        f"logs and traces: {names.run_dir}/",
    ]

