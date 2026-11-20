"""Capacity and cost math for N participants (pure functions; unit tested).

Search object limits: https://learn.microsoft.com/en-us/azure/search/search-limits-quotas-capacity
(checked 2026-11; Basic shows "5 or 15" - 15 for services created in newer regions/after 2024).
Prices are USD list-price snapshots for estimates only; verify on https://azure.microsoft.com/pricing.
Unknown prices are None and reported as "unknown", never guessed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

# Objects one participant owns on the shared Search service. Smart RAG adds one Foundry IQ knowledge
# source + knowledge base (smart index only; Simple RAG uses none).
PER_PARTICIPANT = {"indexes": 2, "indexers": 2, "skillsets": 2, "datasources": 1,
                   "knowledge_sources": 1, "knowledge_bases": 1}


def _limits(n: int) -> dict[str, int]:
    """Knowledge sources and knowledge bases share the index limit per tier."""
    return {k: n for k in PER_PARTICIPANT}


SEARCH_LIMITS: dict[str, dict[str, int]] = {
    "free": _limits(3), "basic": _limits(15), "standard": _limits(50),
    "standard2": _limits(200), "standard3": _limits(200),
}

# USD per search unit per month (approximate list price, 2026 snapshot).
SEARCH_USD_MONTH = {"free": 0.0, "basic": 75.0, "standard": 250.0, "standard2": 1000.0, "standard3": 2000.0}

# USD per 1M tokens (approximate global list price snapshot). None = not published / unknown.
MODEL_USD_PER_M = {
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "decision-1-1": None,
}

# Measured on the reference corpus (25 docs) during validation; see docs/cost-capacity-cleanup.md.
CORPUS_DOCS = 25
DECISION_INPUT_TOKENS_PER_DOC = 1000
EMBEDDING_TOKENS_PER_INDEX = 12_000
AGENT_TOKENS_PER_QUESTION = {"input": 2_900, "output": 1_600}
# Smart RAG knowledge base query planning on the same chat deployment, per question at effort
# medium (measured: 2 planning passes, ~4.8k input + ~0.3k output; effort low ~ half).
KB_PLANNING_TOKENS_PER_QUESTION = {"input": 4_800, "output": 300}
# Smart RAG knowledge base: each question runs 1-3 semantically reranked subqueries. ~100 per participant
# covers smoke tests, `fomo eval`, both Foundry eval suites and ad-hoc questions.
SEMANTIC_QUERIES_PER_PARTICIPANT = 100
SEMANTIC_FREE_QUERIES = 1000  # per month on the semantic ranker 'free' plan


@dataclass
class CapacityReport:
    participants: int
    search_sku: str
    required: dict[str, int]
    limits: dict[str, int]
    current: dict[str, int] = field(default_factory=dict)
    headroom: dict[str, int] = field(default_factory=dict)
    fits: bool = True
    participants_max: int = 0
    recommended_sku: str = ""
    services_needed_at_current_sku: int = 1
    cost: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def required_objects(participants: int) -> dict[str, int]:
    return {k: v * participants for k, v in PER_PARTICIPANT.items()}


def max_participants(sku: str) -> tuple[int, str]:
    limits = SEARCH_LIMITS[sku]
    per = {k: limits[k] // v for k, v in PER_PARTICIPANT.items()}
    binding = min(per, key=lambda k: per[k])
    return per[binding], binding


def recommend_sku(participants: int, current: dict[str, int] | None = None) -> str:
    for sku in ("basic", "standard", "standard2"):
        cap, _ = max_participants(sku)
        used = max((current or {}).get(k, 0) // max(v, 1) for k, v in PER_PARTICIPANT.items())
        if cap - used >= participants:
            return sku
    return "standard2 (multiple services)"


def cost_estimate(participants: int, *, search_sku: str, hours: float = 8.0, agent_questions: int = 10,
                  miner_runs: int = 3) -> dict[str, Any]:
    """Per-event estimate. Shared Search is billed hourly for the whole service."""
    search_hour = SEARCH_USD_MONTH.get(search_sku, 0.0) / 730.0
    chat = MODEL_USD_PER_M["gpt-5-mini"]
    emb = MODEL_USD_PER_M["text-embedding-3-small"]
    chat_usd = participants * agent_questions * (
        AGENT_TOKENS_PER_QUESTION["input"] * chat["input"] + AGENT_TOKENS_PER_QUESTION["output"] * chat["output"]
    ) / 1e6 if chat else None
    emb_usd = participants * 2 * 2 * EMBEDDING_TOKENS_PER_INDEX * emb["input"] / 1e6 if emb else None
    decision_tokens = participants * miner_runs * CORPUS_DOCS * DECISION_INPUT_TOKENS_PER_DOC
    known = [x for x in (chat_usd, emb_usd) if x is not None]
    return {
        "search_usd": round(search_hour * hours, 2),
        "search_hours": hours,
        "chat_usd": round(chat_usd, 2) if chat_usd is not None else None,
        "embedding_usd": round(emb_usd, 4) if emb_usd is not None else None,
        "decision_tokens": decision_tokens,
        "decision_usd": "unknown (check Foundry pricing for Microsoft-Decision)",
        "hosted_agents": "billed per session compute; check Foundry Agent Service pricing",
        "total_known_usd": round(search_hour * hours + sum(known), 2),
        "assumptions": {
            "agent_questions_per_participant": agent_questions,
            "miner_runs_per_participant": miner_runs,
            "embedding_passes": "2 indexes x (index + reindex)",
            "prices": "USD list-price snapshot; verify before the event",
        },
    }


def capacity_report(participants: int, search_sku: str, *, current: dict[str, int] | None = None,
                    replicas: int = 1, hours: float = 8.0) -> CapacityReport:
    sku = search_sku.lower()
    if sku not in SEARCH_LIMITS:
        raise ValueError(f"unknown Search SKU {search_sku}")
    current = current or {}
    limits = SEARCH_LIMITS[sku]
    required = required_objects(participants)
    headroom = {k: limits[k] - current.get(k, 0) - required[k] for k in PER_PARTICIPANT}
    cap, binding = max_participants(sku)
    rep = CapacityReport(
        participants=participants, search_sku=sku, required=required, limits=limits, current=current,
        headroom=headroom, fits=all(v >= 0 for v in headroom.values()), participants_max=cap,
        recommended_sku=recommend_sku(participants, current),
        services_needed_at_current_sku=max(1, math.ceil(participants / max(cap, 1))),
        cost=cost_estimate(participants, search_sku=sku, hours=hours),
    )
    rep.notes.append(f"{sku}: max {cap} participants (binding: {binding}, {PER_PARTICIPANT[binding]} each)")
    rep.notes.append(f"Foundry: {participants * 2} prompt agents + {participants} hosted agents; "
                     "agents do not count against Search limits")
    if replicas < 2 and participants > 10:
        rep.notes.append("1 replica: no query SLA; indexing and 40 parallel agents share one replica. "
                         "Stagger provision start times (see facilitator runbook) and consider 2 replicas")
    if not rep.fits:
        rep.notes.append("does NOT fit: use a larger SKU or split cohorts across services. "
                         "Never upgrade a shared service silently")
    return rep


def render(rep: CapacityReport) -> str:
    lines = [f"{rep.participants} participants on Search '{rep.search_sku}': "
             + ("FITS" if rep.fits else "DOES NOT FIT")]
    lines.append(f"  {'object':<18}{'need':>6}{'in use':>8}{'limit':>7}{'headroom':>10}")
    for k in PER_PARTICIPANT:
        lines.append(f"  {k:<18}{rep.required[k]:>6}{rep.current.get(k, 0):>8}{rep.limits[k]:>7}{rep.headroom[k]:>10}")
    lines.append(f"  max participants at this SKU: {rep.participants_max}; recommended SKU: {rep.recommended_sku}")
    c = rep.cost
    lines.append(f"  cost (~{c['search_hours']:.0f} h, list prices): Search ${c['search_usd']}, chat ${c['chat_usd']}, "
                 f"embeddings ${c['embedding_usd']}, known total ~${c['total_known_usd']}; "
                 f"decision: {c['decision_tokens']:,} input tokens ({c['decision_usd']})")
    lines += [f"  - {n}" for n in rep.notes]
    return "\n".join(lines)
