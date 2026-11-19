"""Hosted Metadata Miner entry point (Foundry hosted agent, invocations protocol 2.0.0).

Packaged into the source ZIP by `fomo.agents.hosted.build_zip` together with the
`fomo` modules the miner needs. Same pipeline code as the local developer mode.

One agent per participant. The target container is fixed by environment variables set at
deploy time, and the agent identity only holds Storage Blob Data Contributor on that one
container, so a request can never reach another participant's data.

POST /invocations  {"mode": "delta"|"all", "only": [...], "limit": n, "dry_run": false}
-> run summary JSON (counts, labels, per-item results; never document text).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from fomo.azclient import credential
from fomo.data.blob import ParticipantContainer
from fomo.decision.client import DecisionClient
from fomo.decision.config import load_classifier_config
from fomo.errors import PermissionDenied, WorkshopError
from fomo.miner.pipeline import Miner

log = logging.getLogger("metadata-miner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PARTICIPANT = os.environ["FOMO_PARTICIPANT"]
CONTAINER = os.environ["FOMO_CONTAINER"]
BLOB_ENDPOINT = os.environ["FOMO_BLOB_ENDPOINT"]
DECISION_ENDPOINT = os.environ["FOMO_DECISION_ENDPOINT"]
DECISION_DEPLOYMENT = os.environ.get("FOMO_DECISION_DEPLOYMENT", "decision-1-1")
MAX_LIMIT = 500

app = InvocationAgentServerHost()


def _miner() -> Miner:
    return Miner(
        ParticipantContainer(BLOB_ENDPOINT, CONTAINER, credential()),
        DecisionClient(DECISION_ENDPOINT, DECISION_DEPLOYMENT),
        load_classifier_config(),
        participant=PARTICIPANT,
        concurrency=int(os.environ.get("FOMO_CONCURRENCY", "4")),
        requests_per_second=float(os.environ.get("FOMO_RPS", "5")),
        log=lambda msg, **kw: log.info("%s | %s", PARTICIPANT.upper(), msg),
    )


@app.invoke_handler
async def handle(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    mode = str(body.get("mode", "delta"))
    only = body.get("only") or None
    limit = body.get("limit")
    if only is not None and (not isinstance(only, list) or not all(isinstance(x, str) for x in only)):
        return JSONResponse({"error": "'only' must be a list of blob names or doc ids"}, status_code=400)
    if limit is not None and (not isinstance(limit, int) or not 0 < limit <= MAX_LIMIT):
        return JSONResponse({"error": f"'limit' must be 1..{MAX_LIMIT}"}, status_code=400)
    log.info("run participant=%s mode=%s only=%s limit=%s", PARTICIPANT, mode, only, limit)
    try:
        report = await asyncio.to_thread(
            _miner().run, mode=mode, only=only, limit=limit, dry_run=bool(body.get("dry_run", False)))
    except WorkshopError as exc:
        log.error("run failed: %s", exc)
        # Non-transient codes on purpose: callers retry 5xx, and a storage/RBAC denial won't heal by retrying.
        return JSONResponse({"error": str(exc)[:800], "code": type(exc).__name__, "fix": getattr(exc, "fix", None),
                             "participant": PARTICIPANT},
                            status_code=403 if isinstance(exc, PermissionDenied) else 422)
    out = report.to_dict()
    # FOUNDRY_AGENT_NAME is injected by the Foundry hosted runtime; absent when run as a local container.
    out["runtime"] = "foundry-hosted" if os.environ.get("FOUNDRY_AGENT_NAME") else "local-container"
    out["ok"] = report.ok
    return JSONResponse(out, status_code=200)


if __name__ == "__main__":
    app.run()
