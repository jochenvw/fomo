"""Foundry IQ knowledge source + knowledge base for Smart RAG (Search data plane, preview API).

Smart RAG does not hardcode a document type. The knowledge source carries a `document_type`
query hint whose values and definitions come from the Metadata Miner's classifier; the knowledge
base's query planner (the chat model) derives the filter per question. One knowledge source and one
knowledge base per participant, over the participant's smart index. Simple RAG does not use them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from ..azclient import SCOPE_SEARCH, request
from ..config import Environment
from ..errors import ConfigError
from ..naming import ParticipantNames
from .schema import SEMANTIC_CONFIG

KB_API_VERSION = "2026-11-01-preview"
FILTER_FIELD = "document_type"
FILTER_INSTRUCTIONS_MAX = 1024
REASONING_EFFORTS = ("low", "medium")  # query hints need low or higher

FILTER_HEADER = "Filter on the single document type that holds the authoritative answer. Types: "
RETRIEVAL_INSTRUCTIONS = (
    "Every query plan must first decide which single document_type holds the authoritative answer, using "
    "the document type definitions in the document_type filter hint, and apply it as a document_type filter "
    "on every subquery. Use no filter only when no single type fits."
)


def filter_instructions(scopes: dict[str, str], limit: int = FILTER_INSTRUCTIONS_MAX) -> str:
    """Classifier definitions as one hint text within the service limit. Each definition is cut at a
    word boundary to an equal share of the budget, so every routable type keeps its label and meaning."""
    entries = [(label, " ".join(text.split())) for label, text in scopes.items()]
    overhead = len(FILTER_HEADER) + sum(len(label) + 4 for label, _ in entries)
    share = max(20, (limit - overhead) // max(len(entries), 1))
    parts = []
    for label, text in entries:
        if len(text) > share:
            text = text[: share - 1].rsplit(" ", 1)[0].rstrip(",;:(") + "…"
        parts.append(f"{label}: {text}")
    return (FILTER_HEADER + "; ".join(parts))[:limit]


def knowledge_source_definition(names: ParticipantNames, scopes: dict[str, str]) -> dict[str, Any]:
    return {
        "name": names.knowledge_source,
        "kind": "searchIndex",
        "description": f"Contoso Oil documents classified by type ({names.hackername})",
        "searchIndexParameters": {
            "searchIndexName": names.smart_index,
            "semanticConfigurationName": SEMANTIC_CONFIG,
            "sourceDataFields": [{"name": "doc_id"}, {"name": "title"}, {"name": FILTER_FIELD}],
            "searchFields": [{"name": "content"}],
            "queryHints": {"filters": [{
                "field": FILTER_FIELD,
                "fieldValues": list(scopes),
                "filterInstructions": filter_instructions(scopes),
            }]},
        },
    }


def knowledge_base_definition(env: Environment, names: ParticipantNames) -> dict[str, Any]:
    if env.kb_reasoning_effort not in REASONING_EFFORTS:
        raise ConfigError(f"kb_reasoning_effort '{env.kb_reasoning_effort}' is not supported",
                          fix=f"set FOMO_KB_REASONING_EFFORT to one of {', '.join(REASONING_EFFORTS)} "
                              "(document-type query hints need low or higher)")
    return {
        "name": names.knowledge_base,
        "description": f"Smart RAG knowledge base for {names.hackername}",
        "knowledgeSources": [{"name": names.knowledge_source}],
        # Query planning runs on the workshop chat deployment with the Search managed identity
        # (Cognitive Services OpenAI User on the Foundry account, granted by infra).
        "models": [{"kind": "azureOpenAI", "azureOpenAIParameters": {
            "resourceUri": env.resolved_openai_endpoint(),
            "deploymentId": env.chat_deployment,
            "modelName": env.chat_model or env.chat_deployment,
        }}],
        "retrievalReasoningEffort": {"kind": env.kb_reasoning_effort},
        "retrievalInstructions": RETRIEVAL_INSTRUCTIONS,
        "outputMode": "extractiveData",
    }


def mcp_url(env: Environment, knowledge_base: str) -> str:
    return (f"{env.resolved_search_endpoint().rstrip('/')}/knowledgebases/{knowledge_base}/mcp"
            f"?api-version={KB_API_VERSION}")


def source_documents(output: Any) -> list[dict[str, Any]]:
    """Source fields (chunk_id, doc_id, title, document_type) of each chunk in a `knowledge_base_retrieve`
    MCP output: `{"documents": [{"content": "<json of the source fields>", ...}]}`."""
    try:
        data = json.loads(output) if isinstance(output, str) else output
    except ValueError:
        return []
    docs: list[dict[str, Any]] = []
    for item in (data or {}).get("documents", []) if isinstance(data, dict) else []:
        content = item.get("content")
        try:
            fields = json.loads(content) if isinstance(content, str) else content
        except ValueError:
            fields = None
        if isinstance(fields, dict) and fields.get("doc_id"):
            docs.append({k: fields.get(k) for k in ("chunk_id", "doc_id", "title", FILTER_FIELD)})
    return docs


@dataclass
class Retrieval:
    """Knowledge base retrieve with activity: what the query planner did for one question."""

    filters: list[str | None] = field(default_factory=list)  # filter per search subquery
    doc_ids: list[str] = field(default_factory=list)
    reasoning_tokens: int = 0


class KnowledgeClient:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.endpoint = env.resolved_search_endpoint().rstrip("/")

    def _url(self, kind: str, name: str | None = None, action: str = "") -> str:
        target = f"{kind}('{quote(name)}')" if name else kind
        return f"{self.endpoint}/{target}{action}?api-version={KB_API_VERSION}"

    def get(self, kind: str, name: str) -> dict[str, Any] | None:
        resp = request("GET", self._url(kind, name), scope=SCOPE_SEARCH, ok=(200, 404))
        return resp.json() if resp.status_code == 200 else None

    def put(self, kind: str, body: dict[str, Any]) -> None:
        request("PUT", self._url(kind, body["name"]), scope=SCOPE_SEARCH, json_body=body, ok=(200, 201, 204),
                headers={"Prefer": "return=representation"})

    def delete(self, kind: str, name: str) -> bool:
        resp = request("DELETE", self._url(kind, name), scope=SCOPE_SEARCH, ok=(204, 404))
        return resp.status_code == 204

    def list_names(self, kind: str) -> list[str]:
        resp = request("GET", self._url(kind), scope=SCOPE_SEARCH, params={"$select": "name"})
        return [item["name"] for item in resp.json().get("value", [])]

    def ensure(self, names: ParticipantNames, scopes: dict[str, str]) -> None:
        """Idempotent: knowledge source first (the knowledge base references it)."""
        self.put("knowledgesources", knowledge_source_definition(names, scopes))
        self.put("knowledgebases", knowledge_base_definition(self.env, names))

    def remove(self, names: ParticipantNames) -> list[str]:
        """Knowledge base before its knowledge source. Returns what was deleted."""
        done = []
        for kind, name in (("knowledgebases", names.knowledge_base), ("knowledgesources", names.knowledge_source)):
            if self.delete(kind, name):
                done.append(f"{kind}/{name}")
        return done

    def retrieve(self, names: ParticipantNames, question: str) -> Retrieval:
        body = {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
                "includeActivity": True,
                "knowledgeSourceParams": [{"knowledgeSourceName": names.knowledge_source, "kind": "searchIndex",
                                           "includeReferences": True, "includeReferenceSourceData": True}]}
        data = request("POST", self._url("knowledgebases", names.knowledge_base, "/retrieve"), scope=SCOPE_SEARCH,
                       json_body=body, ok=(200, 206)).json()
        out = Retrieval()
        for step in data.get("activity", []):
            if step.get("type") == "searchIndex":
                arguments = step.get("searchIndexArguments") or {}
                out.filters.append((arguments.get("filter") or "").strip() or None)
            out.reasoning_tokens += int(step.get("reasoningTokens") or 0)
        for ref in data.get("references", []):
            doc_id = (ref.get("sourceData") or {}).get("doc_id")
            if doc_id and doc_id not in out.doc_ids:
                out.doc_ids.append(doc_id)
        return out


def service_usage(env: Environment) -> dict[str, int]:
    """Objects in use on the Search service, keyed like `capacity.PER_PARTICIPANT`. Knowledge sources and
    knowledge bases are counted by listing (preview API); unreadable counts are reported as 0."""
    from .client import SearchClient

    stats = SearchClient(env).service_stats().get("counters", {})
    usage = {"indexes": stats.get("indexesCount", {}).get("usage", 0),
             "indexers": stats.get("indexersCount", {}).get("usage", 0),
             "skillsets": stats.get("skillsetCount", {}).get("usage", 0),
             "datasources": stats.get("dataSourcesCount", {}).get("usage", 0)}
    client = KnowledgeClient(env)
    for key, kind in (("knowledge_sources", "knowledgesources"), ("knowledge_bases", "knowledgebases")):
        try:
            usage[key] = len(client.list_names(kind))
        except Exception:
            usage[key] = 0
    return usage
