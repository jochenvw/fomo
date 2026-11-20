from __future__ import annotations

import pytest

from fomo import guide
from fomo.errors import Blocked
from fomo.provision.ops import check_provision_prerequisites, plan_provision

ALL = {"simple_rag": {"exists": True}, "metadata_miner": {"exists": True}, "smart_rag": {"exists": True}}
DONE_CLASSIFIER = "criteria:\n  contract: binding agreement\n  presentation: slide deck\n"
TODO_CLASSIFIER = "criteria:\n  contract: 'TODO: describe'\n  presentation: slide deck\n"


def _state(*, docs=25, chunks=40, classified=25, smart_types=None, agents=ALL):
    return {
        "participant": "cosmic-wombat",
        "container": {"exists": docs > 0, "documents": docs, "classified": classified},
        "raw_index": {"exists": bool(chunks), "chunks": chunks},
        "smart_index": {"exists": True, "document_type": {"contract": 5} if smart_types is None else smart_types},
        "agents": agents,
    }


def _agents(*present):
    return {k: {"exists": k in present} for k in ("simple_rag", "metadata_miner", "smart_rag")}


@pytest.mark.parametrize(
    ("kwargs", "classifier", "checkpoint"),
    [
        ({"docs": 0}, DONE_CLASSIFIER, "upload"),
        ({"docs": 12}, DONE_CLASSIFIER, "upload"),
        ({"chunks": 0}, DONE_CLASSIFIER, "index"),
        ({"agents": _agents()}, DONE_CLASSIFIER, "simple-rag"),
        ({"classified": 0, "agents": _agents("simple_rag")}, TODO_CLASSIFIER, "classifier"),
        ({"classified": 0, "agents": _agents("simple_rag")}, None, "classifier"),
        ({"classified": 0, "agents": _agents("simple_rag")}, DONE_CLASSIFIER, "miner-deploy"),
        ({"agents": _agents("simple_rag", "smart_rag")}, DONE_CLASSIFIER, "miner-deploy"),
        ({"classified": 0, "smart_types": {}, "agents": _agents("simple_rag", "metadata_miner")},
         TODO_CLASSIFIER, "miner-run"),
        ({"smart_types": {"unclassified": 25}, "agents": _agents("simple_rag", "metadata_miner")},
         DONE_CLASSIFIER, "smart"),
        ({"agents": _agents("simple_rag", "metadata_miner")}, DONE_CLASSIFIER, "smart"),
        ({}, DONE_CLASSIFIER, "compare"),
    ],
)
def test_next_checkpoint_follows_live_evidence(tmp_path, kwargs, classifier, checkpoint):
    path = tmp_path / "classifier.yaml"
    if classifier is not None:
        path.write_text(classifier, encoding="utf-8")
    result = guide.next_checkpoint(_state(**kwargs), {}, expected_docs=25, classifier=path)
    assert result.checkpoint == checkpoint
    assert result.done == list(guide.ORDER[:guide.ORDER.index(checkpoint)]) + (
        ["compare-ready"] if checkpoint == "compare" else [])


@pytest.mark.parametrize("broken", ["container", "agents"])
def test_probe_errors_are_unknown_not_missing(tmp_path, broken):
    state = _state()
    if broken == "container":
        state["container"] = {"name": "x", "error": "403"}
    else:
        state["agents"]["simple_rag"] = {"error": "timeout"}
    result = guide.next_checkpoint(state, {}, expected_docs=25, classifier=tmp_path / "c.yaml")
    assert result.checkpoint == "unknown"
    assert not result.writes
    assert result.unknown


@pytest.mark.parametrize(
    ("step", "targets"),
    [
        ("upload", {"blob container", "25 synthetic"}),
        ("index", {"search ", "indexer "}),
        ("agent", {"prompt agent", "cosmic-wombat"}),
    ],
)
def test_provision_step_plans_only_that_slice(env, names, step, targets):
    full = plan_provision(env, names, {}, step="all")
    part = plan_provision(env, names, {}, step=step)
    assert part.operation == f"provision --step {step}"
    assert 0 < len(part.actions) < len(full.actions)
    assert all(any(t in a.target for t in targets) for a in part.actions)


@pytest.mark.parametrize(
    ("step", "state", "blocked"),
    [
        ("index", {"container": {"documents": 0}}, True),
        ("index", {"container": {"documents": 25}}, False),
        ("agent", {"raw_index": {"chunks": 0}}, True),
        ("agent", {"raw_index": {"chunks": 40}}, False),
        ("upload", {}, False),
        ("all", {}, False),
    ],
)
def test_single_steps_require_the_previous_one(step, state, blocked):
    if blocked:
        with pytest.raises(Blocked):
            check_provision_prerequisites(state, step)
    else:
        check_provision_prerequisites(state, step)
