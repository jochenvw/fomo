"""Abstention policy: thresholds turn low-confidence answers into needs_review."""

from __future__ import annotations

from dataclasses import dataclass

from .client import DecisionResult
from .config import NEEDS_REVIEW, ClassifierConfig


@dataclass(frozen=True)
class PolicyOutcome:
    label: str  # final stored document_type
    model_choice: str
    probability: float | None
    margin: float | None
    reason: str  # accepted | abstained | low_probability | low_margin | invalid_label


def apply_policy(result: DecisionResult, cfg: ClassifierConfig) -> PolicyOutcome:
    choice = result.choice
    prob = result.probability_of(choice)
    margin = result.margin()
    if choice == cfg.abstain_label:
        return PolicyOutcome(NEEDS_REVIEW, choice, prob, margin, "abstained")
    if choice not in cfg.labels:
        return PolicyOutcome(NEEDS_REVIEW, choice, prob, margin, "invalid_label")
    if prob is not None and prob < cfg.min_probability:
        return PolicyOutcome(NEEDS_REVIEW, choice, prob, margin, "low_probability")
    if margin is not None and margin < cfg.min_margin:
        return PolicyOutcome(NEEDS_REVIEW, choice, prob, margin, "low_margin")
    return PolicyOutcome(choice, choice, prob, margin, "accepted")
