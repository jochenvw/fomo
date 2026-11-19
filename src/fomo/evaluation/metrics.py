"""Retrieval and classification metrics (pure; unit tested)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any


def distinct_docs(hits: Iterable[dict[str, Any]], k: int) -> list[str]:
    """Chunk hits -> first k distinct source document ids, in rank order."""
    out: list[str] = []
    for hit in hits:
        doc = hit.get("doc_id")
        if doc and doc not in out:
            out.append(doc)
        if len(out) == k:
            break
    return out


def precision_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """Relevant share of the top-k slots (divides by k: empty slots count as misses)."""
    if k <= 0:
        raise ValueError("k must be positive")
    rel = set(relevant)
    return sum(1 for d in retrieved[:k] if d in rel) / k


def recall_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    rel = set(relevant)
    if not rel:
        return 1.0
    return len(rel.intersection(retrieved[:k])) / len(rel)


def ndcg_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """Binary-relevance nDCG@k: rewards relevant documents near the top."""
    rel = set(relevant)
    if not rel:
        return 1.0
    dcg = sum(1 / math.log2(rank + 2) for rank, doc in enumerate(retrieved[:k]) if doc in rel)
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(len(rel), k)))
    return round(dcg / ideal, 4)


def false_exclusions(relevant: Iterable[str], predicted: dict[str, str], allowed_labels: set[str]) -> list[str]:
    """Relevant docs a label filter can never return because they were (mis)classified outside it."""
    return sorted(d for d in relevant if predicted.get(d) not in allowed_labels)


def labels_in_filter(odata: str | None) -> set[str] | None:
    """Labels admitted by the simple filters this workshop uses: `document_type eq 'x'`
    (optionally OR-ed). None = no filter (everything admitted)."""
    if not odata:
        return None
    labels: set[str] = set()
    for part in odata.split(" or "):
        part = part.strip().strip("()")
        if not part.startswith("document_type eq '") or not part.endswith("'"):
            raise ValueError(f"unsupported filter for exclusion analysis: {odata}")
        labels.add(part[len("document_type eq '"):-1])
    return labels


def classification_scores(truth: dict[str, dict[str, Any]], predicted: dict[str, str]) -> dict[str, Any]:
    """Accuracy against ground truth `acceptable` labels; needs_review is an abstention, not a hit.

    Returns accuracy over all docs, accuracy over answered docs, abstentions, a confusion matrix
    (truth -> predicted -> count) and the list of misclassified documents.
    """
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    correct = answered = abstained = missing = 0
    errors: list[dict[str, Any]] = []
    for doc_id, gt in sorted(truth.items()):
        pred = predicted.get(doc_id)
        if pred is None:
            missing += 1
            confusion[gt["document_type"]]["(unclassified)"] += 1
            continue
        confusion[gt["document_type"]][pred] += 1
        acceptable = set(gt.get("acceptable") or [gt["document_type"]])
        if pred == "needs_review":
            abstained += 1
            if "needs_review" in acceptable:
                correct += 1
            continue
        answered += 1
        if pred in acceptable:
            correct += 1
        else:
            errors.append({"doc_id": doc_id, "truth": gt["document_type"], "predicted": pred,
                           "trap": gt.get("trap")})
    total = len(truth)
    return {
        "documents": total,
        "classified": total - missing,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "accuracy_answered": round(sum(1 for d, g in truth.items() if predicted.get(d) in
                                       set(g.get("acceptable") or [g["document_type"]]) and
                                       predicted.get(d) != "needs_review") / answered, 4) if answered else 0.0,
        "abstained": abstained,
        "unclassified": missing,
        "confusion": {t: dict(sorted(c.items())) for t, c in sorted(confusion.items())},
        "errors": errors,
    }


def mean(values: Iterable[float]) -> float:
    vals = list(values)
    return round(sum(vals) / len(vals), 4) if vals else 0.0
