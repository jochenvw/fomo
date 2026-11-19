from __future__ import annotations

import pytest

from fomo.provision.participant import render_status, stage_of

ALL_AGENTS = {"simple_rag": {"exists": True}, "metadata_miner": {"exists": True}, "smart_rag": {"exists": True}}


def _state(*, container=True, chunks=40, classified=0, smart_types=None, agents=ALL_AGENTS):
    state = {
        "participant": "cosmic-wombat",
        "container": {"name": "cosmic-wombat-inbox", "exists": container, "documents": 25, "classified": classified},
        "raw_index": {"name": "cosmic-wombat-raw", "exists": bool(chunks), "chunks": chunks},
        "smart_index": {"name": "cosmic-wombat-smart", "exists": smart_types is not None,
                        "document_type": smart_types or {}},
    }
    if agents is not None:
        state["agents"] = agents
    return state


@pytest.mark.parametrize(
    ("kwargs", "stage"),
    [
        ({"container": False}, "not-provisioned"),
        ({"chunks": 0}, "provisioning-incomplete"),
        ({}, "baseline-ready"),
        ({"classified": 25}, "classified-not-indexed"),
        ({"classified": 25, "smart_types": {"unclassified": 25}}, "classified-not-indexed"),
        ({"classified": 25, "smart_types": {"contract": 5}}, "complete"),
        ({"classified": 25, "smart_types": {"contract": 5}, "agents": None}, "complete"),
        ({"agents": None}, "baseline-ready"),
        ({"agents": {"metadata_miner": {"exists": True}}}, "provisioning-incomplete"),
        ({"classified": 25, "smart_types": {"contract": 5},
          "agents": {"simple_rag": {"exists": True}, "metadata_miner": {"exists": True}}}, "smart-index-ready"),
        ({"classified": 25, "smart_types": {"contract": 5},
          "agents": {"simple_rag": {"exists": True}, "smart_rag": {"exists": True},
                     "metadata_miner": {"exists": False}}}, "complete-local-miner"),
    ],
)
def test_stage_of(kwargs, stage):
    assert stage_of(_state(**kwargs)) == stage


def test_render_status_handles_missing_agents():
    state = _state(classified=25, smart_types={"contract": 5}, agents=None)
    state["stage"] = stage_of(state)
    text = render_status(state)
    assert "stage: complete" in text
    assert "contract=5" in text
