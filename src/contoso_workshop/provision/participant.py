"""Participant namespace: resolve the hackername, inspect live state, write the manifest.

The manifest (`.workshop/runs/<hackername>/manifest.json`) records exact resource IDs,
endpoints, deployments, indexer status and ownership. It is rebuilt from live Azure state
by `contoso status`, never from assumptions.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import WORKSHOP_TAG
from ..azclient import credential
from ..config import Environment, load_participant, save_participant
from ..data.blob import ParticipantContainer
from ..errors import NamingError, WorkshopError
from ..naming import ParticipantNames, normalize_hackername, pick_free_hackername
from ..search.client import SearchClient


def container_client(env: Environment, names: ParticipantNames) -> ParticipantContainer:
    return ParticipantContainer(env.resolved_blob_endpoint(), names.container, credential())


def resolve_participant(env: Environment, explicit: str | None, *, allow_new: bool = False) -> ParticipantNames:
    """--participant > saved .workshop/participant.json > (allow_new) a fresh collision-checked name."""
    raw = explicit or load_participant()
    if raw:
        names = ParticipantNames(normalize_hackername(raw))
        names.validate()
        return names
    if not allow_new:
        raise NamingError("no participant selected",
                          fix="pass --participant <hackername> or run `contoso provision` first")
    search = SearchClient(env)
    taken = set(search.list_names("indexes"))

    def is_taken(name: str) -> bool:
        n = ParticipantNames(name)
        return n.raw_index in taken or n.smart_index in taken or container_client(env, n).exists()

    names = ParticipantNames(pick_free_hackername(is_taken))
    names.validate()
    return names


def remember(names: ParticipantNames) -> None:
    save_participant(names.hackername)


def run_dir(names: ParticipantNames) -> Path:
    return Path(names.run_dir)


def _safe(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except WorkshopError as exc:
        return {"error": str(exc)[:300]} if default is None else default
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:300]}"} if default is None else default


def _indexer_state(search: SearchClient, name: str) -> dict[str, Any] | None:
    body = search.get("indexers", name)
    if body is None:
        return None
    st = search.indexer_status(name)
    last = st.get("lastResult") or {}
    return {"exists": True, "disabled": bool(body.get("disabled")), "status": last.get("status") or "never-run",
            "items_processed": last.get("itemsProcessed"), "items_failed": last.get("itemsFailed"),
            "end_time": last.get("endTime"), "errors": [e.get("errorMessage") for e in last.get("errors") or []][:3]}


def _index_state(search: SearchClient, name: str, *, smart: bool) -> dict[str, Any] | None:
    if search.get("indexes", name) is None:
        return None
    out: dict[str, Any] = {"exists": True, "chunks": search.count(name),
                           "documents": len(search.facets(name, "doc_id"))}
    if smart:
        out["document_type"] = search.facets(name, "document_type")
    return out


def _agents_state(env: Environment, names: ParticipantNames) -> dict[str, Any]:
    from azure.ai.projects import AIProjectClient

    client = AIProjectClient(endpoint=env.resolved_project_endpoint(), credential=credential())

    def one(name: str) -> dict[str, Any]:
        try:
            agent = client.agents.get(name)
        except Exception:
            return {"name": name, "exists": False}
        latest = agent.versions.latest
        item: dict[str, Any] = {"name": name, "exists": True, "id": getattr(agent, "id", None),
                                "version": str(getattr(latest, "version", "")),
                                "kind": str(getattr(latest.definition, "kind", ""))}
        status = getattr(latest, "status", None)
        if status:
            item["status"] = str(status)
        return item

    keys = {"simple_rag": names.simple_rag_agent, "metadata_miner": names.metadata_miner_agent,
            "smart_rag": names.smart_rag_agent}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = dict(zip(keys, pool.map(one, keys.values()), strict=True))
    return results


def status(env: Environment, names: ParticipantNames, *, agents: bool = True) -> dict[str, Any]:
    search = SearchClient(env)
    cont = container_client(env, names)

    def container_state() -> dict[str, Any]:
        if not cont.exists():
            return {"exists": False}
        blobs = list(cont.list())
        classified = [b for b in blobs if {k.lower() for k in b.metadata} & {"document_type"}]
        return {"exists": True, "documents": len(blobs), "classified": len(classified)}

    probes: dict[str, tuple[str, Any]] = {
        "container": (names.container, container_state),
        "datasource": (names.datasource,
                       lambda: {"exists": search.get("datasources", names.datasource) is not None}),
        "raw_index": (names.raw_index, lambda: _index_state(search, names.raw_index, smart=False)),
        "raw_indexer": (names.raw_indexer, lambda: _indexer_state(search, names.raw_indexer)),
        "smart_index": (names.smart_index, lambda: _index_state(search, names.smart_index, smart=True)),
        "smart_indexer": (names.smart_indexer, lambda: _indexer_state(search, names.smart_indexer)),
    }
    if agents:
        probes["agents"] = ("", lambda: _agents_state(env, names))
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {k: pool.submit(_safe, fn) for k, (_, fn) in probes.items()}
    state: dict[str, Any] = {"participant": names.hackername,
                             "generated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    for key, (name, _) in probes.items():
        value = futures[key].result()
        state[key] = value if key == "agents" else {"name": name, **(value or {"exists": False})}
    state["stage"] = stage_of(state)
    return state


def stage_of(state: dict[str, Any]) -> str:
    """Coarse progress label used by status, the facilitator dashboard and the skills."""
    agents = state.get("agents")

    def has(key: str) -> bool:
        if agents is None:  # agent lookup skipped (--no-agents): judge by data only
            return True
        return bool((agents.get(key) or {}).get("exists"))

    container = state.get("container") or {}
    raw = state.get("raw_index") or {}
    smart = state.get("smart_index") or {}
    smart_types = {k: v for k, v in (smart.get("document_type") or {}).items() if k != "unclassified"}
    if not container.get("exists"):
        return "not-provisioned"
    if not raw.get("chunks") or not has("simple_rag"):
        return "provisioning-incomplete"
    if not container.get("classified"):
        return "baseline-ready"
    if not smart_types:
        return "classified-not-indexed"
    if not has("smart_rag"):
        return "smart-index-ready"
    return "complete" if has("metadata_miner") else "complete-local-miner"


def manifest(env: Environment, names: ParticipantNames, state: dict[str, Any]) -> dict[str, Any]:
    search_id = env.search_service_id
    project_ep = env.resolved_project_endpoint()
    return {
        "workshop": WORKSHOP_TAG,
        "participant": names.hackername,
        "note": "hackername is a namespace label, not a security boundary",
        "generated_at": state.get("generated_at"),
        "stage": state.get("stage"),
        "shared": {
            "owner": "facilitator",
            "subscription_id": env.subscription_id,
            "resource_group": env.resource_group,
            "foundry_account_id": env.foundry_account_id,
            "project_endpoint": project_ep,
            "search_service_id": search_id,
            "search_endpoint": env.resolved_search_endpoint(),
            "search_sku": env.search_sku,
            "storage_account_id": env.resolved_storage_account_id(),
            "search_connection": env.search_connection_name,
            "deployments": {"chat": env.chat_deployment, "embedding": env.embedding_deployment,
                            "decision": env.decision_deployment, "embedding_model": env.embedding_model},
        },
        "participant_owned": {
            "container": {"id": f"{env.resolved_storage_account_id()}/blobServices/default/containers/"
                                f"{names.container}", **state.get("container", {})},
            "search": {k: state.get(k) for k in ("datasource", "raw_index", "raw_indexer", "smart_index",
                                                 "smart_indexer")},
            "agents": state.get("agents"),
            "run_dir": names.run_dir,
        },
    }


def write_manifest(env: Environment, names: ParticipantNames, state: dict[str, Any]) -> Path:
    path = run_dir(names) / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest(env, names, state), indent=2, default=str), encoding="utf-8")
    return path


def render_status(state: dict[str, Any]) -> str:
    def mark(ok: bool) -> str:
        return "ok " if ok else "-- "

    c = state.get("container", {})
    lines = [f"participant {state['participant']}  stage: {state.get('stage')}"]
    lines.append(f"  {mark(c.get('exists'))}container      {c.get('name')}  "
                 f"{c.get('documents', 0)} docs, {c.get('classified', 0)} classified")
    for key in ("raw_index", "smart_index"):
        s = state.get(key, {})
        extra = f"{s.get('documents', 0)} docs / {s.get('chunks', 0)} chunks" if s.get("exists") else ""
        if s.get("document_type"):
            extra += "  " + ", ".join(f"{k}={v}" for k, v in sorted(s["document_type"].items()))
        lines.append(f"  {mark(bool(s.get('exists')))}{key:<14} {s.get('name')}  {extra}")
    for key in ("raw_indexer", "smart_indexer"):
        s = state.get(key, {})
        extra = ""
        if s.get("exists"):
            extra = ("disabled (pending) " if s.get("disabled") else "") + f"last run: {s.get('status')}"
        lines.append(f"  {mark(bool(s.get('exists')))}{key:<14} {s.get('name')}  {extra}")
    for key, a in (state.get("agents") or {}).items():
        if not isinstance(a, dict):
            continue
        extra = f"v{a.get('version')} {a.get('kind', '')} {a.get('status', '')}".strip() if a.get("exists") else ""
        lines.append(f"  {mark(bool(a.get('exists')))}{key:<14} {a.get('name')}  {extra}")
    return "\n".join(lines)
