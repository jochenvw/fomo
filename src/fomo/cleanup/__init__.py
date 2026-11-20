"""Reset and cleanup of ONE participant's workshop-owned assets.

Never touches shared/facilitator resources: Foundry account/project, model deployments,
Search service, storage account, shared Search project connection. The participant's own
knowledge base, knowledge source and knowledge base connection are deleted. Inventory first (dry run is the
default), then a typed DELETE confirmation.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Any

from .. import rbac
from ..agents.hosted import HostedMiner, required_grants
from ..agents.prompt import PromptAgents, delete_kb_connection, kb_connection_exists
from ..config import Environment
from ..data.blob import CLASSIFICATION_KEYS
from ..miner.pipeline import CHECKPOINT_BLOB
from ..naming import ParticipantNames
from ..plan import Plan
from ..provision.participant import container_client
from ..search.client import SearchClient
from ..search.knowledge import KnowledgeClient
from ..telemetry.logging import RunLogger

SEARCH_OBJECTS = (
    ("indexers", "raw_indexer"), ("indexers", "smart_indexer"),
    ("skillsets", "raw_skillset"), ("skillsets", "smart_skillset"),
    ("indexes", "raw_index"), ("indexes", "smart_index"),
    ("datasources", "datasource"),
)  # fmt: skip


def _miner_principal(env: Environment, names: ParticipantNames) -> str | None:
    agent = HostedMiner(env, names).current()
    if agent is None:
        return None
    ident = getattr(agent, "instance_identity", None) or (agent.get("instance_identity")
                                                          if isinstance(agent, dict) else None)
    if ident is None:
        latest = getattr(getattr(agent, "versions", None), "latest", None)
        ident = getattr(latest, "instance_identity", None)
    return getattr(ident, "principal_id", None) or (ident.get("principal_id") if isinstance(ident, dict) else None)


def inventory(env: Environment, names: ParticipantNames) -> dict[str, Any]:
    search = SearchClient(env)
    agents = PromptAgents(env)
    cont = container_client(env, names)
    inv: dict[str, Any] = {"search": [], "agents": [], "container": None, "grants": [], "local": None,
                           "connection": None}
    knowledge = KnowledgeClient(env)
    for kind, name in (("knowledgebases", names.knowledge_base), ("knowledgesources", names.knowledge_source)):
        with contextlib.suppress(Exception):
            if knowledge.get(kind, name) is not None:
                inv["search"].append(f"{kind}/{name}")
    for kind, attr in SEARCH_OBJECTS:
        name = getattr(names, attr)
        if search.get(kind, name) is not None:
            inv["search"].append(f"{kind}/{name}")
    for name in (names.simple_rag_agent, names.smart_rag_agent, names.metadata_miner_agent,
                 *names.eval_route_agents):
        if agents.exists(name):
            inv["agents"].append(name)
    if kb_connection_exists(env, names):
        inv["connection"] = names.knowledge_base_connection
    if cont.exists():
        inv["container"] = {"name": names.container, "blobs": sum(1 for _ in cont.list())}
    principal = _miner_principal(env, names) if names.metadata_miner_agent in inv["agents"] else None
    if principal:
        inv["grants"] = [{"role": g.role, "scope": g.scope, "principal_id": g.principal_id}
                         for g in required_grants(env, names, principal)]
    if Path(names.run_dir).exists():
        inv["local"] = names.run_dir
    return inv


def plan_cleanup(env: Environment, names: ParticipantNames, inv: dict[str, Any], *, local: bool) -> Plan:
    p = Plan("cleanup", names.hackername, env.subscription_id, env.resource_group, destructive=True)
    for g in inv["grants"]:
        p.add("delete", f"role assignment {g['role']}", f"agent identity {g['principal_id']} at {g['scope']}")
    for name in inv["agents"]:
        p.add("delete", f"agent {name}", "all versions")
    if inv.get("connection"):
        p.add("delete", f"project connection {inv['connection']}", "Smart RAG knowledge base MCP connection")
    for obj in inv["search"]:
        p.add("delete", f"search {obj}")
    if inv["container"]:
        p.add("delete", f"blob container {inv['container']['name']}",
              f"{inv['container']['blobs']} documents + miner checkpoints")
    if local and inv["local"]:
        p.add("delete", f"local folder {inv['local']}", "logs, traces, your classifier.yaml edits")
    p.notes.append("shared resources are out of scope and never deleted by this command")
    if not local and inv["local"]:
        p.notes.append(f"local folder {inv['local']} kept (add --local to remove it)")
    return p


def apply_cleanup(env: Environment, names: ParticipantNames, log: RunLogger, inv: dict[str, Any], *,
                  local: bool) -> dict[str, Any]:
    done: dict[str, Any] = {"deleted": [], "failed": []}

    def record(ok: bool, what: str) -> None:
        (done["deleted"] if ok else done["failed"]).append(what)
        log.info(f"{'deleted' if ok else 'FAILED to delete'} {what}")

    for g in inv["grants"]:
        record(rbac.delete(rbac.Grant(g["role"], g["scope"], g["principal_id"])), f"grant {g['role']}")
    agents = PromptAgents(env)
    for name in inv["agents"]:
        try:
            # force: hosted agents keep idle sessions for a while; deleting them is the point of cleanup
            agents.client.agents.delete(name, force=True if name == names.metadata_miner_agent else None)
            record(True, f"agent {name}")
        except Exception as exc:
            log.error(f"agent {name}: {exc}"[:500])
            record(False, f"agent {name}")
    if inv.get("connection"):
        record(delete_kb_connection(env, names), f"connection {inv['connection']}")
    search = SearchClient(env)
    knowledge = KnowledgeClient(env)
    for obj in inv["search"]:  # knowledge base -> knowledge source -> indexers -> ... -> indexes
        kind, name = obj.split("/", 1)
        client = knowledge if kind.startswith("knowledge") else search
        record(client.delete(kind, name), f"search {obj}")
    if inv["container"]:
        try:
            container_client(env, names).delete_container()
            record(True, f"container {names.container}")
        except Exception as exc:
            log.error(str(exc))
            record(False, f"container {names.container}")
    if local and inv["local"]:
        log.detach_file()
        shutil.rmtree(inv["local"], ignore_errors=True)
        record(not Path(inv["local"]).exists(), f"local {inv['local']}")
    return done


# ---- reset -----------------------------------------------------------------------------------
def plan_reset(env: Environment, names: ParticipantNames, scope: str) -> Plan:
    p = Plan("reset-participant", names.hackername, env.subscription_id, env.resource_group, destructive=True)
    if scope in ("metadata", "all"):
        p.add("reset", f"blob metadata in {names.container}",
              "remove classification keys (" + ", ".join(CLASSIFICATION_KEYS[:3]) + ", ...) and the miner "
              "checkpoint; source documents untouched")
        p.add("update", f"indexer {names.smart_indexer}", "disable (back to pending)")
    if scope in ("indexes", "all"):
        p.add("reset", f"indexers {names.raw_indexer}, {names.smart_indexer}",
              "reset change tracking and re-run (full rebuild of both indexes)", cost="re-embeds the corpus")
    p.notes.append("agents and the container stay; other participants are unaffected")
    return p


def apply_reset(env: Environment, names: ParticipantNames, log: RunLogger, scope: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    search = SearchClient(env)
    if scope in ("metadata", "all"):
        cont = container_client(env, names)
        stripped = 0
        for info in cont.list():
            if {k.lower() for k in info.metadata} & set(CLASSIFICATION_KEYS):
                cont.remove_metadata_keys(info.name, CLASSIFICATION_KEYS)
                stripped += 1
        with contextlib.suppress(Exception):
            cont.delete_blob(CHECKPOINT_BLOB)
        log.info(f"removed classification metadata from {stripped} blob(s)")
        if search.get("indexers", names.smart_indexer) is not None:
            search.ensure_participant(names, smart=True, pending=True)
            log.info(f"{names.smart_indexer} disabled (pending)")
        out["metadata_reset"] = stripped
    if scope in ("indexes", "all"):
        runs = {}
        for ixr in (names.raw_indexer, names.smart_indexer):
            body = search.get("indexers", ixr)
            if body is None or body.get("disabled"):
                continue
            run = search.run_and_wait(ixr, reset=True)
            runs[ixr] = run.status
            log.info(f"{ixr}: reset + run -> {run.status} ({run.items_processed} items)")
        out["indexers"] = runs
    return out
