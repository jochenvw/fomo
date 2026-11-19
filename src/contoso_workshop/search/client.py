"""Search data plane via REST (Entra ID only; local auth is disabled on the service)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from ..azclient import SCOPE_SEARCH, request
from ..config import Environment
from ..errors import SearchError
from ..naming import ParticipantNames
from .schema import API_VERSION, datasource_definition, participant_definitions

TERMINAL = {"success", "transientFailure", "persistentFailure", "reset"}


@dataclass
class IndexerRun:
    name: str
    status: str
    items_processed: int = 0
    items_failed: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "success" and self.items_failed == 0


class SearchClient:
    def __init__(self, env: Environment) -> None:
        self.endpoint = env.resolved_search_endpoint().rstrip("/")
        self.env = env

    # ---- generic -----------------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.endpoint}/{path}?api-version={API_VERSION}"

    def _call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200, 201, 202, 204),
              **kw: Any) -> Any:
        resp = request(method, self._url(path), scope=SCOPE_SEARCH, json_body=body, ok=ok, **kw)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def get(self, kind: str, name: str) -> dict[str, Any] | None:
        resp = request("GET", self._url(f"{kind}/{quote(name)}"), scope=SCOPE_SEARCH, ok=(200, 404))
        return resp.json() if resp.status_code == 200 else None

    def put(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        result = self._call("PUT", f"{kind}/{quote(body['name'])}", body,
                            headers={"Prefer": "return=representation"})
        return result or body

    def delete(self, kind: str, name: str) -> bool:
        resp = request("DELETE", self._url(f"{kind}/{quote(name)}"), scope=SCOPE_SEARCH, ok=(204, 404))
        return resp.status_code == 204

    def list_names(self, kind: str) -> list[str]:
        data = self._call("GET", kind, params={"$select": "name"}) or {}
        return [item["name"] for item in data.get("value", [])]

    def service_stats(self) -> dict[str, Any]:
        return self._call("GET", "servicestats") or {}

    # ---- participant objects -----------------------------------------------------------
    def ensure_participant(self, names: ParticipantNames, *, smart: bool, pending: bool = False) -> dict[str, str]:
        """Idempotent create-or-update of datasource + index + skillset + indexer.
        `pending` creates the indexer disabled (smart index plan before classification)."""
        results: dict[str, str] = {}
        ds = datasource_definition(self.env, names)
        results[ds["name"]] = "exists" if self.get("datasources", ds["name"]) else "created"
        self.put("datasources", ds)
        defs = participant_definitions(self.env, names, smart=smart, pending=pending)
        for kind, key in (("indexes", "index"), ("skillsets", "skillset"), ("indexers", "indexer")):
            body = defs[key]
            existed = self.get(kind, body["name"]) is not None
            self.put(kind, body)
            results[body["name"]] = "updated" if existed else "created"
        return results

    def run_indexer(self, name: str, *, reset: bool = False) -> bool:
        """Request a run. Returns False when a run is already in progress (HTTP 409)."""
        if reset:
            self._call("POST", f"indexers/{quote(name)}/reset")
        resp = request("POST", self._url(f"indexers/{quote(name)}/run"), scope=SCOPE_SEARCH,
                       ok=(202, 409))
        return resp.status_code == 202

    def run_and_wait(self, name: str, *, reset: bool = False, timeout: float = 900) -> IndexerRun:
        # 10 s slack for clock skew between this machine and the Search service.
        since = (datetime.now(UTC) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%S")
        started = self.run_indexer(name, reset=reset)
        return self.wait_indexer(name, started_after=since if started else None, timeout=timeout)

    def indexer_status(self, name: str) -> dict[str, Any]:
        return self._call("GET", f"indexers/{quote(name)}/status") or {}

    def wait_indexer(self, name: str, *, started_after: str | None = None, timeout: float = 600,
                     poll: float = 5) -> IndexerRun:
        """Poll until the latest execution (started after `started_after`, ISO) is terminal."""
        t0 = time.monotonic()
        while True:
            st = self.indexer_status(name)
            last = st.get("lastResult") or {}
            status = last.get("status") or "none"
            fresh = started_after is None or (last.get("startTime") or "") >= started_after
            if fresh and status in TERMINAL:
                return IndexerRun(
                    name=name,
                    status=status,
                    items_processed=int(last.get("itemsProcessed") or 0),
                    items_failed=int(last.get("itemsFailed") or 0),
                    errors=[e.get("errorMessage", "") for e in last.get("errors") or []][:10],
                    warnings=[w.get("message", "") for w in last.get("warnings") or []][:10],
                    seconds=time.monotonic() - t0,
                )
            if time.monotonic() - t0 > timeout:
                raise SearchError(f"indexer {name} did not finish within {timeout:.0f}s (status {status})",
                                  fix="check the indexer in the portal or `fomo status`; rerun later")
            time.sleep(poll)

    def count(self, index: str, filter_: str | None = None) -> int:
        body: dict[str, Any] = {"search": "*", "count": True, "top": 0}
        if filter_:
            body["filter"] = filter_
        data = self._call("POST", f"indexes/{quote(index)}/docs/search", body)
        return int(data.get("@odata.count", 0))

    def wait_for_count(self, index: str, filter_: str | None = None, *, timeout: float = 60,
                       poll: float = 3) -> int:
        """Indexer success precedes query visibility by a few seconds; wait until >0 or timeout."""
        deadline = time.monotonic() + timeout
        while True:
            n = self.count(index, filter_)
            if n > 0 or time.monotonic() >= deadline:
                return n
            time.sleep(poll)

    def facets(self, index: str, field_name: str) -> dict[str, int]:
        body = {"search": "*", "top": 0, "facets": [f"{field_name},count:50"]}
        data = self._call("POST", f"indexes/{quote(index)}/docs/search", body)
        return {f["value"]: f["count"] for f in data.get("@search.facets", {}).get(field_name, [])}

    def query(self, index: str, text: str, *, top: int = 5, filter_: str | None = None,
              hybrid: bool = True, select: str = "chunk_id,parent_id,doc_id,title,url,content",
              scoring_profile: str | None = None, scoring_parameters: list[str] | None = None,
              ) -> list[dict[str, Any]]:
        """Same query shape the Foundry tool uses for vector_simple_hybrid: BM25 + vector, RRF."""
        body: dict[str, Any] = {"search": text, "top": top, "select": select, "queryType": "simple"}
        if hybrid:
            body["vectorQueries"] = [{"kind": "text", "text": text, "fields": "content_vector", "k": 50}]
        if filter_:
            body["filter"] = filter_
        if scoring_profile:
            body["scoringProfile"] = scoring_profile
            body["scoringParameters"] = scoring_parameters or []
        data = self._call("POST", f"indexes/{quote(index)}/docs/search", body)
        return list(data.get("value", []))

    def delete_participant(self, names: ParticipantNames) -> list[str]:
        removed = []
        for kind, name in (
            ("indexers", names.raw_indexer), ("indexers", names.smart_indexer),
            ("skillsets", names.raw_skillset), ("skillsets", names.smart_skillset),
            ("indexes", names.raw_index), ("indexes", names.smart_index),
            ("datasources", names.datasource),
        ):
            if self.delete(kind, name):
                removed.append(f"{kind}/{name}")
        return removed
