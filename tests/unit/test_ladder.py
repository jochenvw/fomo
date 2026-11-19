from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fomo.errors import WorkshopError
from fomo.evaluation import ladder, stats
from fomo.evaluation.metrics import ndcg_at_k
from fomo.naming import EVAL_ROUTES, ParticipantNames, validate_agent_name


def _row(**over):
    row = {
        "id": "L001", "split": "test", "stratum": "contract_distractor", "target_type": "contract",
        "query": "What payment terms apply to the supply vessel charter?",
        "source_doc_ids": ["doc-001"],
        "evidence": {"doc-001": "within forty-five (45) days of receipt of invoice"},
        "required_facts": [["forty-five (45) days", "45 days"]],
        "distractor_doc_ids": ["doc-003"], "distractor_facts": [["net 15", "15 days"]],
        "ground_truth": "The charterer pays each correct invoice within 45 days of receipt.",
        "expected_behavior": "Cite the charter; do not use the training deck.",
    }
    row.update(over)
    return row


def _write(tmp_path, rows):
    path = tmp_path / "rag_eval_v2.json"
    path.write_text(json.dumps({"version": "v2", "corpus_version": "2026.1", "queries": rows}), encoding="utf-8")
    return path


def test_dataset_accepts_verifiable_rows_and_selects_split(tmp_path):
    invoice = _row(id="L002", split="dev", stratum="other_type", target_type="invoice",
                   query="How much did the March coffee airlift cost?", source_doc_ids=["doc-019"],
                   evidence={"doc-019": "emergency airlift, 6 March): GBP 4,880.00"},
                   required_facts=[["GBP 4,880", "4,880"]], distractor_doc_ids=[], distractor_facts=[],
                   ground_truth="Invoice FC-2026-031 charged GBP 4,880.00 for the airlift.")
    path = _write(tmp_path, [_row(), invoice])
    assert [r["id"] for r in ladder.load_dataset(path, split="test")] == ["L001"]
    assert len(ladder.load_dataset(path, split="all")) == 2


def test_shipped_dataset_is_valid_and_stratified():
    rows = ladder.load_dataset(split="all")
    for stratum in ladder.STRATA:
        assert {r["split"] for r in rows if r["stratum"] == stratum} == {"dev", "test"}
    assert sum(r["split"] == "test" for r in rows) > sum(r["split"] == "dev" for r in rows)


@pytest.mark.parametrize("change,message", [
    ({"required_facts": [["ninety-nine (99) days"]]}, "not found in any source"),
    ({"distractor_facts": [["forty-five (45) days"]]}, "not found in any distractor"),
    ({"target_type": "invoice"}, "is not a invoice"),
    ({"evidence": {"doc-001": "invented clause"}}, "evidence quote not found"),
    ({"distractor_facts": [["net 15"]], "ground_truth": "Not net 15: 45 days."}, "occurs in the query"),
    ({"distractor_doc_ids": ["doc-004"], "distractor_facts": [["sixty (60) days"]]}, "has the target type"),
])
def test_dataset_rejects_unverifiable_or_leaky_rows(tmp_path, change, message):
    with pytest.raises(WorkshopError, match=message):
        ladder.load_dataset(_write(tmp_path, [_row(**change)]), split="all")


LABELS = {"doc-001": {"document_type": "contract"}, "doc-003": {"document_type": "presentation"},
          "doc-024": {"document_type": "needs_review", "acceptable": ["contract", "correspondence"]}}


def _answer(text, cited=("doc-001",), tool_calls=1, error=None):
    return {"answer": text, "cited_doc_ids": list(cited), "tool_calls": tool_calls, "error": error}


def test_clean_answer_requires_facts_without_distractors_or_failures():
    row = _row()
    clean = ladder.score_answer(_answer("The charter requires payment within 45 days."), row, LABELS)
    assert clean["clean_answer"] and clean["fact_recall"] == 1.0 and clean["distractor_mention"] is False
    leaky = ladder.score_answer(_answer("Net 15 for small suppliers; the charter says 45 days.",
                                        cited=("doc-001", "doc-003")), row, LABELS)
    assert not leaky["clean_answer"] and leaky["distractor_mention"] is True
    assert leaky["off_target_citation"] == 0.5 and leaky["off_target_doc_ids"] == ["doc-003"]


@pytest.mark.parametrize("record", [
    _answer("(remote_functions.azure_ai_search)", cited=()),
    _answer("I'll search the documents for that.", cited=()),
    _answer("Payment within 45 days.", tool_calls=0),
    _answer("", error="agent failed: 500"),
])
def test_operational_failures_never_look_clean(record):
    scored = ladder.score_answer(record, _row(), LABELS)
    assert scored["operational_failure"] and not scored["clean_answer"]
    assert scored["distractor_mention"] is None and scored["off_target_citation"] is None


def test_matching_respects_number_boundaries():
    assert not ladder.score_answer(_answer("Payment within 145 days."), _row(), LABELS)["clean_answer"]
    scored = ladder.score_answer(_answer("Within 45 days; never 115 days."), _row(), LABELS)
    assert scored["distractor_mention"] is False
    assert ladder.contains("GBP 4,880.00 charged", "GBP 4,880") and not ladder.contains("GBP 14,880", "4,880")
    assert not ladder.contains("charged 11.5% per month", "1.5% per month")


@pytest.mark.parametrize("answer,fact", [
    ("only after the first twelve (12) months", "first twelve months"),
    ("only after the first 12 months", "first twelve (12) months"),
    ("retains 5% of each payment", "five percent (5%) of each payment"),
    ("Forty-five days after receipt", "45 days after receipt"),
    ("cap of one hundred percent (100%)", "100%"),
    ("£140 per hour", "GBP 140 per hour"),
])
def test_canonical_matching_accepts_number_and_symbol_paraphrases(answer, fact):
    assert ladder.contains(answer, fact)


def test_unanswerable_answers_are_never_objectively_clean():
    row = _row(stratum="unanswerable", required_facts=[], distractor_facts=[["net 15"]])
    fabricated = ladder.score_answer(_answer("The absent clause guarantees a 200% refund."), row, LABELS)
    assert fabricated["clean_answer"] is None and fabricated["distractor_mention"] is False
    assert ladder.score_answer(_answer("It is net 15."), row, LABELS)["distractor_mention"] is True


def test_truncated_final_answer_line_is_repaired_but_corruption_is_not(tmp_path):
    path = tmp_path / "responses.jsonl"
    path.write_text('{"key": "a"}\n{"key": "b"', encoding="utf-8")
    assert [r["key"] for r in ladder._read_jsonl(path, repair=True)] == ["a"]
    assert path.read_text(encoding="utf-8") == '{"key": "a"}\n'
    path.write_text('{"key": "a"\n{"key": "b"}\n', encoding="utf-8")
    with pytest.raises(WorkshopError, match="corrupt"):
        ladder._read_jsonl(path)


def _paired(diff, low, high, verdict="inconclusive"):
    return stats.Paired("m", "a", "b", 10, 0.5, 0.5 + diff, diff, low, high, 0, 10, 0, verdict)


@pytest.mark.parametrize("fixed,routed,verdict", [
    ((-0.4, -0.6, -0.2), (-0.02, -0.03, -0.01, "worse"), "supported"),
    ((-0.1, -0.3, 0.1), (-0.02, -0.03, -0.01, "worse"), "partially supported"),
    ((-0.4, -0.6, -0.2), (-0.3, -0.5, -0.15, "worse"), "contradicted"),
])
def test_h2_uses_the_noninferiority_margin_not_zero(fixed, routed, verdict):
    comparisons = {"H1:smart_contract": _paired(0, -1, 1), "H1:smart_routed": _paired(0, -1, 1),
                   "H2:smart_contract": _paired(*fixed), "H2:smart_routed": _paired(*routed),
                   "H3:smart_nofilter": _paired(0, -0.05, 0.05), "H4:smart_oracle": _paired(-0.08, -0.15, -0.02),
                   "H5:smart_contract": _paired(-0.3, -0.5, -0.1), "H5:smart_routed": _paired(-0.3, -0.5, -0.1)}
    result = ladder.hypotheses(comparisons)
    assert result["H5"]["verdict"] == "supported"
    assert result["H2"]["verdict"] == verdict
    assert result["H4"]["verdict"] == "inconclusive"  # CI overlaps the -0.10 margin
    assert result["H3"]["verdict"] == "supported"


def test_ambiguous_draft_is_not_counted_as_off_target_for_contract_questions():
    scored = ladder.score_answer(_answer("45 days", cited=("doc-024",)), _row(), LABELS)
    assert scored["off_target_citation"] == 0.0


def test_variant_agents_differ_only_by_route(names):
    routes = {"L001": {"label": "invoice"}}
    row = _row()
    assert ladder.agent_for("simple", row, routes, names) == (names.simple_rag_agent, None)
    assert ladder.agent_for("smart_contract", row, routes, names) == (names.smart_rag_agent, "contract")
    assert ladder.agent_for("smart_routed", row, routes, names)[1] == "invoice"
    assert ladder.agent_for("smart_oracle", row, routes, names)[1] == "contract"
    assert ladder.agent_for("smart_oracle", _row(target_type=None), routes, names)[1] == "any"
    for agent in names.eval_route_agents:
        validate_agent_name(agent)
    long = ParticipantNames("a" * 24)
    assert all(len(long.smart_route_agent(r)) <= 63 for r in EVAL_ROUTES)


def test_route_specs_share_smart_settings_except_filter(env, names):
    specs = ladder.route_specs(env, names)
    assert specs["any"].filter is None and specs["invoice"].filter == "document_type eq 'invoice'"
    assert {(s.model, s.index, s.top_k, s.query_type, s.instructions) for s in specs.values()} == {
        (env.chat_deployment, names.smart_index, 5, "vector_simple_hybrid", specs["any"].instructions)}


@pytest.mark.parametrize("choice,probability,label", [
    ("invoice", 0.9, "invoice"), ("invoice", 0.4, "any"), ("any", 0.95, "any")])
def test_router_sees_only_question_and_falls_back_when_unsure(choice, probability, label):
    client = Mock()
    client.classify.return_value = SimpleNamespace(
        choice=choice, confidence=probability, probabilities={choice: probability}, model="m", latency_ms=1,
        probability_of=lambda c: {choice: probability}.get(c))
    routed = ladder.route_question(client, "How much did the airlift cost?")
    assert routed["label"] == label
    state, cfg = client.classify.call_args.args
    assert state == "Question: How much did the airlift cost?" and cfg is ladder.ROUTER


def test_bootstrap_verdicts_use_ci_not_win_counts():
    base = {f"q{i}": 0.0 for i in range(10)}
    better = stats.compare("clean_answer", "simple", "x", base, {q: 1.0 for q in base})
    assert better.verdict == "better" and better.wins == 10 and better.ci_low == 1.0
    mixed = stats.compare("clean_answer", "simple", "x", base, {q: (1.0 if i % 2 else -1.0)
                                                                 for i, q in enumerate(base)})
    assert mixed.verdict == "inconclusive"
    harm = stats.compare("distractor_mention", "simple", "x", base, {q: 1.0 for q in base}, higher_is_better=False)
    assert harm.verdict == "worse" and harm.losses == 10
    assert stats.compare("m", "a", "b", {"q": 1.0}, {"q": 0.0}).verdict == "insufficient data"


def test_repeats_are_averaged_per_question_before_comparison():
    rows = [{"qid": "q1", "m": True}, {"qid": "q1", "m": False}, {"qid": "q2", "m": 1.0}]
    assert stats.per_question_means(rows, "m") == {"q1": 0.5, "q2": 1.0}
    assert stats.repeat_spread(rows, "m") == 0.5


def test_ndcg_rewards_rank():
    assert ndcg_at_k(["a", "x"], ["a"], 5) == 1.0
    assert ndcg_at_k(["x", "a"], ["a"], 5) < 1.0


def _report_inputs(repeats=3):
    rows = []
    for i in range(4):
        rows.append(_row(id=f"C{i}", query=f"contract question {i}"))
        rows.append(_row(id=f"O{i}", stratum="other_type", target_type="presentation",
                         query=f"deck question {i}", source_doc_ids=["doc-003"], distractor_doc_ids=[],
                         distractor_facts=[], required_facts=[["net 90"]]))
    records = []
    for row in rows:
        for variant in ladder.VARIANTS:
            for repeat in range(1, repeats + 1):
                contract_scope = variant == "smart_contract"
                if row["stratum"] == "contract_distractor":
                    text = "45 days" if variant != "simple" else "45 days, or net 15 per training"
                else:
                    text = "no contract says that" if contract_scope else "Ambition: net 90 days"
                records.append({"key": f"{variant}|{row['id']}|{repeat}", "qid": row["id"], "variant": variant,
                                "repeat": repeat, "route": None, **_answer(text)})
    state = {"run_id": "r", "participant": "p", "split": "test", "repeats": repeats, "seed": 1, "judge": "j",
             "judge_model": "gpt-5.4", "agent_model": "gpt-5-mini", "agent_versions": {}, "dataset_version": "v2",
             "dataset_sha256": "x", "routes": {r["id"]: {"label": r["target_type"]} for r in rows},
             "judge_eval": {"eval_id": "e", "runs": {v: {"id": v, "status": "completed"} for v in ladder.VARIANTS}}}
    retrieved = [{"key": f"{v}|{r['id']}", "qid": r["id"], "variant": v, "p_at_5": 0.2}
                 for r in rows for v in ladder.VARIANTS]
    judged = {rec["key"]: {m: {"score": 4} for m in ladder.JUDGE_METRICS} for rec in records}
    return state, rows, records, retrieved, judged


def test_report_tests_preregistered_hypotheses_and_renders(monkeypatch):
    monkeypatch.setattr(ladder, "load_ground_truth", lambda: LABELS)
    state, rows, records, retrieved, judged = _report_inputs()
    report = ladder.build_report(state, rows, records, retrieved, judged, {})
    assert report["complete"] and report["protocol_compliant"] and report["hypothesis_split"] == "test"
    assert report["hypotheses"]["H1"]["verdict"] == "supported"
    assert report["hypotheses"]["H2"]["verdict"] == "supported"
    assert report["hypotheses"]["H3"]["verdict"] == "contradicted"  # nofilter differs from simple here
    assert report["hypotheses"]["H4"]["verdict"] == "supported"
    assert report["pre_registered"]["H1:smart_routed"]["stratum"] == "contract_distractor"
    assert report["router"]["accuracy"] == 1.0
    assert report["completeness"]["simple"]["response_completeness"] == 12 * 2
    text = ladder.render(report)
    assert "Headline result" in text and "H2: supported" in text and "INCOMPLETE" not in text


def test_report_flags_missing_answers_and_failed_judging(monkeypatch):
    monkeypatch.setattr(ladder, "load_ground_truth", lambda: LABELS)
    state, rows, records, retrieved, judged = _report_inputs(repeats=1)
    state["judge_eval"]["runs"]["smart_routed"]["status"] = "failed"
    report = ladder.build_report(state, rows, records[1:], retrieved, judged, {})
    assert not report["complete"]
    assert report["integrity"]["missing_answers"] == [records[0]["key"]]
    assert report["integrity"]["failed_judge_runs"] == {"smart_routed": "failed"}
    assert not report["protocol_compliant"]
    assert "INCOMPLETE" in ladder.render(report) and "NOT protocol-compliant" in ladder.render(report)
