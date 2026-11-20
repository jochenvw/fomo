"""Facilitator dashboard: every participant's stage, shared capacity, and collected readiness reports.

Read-only. Participants are discovered from Search index names (`<hackername>-raw`), so the
dashboard needs no registry and shows exactly what exists.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import capacity
from .config import Environment
from .naming import ParticipantNames
from .provision.participant import stage_of, status
from .search.client import SearchClient

GREEN = {"complete", "smart-index-ready"}
YELLOW = {"baseline-ready", "classified-not-indexed", "complete-local-miner"}


def light(stage: str) -> str:
    if stage in GREEN:
        return "green"
    if stage in YELLOW:
        return "yellow"
    return "red"


def discover(env: Environment) -> list[str]:
    names = SearchClient(env).list_names("indexes")
    return sorted({n[: -len("-raw")] for n in names if n.endswith("-raw")})


def readiness(folder: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "checks" not in data:
            continue
        blockers = [f"{c['id']}: {c.get('fix') or c['evidence']}"[:160] for c in data["checks"]
                    if c.get("status") == "BLOCKED"]
        rows.append({"file": path.name, "verdict": data.get("verdict"), "generated_at": data.get("generated_at"),
                     "blockers": blockers})
    return rows


def dashboard(env: Environment, *, readiness_dir: Path | None = None, workers: int = 8,
              agents: bool = True) -> dict[str, Any]:
    hackernames = discover(env)

    def one(h: str) -> dict[str, Any]:
        try:
            st = status(env, ParticipantNames(h), agents=agents)
            stage = stage_of(st)
            c = st.get("container") or {}
            return {"participant": h, "stage": stage, "light": light(stage),
                    "documents": c.get("documents", 0), "classified": c.get("classified", 0),
                    "raw_chunks": (st.get("raw_index") or {}).get("chunks", 0),
                    "smart_chunks": (st.get("smart_index") or {}).get("chunks", 0),
                    "agents": sorted(k for k, v in (st.get("agents") or {}).items() if v.get("exists"))}
        except Exception as exc:
            return {"participant": h, "stage": "error", "light": "red", "error": str(exc)[:200]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, hackernames))
    from .search.knowledge import service_usage
    current = service_usage(env)
    sku = (env.search_sku or "basic").lower()
    cap = capacity.capacity_report(0, sku, current=current)
    out: dict[str, Any] = {
        "participants": rows,
        "summary": {color: sum(1 for r in rows if r["light"] == color) for color in ("green", "yellow", "red")},
        "capacity": {"sku": sku, "in_use": current, "limits": cap.limits, "headroom": cap.headroom,
                     "more_participants_fit": min((cap.headroom[k] // capacity.PER_PARTICIPANT[k]
                                                   for k in capacity.PER_PARTICIPANT), default=0)},
        "cost": capacity.cost_estimate(len(rows), search_sku=sku),
    }
    if readiness_dir is not None:
        out["readiness"] = readiness(readiness_dir)
    return out


def render(d: dict[str, Any]) -> str:
    icon = {"green": "[G]", "yellow": "[Y]", "red": "[R]"}
    lines = [f"{'':4}{'participant':<24}{'stage':<26}{'docs':>5}{'class':>6}{'raw':>6}{'smart':>6}  agents"]
    for r in d["participants"]:
        lines.append(f"{icon[r['light']]:4}{r['participant']:<24}{r['stage']:<26}{r.get('documents', 0):>5}"
                     f"{r.get('classified', 0):>6}{r.get('raw_chunks', 0):>6}{r.get('smart_chunks', 0):>6}  "
                     + (", ".join(r.get("agents", [])) or r.get("error", "")))
    s = d["summary"]
    lines.append(f"\n{len(d['participants'])} participants: {s['green']} green, {s['yellow']} yellow, {s['red']} red")
    c = d["capacity"]
    lines.append(f"Search ({c['sku']}): in use {c['in_use']}, headroom {c['headroom']}; "
                 f"room for {c['more_participants_fit']} more participant(s)")
    cost = d["cost"]
    lines.append(f"estimated event cost (known list prices, {cost['search_hours']:.0f} h): "
                 f"~${cost['total_known_usd']} (Search ${cost['search_usd']}, chat ${cost['chat_usd']}, "
                 f"embeddings ${cost['embedding_usd']}); decision + hosted compute: see Foundry pricing")
    for r in d.get("readiness", []):
        lines.append(f"readiness {r['file']}: {r['verdict']}" + (f" ({'; '.join(r['blockers'][:3])})"
                                                                  if r["blockers"] else ""))
    return "\n".join(lines)
