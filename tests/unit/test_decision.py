from __future__ import annotations

import pytest
import yaml

from contoso_workshop.decision.client import DecisionResult, _as_probabilities, build_state, normalize_response
from contoso_workshop.decision.config import NEEDS_REVIEW, REFERENCE_CONFIG, load_classifier_config
from contoso_workshop.decision.policy import apply_policy
from contoso_workshop.errors import ConfigError, DecisionError


@pytest.fixture
def cfg():
    return load_classifier_config()


def _body(answer, question="document_type"):
    return {"model": "decision-1", "answers": {question: answer}, "usage": {"input_tokens": 10}}


def test_normalize_live_shape():
    body = _body({"type": "choice", "choice": "contract", "confidence": 0.9,
                  "probabilities": {"contract": 0.9, "invoice": 0.07, "cannot_tell": 0.03}})
    r = normalize_response(body, "document_type", latency_ms=12, request_id="req")
    assert r.choice == "contract"
    assert r.probabilities["invoice"] == pytest.approx(0.07)
    assert r.confidence == 0.9
    assert r.margin() == pytest.approx(0.83)
    assert (r.model, r.usage, r.latency_ms, r.request_id) == ("decision-1", {"input_tokens": 10}, 12, "req")


def test_normalize_infers_choice_from_list_probabilities():
    body = _body({"options": [{"option": "policy", "score": 0.2}, {"label": "invoice", "probability": 0.8}]})
    r = normalize_response(body, "document_type")
    assert r.choice == "invoice"
    assert r.confidence is None


@pytest.mark.parametrize(
    "value",
    [
        {"a": 0.7, "b": 0.3},
        {"a": {"probability": 0.7}, "b": {"score": 0.3}},
        [{"label": "a", "probability": 0.7}, {"choice": "b", "p": 0.3}, "junk", {"name": "c"}],
    ],
)
def test_as_probabilities_shapes(value):
    assert _as_probabilities(value) == {"a": 0.7, "b": 0.3}


@pytest.mark.parametrize(
    "body",
    [
        {"answers": {}},
        {"answers": {"document_type": "contract"}},
        _body({"type": "number", "value": 3}),
        _body({"type": "choice", "probabilities": {}}),
        _body({"choice": "contract", "probabilities": {"contract": 1.7}}),
    ],
)
def test_normalize_rejects_unusable(body):
    with pytest.raises(DecisionError):
        normalize_response(body, "document_type")


def test_build_state_truncates_and_includes_title(cfg):
    cfg.max_chars = 500
    state = build_state("x" * 2000, cfg, title="  Supply Agreement  ", fmt="pdf")
    assert state.startswith("Title: Supply Agreement\n")
    assert "File format" not in state  # include_format_hint is false in the reference config
    assert state.endswith("[...truncated]")
    assert "x" * 500 + "\n" in state and "x" * 501 not in state
    cfg.include_title, cfg.include_format_hint = False, True
    state = build_state("short", cfg, title="T", fmt="pptx")
    assert "Title:" not in state
    assert "File format: pptx" in state


def test_reference_config_valid(cfg):
    assert cfg.question == "document_type"
    assert "cannot_tell" not in cfg.labels
    assert {"contract", "presentation"} <= set(cfg.labels)
    q = cfg.to_question()["document_type"]
    assert q["type"] == "choice"
    assert set(q["criteria"]) == set(cfg.criteria)


def _write(tmp_path, mutate):
    data = yaml.safe_load(REFERENCE_CONFIG.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "classifier.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_todo_placeholders_rejected(tmp_path):
    path = _write(tmp_path, lambda d: d["criteria"].__setitem__("contract", "TODO: describe contracts"))
    with pytest.raises(ConfigError, match="TODO"):
        load_classifier_config(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["criteria"].__setitem__("Bad-Label", "a perfectly fine description"),
        lambda d: d["criteria"].pop("cannot_tell"),
        lambda d: d["criteria"].__setitem__(NEEDS_REVIEW, "reserved for the abstention policy"),
        lambda d: d["thresholds"].__setitem__("min_probability", 1.5),
        lambda d: d["state"].__setitem__("max_chars", 10),
        lambda d: d["criteria"].__setitem__("invoice", "short"),
    ],
)
def test_invalid_configs_rejected(tmp_path, mutate):
    with pytest.raises(ConfigError):
        load_classifier_config(_write(tmp_path, mutate))


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError):
        load_classifier_config(tmp_path / "nope.yaml")


def _result(probs, choice=None):
    return DecisionResult("document_type", choice or max(probs, key=probs.get), probs, "m", {}, 0, None)


def test_policy_accepts_confident_answer(cfg):
    out = apply_policy(_result({"contract": 0.9, "invoice": 0.1}), cfg)
    assert (out.label, out.reason) == ("contract", "accepted")


def test_policy_low_probability(cfg):
    out = apply_policy(_result({"contract": 0.5, "invoice": 0.2, "policy": 0.3}), cfg)
    assert (out.label, out.model_choice, out.reason) == (NEEDS_REVIEW, "contract", "low_probability")


def test_policy_low_margin(cfg):
    out = apply_policy(_result({"contract": 0.62, "presentation": 0.38}), cfg)
    assert out.reason == "accepted"
    out = apply_policy(_result({"contract": 0.61, "presentation": 0.5}), cfg)
    assert (out.label, out.reason) == (NEEDS_REVIEW, "low_margin")


def test_policy_abstain_and_invalid_label(cfg):
    assert apply_policy(_result({"cannot_tell": 0.9, "contract": 0.1}), cfg).reason == "abstained"
    out = apply_policy(_result({"memo": 0.95, "contract": 0.05}), cfg)
    assert (out.label, out.reason) == (NEEDS_REVIEW, "invalid_label")
