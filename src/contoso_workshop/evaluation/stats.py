"""Paired statistics for small evaluation sets (pure; unit tested).

Unit of analysis is the question: repeats are averaged per question first, so three noisy
samples of one question never count as three independent observations.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from statistics import fmean, pstdev
from typing import Any

RESAMPLES = 5000
SEED = 2026


def per_question_means(rows: Iterable[dict[str, Any]], metric: str) -> dict[str, float]:
    """{question id: mean over repeats} for rows where `metric` is numeric; missing values are skipped."""
    values: dict[str, list[float]] = {}
    for row in rows:
        value = row.get(metric)
        if isinstance(value, bool):
            value = float(value)
        if isinstance(value, int | float):
            values.setdefault(row["qid"], []).append(float(value))
    return {qid: fmean(v) for qid, v in values.items()}


def repeat_spread(rows: Iterable[dict[str, Any]], metric: str) -> float | None:
    """Mean within-question population standard deviation across repeats (0 = perfectly stable)."""
    values: dict[str, list[float]] = {}
    for row in rows:
        value = row.get(metric)
        if isinstance(value, bool):
            value = float(value)
        if isinstance(value, int | float):
            values.setdefault(row["qid"], []).append(float(value))
    spreads = [pstdev(v) for v in values.values() if len(v) > 1]
    return round(fmean(spreads), 4) if spreads else None


def bootstrap_ci(diffs: Sequence[float], *, resamples: int = RESAMPLES, seed: int = SEED,
                 alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean paired difference, resampling questions."""
    if len(diffs) < 2:
        raise ValueError("need at least two paired questions")
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(resamples))
    low = means[int(alpha / 2 * resamples)]
    high = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    return round(low, 4), round(high, 4)


@dataclass
class Paired:
    metric: str
    baseline: str
    variant: str
    n: int
    baseline_mean: float | None
    variant_mean: float | None
    diff: float | None
    ci_low: float | None
    ci_high: float | None
    wins: int
    ties: int
    losses: int
    verdict: str  # better | worse | inconclusive | insufficient data

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare(metric: str, baseline: str, variant: str, base: dict[str, float], other: dict[str, float], *,
            higher_is_better: bool = True, tie_margin: float = 0.0) -> Paired:
    """Paired comparison over the questions both variants have a value for.

    `wins` counts questions where the variant is better than the baseline by more than `tie_margin`
    in the desirable direction. The verdict is decided by the CI only, never by the win count.
    """
    common = sorted(set(base) & set(other))
    diffs = [other[q] - base[q] for q in common]
    sign = 1 if higher_is_better else -1
    wins = sum(1 for d in diffs if sign * d > tie_margin)
    losses = sum(1 for d in diffs if sign * d < -tie_margin)
    if len(common) < 2:
        return Paired(metric, baseline, variant, len(common), None, None, None, None, None,
                      wins, len(common) - wins - losses, losses, "insufficient data")
    low, high = bootstrap_ci(diffs)
    if (low > 0 and higher_is_better) or (high < 0 and not higher_is_better):
        verdict = "better"
    elif (high < 0 and higher_is_better) or (low > 0 and not higher_is_better):
        verdict = "worse"
    else:
        verdict = "inconclusive"
    return Paired(metric, baseline, variant, len(common), round(fmean(base[q] for q in common), 4),
                  round(fmean(other[q] for q in common), 4), round(fmean(diffs), 4), low, high,
                  wins, len(common) - wins - losses, losses, verdict)
