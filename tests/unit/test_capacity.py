from __future__ import annotations

import pytest

from fomo.capacity import (
    capacity_report,
    cost_estimate,
    max_participants,
    recommend_sku,
    render,
    required_objects,
)


def test_required_objects_per_participant():
    assert required_objects(1) == {"indexes": 2, "indexers": 2, "skillsets": 2, "datasources": 1}
    assert required_objects(40)["indexes"] == 80


@pytest.mark.parametrize(("sku", "cap"), [("basic", 7), ("standard", 25), ("standard2", 100)])
def test_max_participants(sku, cap):
    assert max_participants(sku)[0] == cap


def test_recommend_sku():
    assert recommend_sku(5) == "basic"
    assert recommend_sku(20) == "standard"
    assert recommend_sku(40) == "standard2"
    assert recommend_sku(5, {"indexes": 10}) == "standard"  # existing objects eat headroom
    assert recommend_sku(500).startswith("standard2 (multiple")


def test_capacity_report_40_on_basic_does_not_fit():
    rep = capacity_report(40, "Basic")
    assert rep.fits is False
    assert rep.recommended_sku == "standard2"
    assert rep.services_needed_at_current_sku == 6
    assert rep.headroom["indexes"] == 15 - 80
    assert any("does NOT fit" in n for n in rep.notes)
    assert "DOES NOT FIT" in render(rep)


def test_capacity_report_counts_current_usage():
    assert capacity_report(5, "basic").fits
    assert not capacity_report(5, "basic", current={"indexes": 6}).fits


def test_unknown_sku():
    with pytest.raises(ValueError):
        capacity_report(1, "premium")


def test_cost_estimate():
    c = cost_estimate(40, search_sku="standard2", hours=8)
    assert c["search_usd"] > 0 and c["chat_usd"] > 0 and c["embedding_usd"] > 0
    assert c["total_known_usd"] >= c["search_usd"] + c["chat_usd"]
    assert c["decision_tokens"] == 40 * 3 * 25 * 1000
    assert cost_estimate(80, search_sku="standard2")["chat_usd"] == pytest.approx(2 * c["chat_usd"], rel=0.01)
