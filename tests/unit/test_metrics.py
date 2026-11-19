from __future__ import annotations

import pytest

from fomo.evaluation.metrics import (
    classification_scores,
    distinct_docs,
    false_exclusions,
    labels_in_filter,
    mean,
    precision_at_k,
    recall_at_k,
)


def test_distinct_docs_dedupes_chunks_in_rank_order():
    hits = [{"doc_id": "a"}, {"doc_id": "a"}, {"doc_id": None}, {"doc_id": "b"}, {"doc_id": "a"}, {"doc_id": "c"}]
    assert distinct_docs(hits, 2) == ["a", "b"]
    assert distinct_docs(hits, 10) == ["a", "b", "c"]


def test_precision_and_recall():
    assert precision_at_k(["a", "x", "b"], {"a", "b", "c"}, 3) == pytest.approx(2 / 3)
    assert precision_at_k(["a"], {"a"}, 5) == pytest.approx(0.2)  # empty slots are misses
    assert recall_at_k(["a", "x", "b"], ["a", "b", "c", "d"], 2) == pytest.approx(0.25)
    assert recall_at_k([], [], 5) == 1.0
    with pytest.raises(ValueError):
        precision_at_k([], [], 0)


def test_false_exclusions_and_filter_labels():
    predicted = {"a": "contract", "b": "presentation", "c": "needs_review"}
    assert false_exclusions(["a", "b", "c", "d"], predicted, {"contract"}) == ["b", "c", "d"]
    assert labels_in_filter("document_type eq 'contract' or document_type eq 'invoice'") == {"contract", "invoice"}
    assert labels_in_filter(None) is None
    with pytest.raises(ValueError):
        labels_in_filter("doc_id eq 'x'")


def test_classification_scores():
    truth = {
        "d1": {"document_type": "contract"},
        "d2": {"document_type": "presentation", "trap": "about contracts"},
        "d3": {"document_type": "policy", "acceptable": ["policy", "needs_review"]},
        "d4": {"document_type": "invoice"},
        "d5": {"document_type": "invoice"},
    }
    predicted = {"d1": "contract", "d2": "contract", "d3": "needs_review", "d4": "needs_review"}
    s = classification_scores(truth, predicted)
    assert s["documents"] == 5 and s["classified"] == 4 and s["unclassified"] == 1
    assert s["abstained"] == 2
    assert s["accuracy"] == pytest.approx(2 / 5)  # d1 hit + d3 acceptable abstention
    assert s["accuracy_answered"] == pytest.approx(1 / 2)  # d1 / (d1, d2)
    assert s["errors"] == [{"doc_id": "d2", "truth": "presentation", "predicted": "contract",
                            "trap": "about contracts"}]
    assert s["confusion"]["invoice"] == {"(unclassified)": 1, "needs_review": 1}


def test_mean():
    assert mean([1, 2, 2]) == pytest.approx(1.6667)
    assert mean([]) == 0.0
