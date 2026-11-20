from __future__ import annotations

import copy
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fomo.agents.prompt import agent_specs, cited_in_text
from fomo.errors import Blocked, WorkshopError
from fomo.evaluation import foundry
from fomo.search.knowledge import mcp_url


def test_seed_dataset_has_real_evidence_and_balanced_scope():
    rows = foundry.load_dataset(suite="full")
    assert Counter(row["category"] for row in rows) == {"contract": 14, "non_contract": 4, "unanswerable": 2}
    assert len({row["query"] for row in rows}) == len(rows)
    assert all(row["expected_behavior"] and row["reference_context"] and row["ground_truth"] for row in rows)
    assert {row["category"] for row in foundry.load_dataset()} == {"contract", "non_contract", "unanswerable"}


def test_dataset_rejects_invented_source_evidence(tmp_path):
    data = json.loads(foundry.DATASET.read_text(encoding="utf-8"))
    data["queries"][0]["evidence"]["doc-001"] = "invented 999-day payment period"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(WorkshopError, match="reference evidence is absent"):
        foundry.load_dataset(path, suite="full")


def _context():
    return {
        "agents": {"simple": {"name": "simple-agent", "version": "2"},
                   "smart": {"name": "smart-agent", "version": "7"}},
        "evaluators": {m: {"name": f"builtin.{m}", "version": "1"} for m in foundry.METRICS},
    }


def test_submission_pairs_identical_inputs_without_reference_leakage(env, names, tmp_path):
    rows = foundry.load_dataset()
    project = Mock()
    project.datasets.upload_file.return_value.id = "dataset-id"
    client = Mock()
    client.evals.create.return_value.id = "eval-test"
    client.evals.runs.create.side_effect = [
        SimpleNamespace(id="run-simple", status="queued"), SimpleNamespace(id="run-smart", status="queued"),
    ]
    path = foundry.start(project, client, env, names, rows, _context(),
                         suite="smoke", judge="judge", root=tmp_path)
    calls = client.evals.runs.create.call_args_list
    sources = [call.kwargs["data_source"] for call in calls]
    assert sources[0]["source"] == sources[1]["source"] == {"type": "file_id", "id": "dataset-id"}
    assert [s["target"]["version"] for s in sources] == ["2", "7"]
    assert sources[0]["input_messages"] == sources[1]["input_messages"]
    assert json.dumps(sources[0]["input_messages"]).count("{{item.query}}") == 1
    assert "ground_truth" not in json.dumps(sources[0]["input_messages"])
    assert "reference_context" not in json.dumps(sources[0]["input_messages"])
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["runs"]["smart"]["id"] == "run-smart"
    assert (tmp_path / "agent-metadata.yaml").exists()
    criteria = client.evals.create.call_args.kwargs["testing_criteria"]
    groundedness = next(c for c in criteria if c["name"] == "groundedness")
    assert groundedness["data_mapping"]["context"] == "{{item.reference_context}}"
    assert {c["initialization_parameters"]["threshold"] for c in criteria} == {4}


def _inspection_mocks(monkeypatch, env, names, *, mismatch=False, missing=False):
    specs = agent_specs(env, names)
    simple, smart = specs["simple"], specs["smart"]
    definitions = {
        simple.name: {"kind": "prompt", "model": simple.model, "instructions": simple.instructions,
                      "tools": [{"type": "azure_ai_search", "azure_ai_search": {"indexes": [{
                          "index_name": simple.index, "filter": None, "top_k": simple.top_k,
                          "query_type": simple.query_type}]}}]},
        smart.name: {"kind": "prompt", "model": smart.model, "instructions": smart.instructions,
                     "tools": [{"type": "mcp", "server_label": "knowledge-base",
                                "server_url": mcp_url(env, smart.knowledge_base)}]},
    }
    monkeypatch.setattr(foundry, "_knowledge_base_index", lambda env: {smart.knowledge_base: smart.index}.__getitem__)
    if mismatch:
        definitions[names.smart_rag_agent]["instructions"] = "Different instructions"
    project = Mock()
    project.agents.get.side_effect = lambda name: SimpleNamespace(versions=SimpleNamespace(
        latest=SimpleNamespace(version="1", definition=SimpleNamespace(as_dict=lambda: definitions[name]))))
    project.evaluators.list.return_value = [
        SimpleNamespace(name=f"builtin.{m}", as_dict=lambda: {"version": "1"}) for m in foundry.METRICS
    ]
    rows = foundry.load_dataset()
    by_doc = {}
    for row in rows:
        for doc, quote in row["evidence"].items():
            by_doc[doc] = by_doc.get(doc, "") + " " + quote
    search = Mock()
    search._call.return_value = {"value": [] if missing else [
        {"doc_id": doc, "content": text} for doc, text in by_doc.items()]}
    monkeypatch.setattr(foundry, "SearchClient", lambda env: search)
    monkeypatch.setattr(foundry, "arm", lambda *args: SimpleNamespace(json=lambda: {"value": [{
        "name": env.chat_deployment, "properties": {
            "provisioningState": "Succeeded", "capabilities": {"chatCompletion": "true"},
        },
    }]}))
    return project, rows


@pytest.mark.parametrize("mismatch,missing", [(True, False), (False, True)])
def test_live_prechecks_reject_unfair_settings_or_absent_evidence(monkeypatch, env, names, mismatch, missing):
    project, rows = _inspection_mocks(monkeypatch, env, names, mismatch=mismatch, missing=missing)
    with pytest.raises(Blocked, match=r"unfair comparison|reference evidence missing"):
        foundry.inspect_targets(project, env, names, rows, env.chat_deployment)
    project.datasets.upload_file.assert_not_called()


def test_live_prechecks_pin_versions_and_check_judge(monkeypatch, env, names):
    project, rows = _inspection_mocks(monkeypatch, env, names)
    context = foundry.inspect_targets(project, env, names, rows, env.chat_deployment)
    assert context["agents"]["simple"]["version"] == "1"
    assert context["index_chunks"] > 0
    with pytest.raises(Blocked, match="not a ready chat-completion"):
        foundry.inspect_targets(project, env, names, rows, "nonexistent-judge")


def _state_with_results(tmp_path, *, missing_metric=False):
    rows = foundry.load_dataset()
    state = {"eval_id": "eval-test", "participant": "cosmic-wombat", "suite": "smoke",
             "dataset_sha256": "hash", "context": _context(), "rows": rows, "runs": {}}
    for role in ("simple", "smart"):
        items = []
        for row in rows:
            results = [{"name": m, "metric": m, "score": 4, "passed": True, "reason": "supported"}
                       for m in foundry.METRICS if not missing_metric or m != "similarity"]
            items.append({"id": row["id"], "status": "pass", "datasource_item": {
                **row, "sample.output_text": "The answer",
                "sample.output_items": [{"type": "message", "content": [{
                    "type": "output_text", "text": "The answer", "annotations": [
                        {"type": "url_citation", "title": "source", "url": "https://example.test/doc-001.pdf"},
                    ],
                }]}],
            }, "results": results})
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps({"run": {"result_counts": {"total": len(rows)}}, "output_items": items}),
                        encoding="utf-8")
        state["runs"][role] = {"id": f"run-{role}", "agent": role, "agent_version": "1",
                              "status": "completed", "output_file": str(path)}
    return state


def test_comparison_keeps_slices_pairs_and_citations(tmp_path):
    report = foundry.comparison(_state_with_results(tmp_path))
    assert report["complete"]
    assert report["runs"]["simple"]["rows"][0]["answer"] == "The answer"
    assert report["runs"]["smart"]["slices"]["non_contract"]["expected"] == 1
    assert all(r["smart_minus_simple"]["similarity"] == 0 for r in report["paired"])
    assert report["runs"]["simple"]["rows"][0]["cited_doc_ids"] == ["doc-001"]
    assert "not reported by Foundry" in foundry.render(report)


def test_comparison_does_not_count_missing_scores_as_success(tmp_path):
    report = foundry.comparison(_state_with_results(tmp_path, missing_metric=True))
    assert not report["complete"]
    metric = report["runs"]["smart"]["slices"]["all"]["metrics"]["similarity"]
    assert metric["mean"] is None
    assert metric["pass_rate"] is None
    assert metric["unscored"] == len(foundry.load_dataset())
    assert any(f["cluster"] == "unscored/evaluator detail missing" for f in report["runs"]["smart"]["failures"])


def test_comparison_discloses_a_skipped_grade_without_discarding_scored_cases(tmp_path):
    state = _state_with_results(tmp_path)
    path = tmp_path / "smart.json"
    output = json.loads(path.read_text(encoding="utf-8"))
    grade = next(r for r in output["output_items"][0]["results"] if r["name"] == "similarity")
    grade.update(score=None, passed=None, reason="Placeholder response; evaluation skipped")
    path.write_text(json.dumps(output), encoding="utf-8")
    report = foundry.comparison(state)
    metric = report["runs"]["smart"]["slices"]["all"]["metrics"]["similarity"]
    assert not report["complete"]
    assert metric["mean"] == 4
    assert metric["scored"] == 3
    assert metric["unscored"] == metric["unknown_pass"] == 1
    assert "4.00 (3/4)" in foundry.render(report)


def test_collect_refuses_wrong_project_without_remote_calls(env, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"project_endpoint": "https://other.invalid"}), encoding="utf-8")
    client = Mock()
    with pytest.raises(WorkshopError, match="different project"):
        foundry.collect(client, env, path)
    client.evals.runs.retrieve.assert_not_called()


def test_inline_target_uses_only_query_as_agent_input():
    rows = foundry.load_dataset()
    source = foundry.target_source(_context()["agents"]["simple"], rows, None)
    assert source["source"]["content"][0]["item"]["ground_truth"] == rows[0]["ground_truth"]
    assert source["input_messages"]["template"][0]["content"]["text"] == "{{item.query}}"
    altered = copy.deepcopy(rows)
    altered[0]["ground_truth"] = "A different judge reference"
    assert foundry.target_source(_context()["agents"]["simple"], altered, None)["input_messages"] == source[
        "input_messages"]


def test_search_call_detection_handles_foundry_messages_not_just_output_text():
    output = [{"role": "system", "content": "Search first"},
              {"role": "assistant", "content": [
                  {"type": "tool_call", "name": "azure_ai_search", "arguments": {"query": "terms"}},
              ]},
              {"role": "assistant", "content": [{"type": "output_text", "text": "45 days"}]}]
    assert foundry._search_calls(output) == 1
    assert foundry._search_calls([{"role": "assistant", "content": "Calling search..."}]) == 0


def test_knowledge_base_answers_cite_only_retrieved_doc_ids_in_text():
    docs = [{"content": json.dumps({"doc_id": d, "document_type": "contract"})} for d in ("doc-001", "doc-004")]
    output = [{"type": "mcp_call", "name": "knowledge_base_retrieve", "output": json.dumps({"documents": docs})}]
    assert foundry._search_calls(output) == 1
    text = "Net 45 (MSA, doc-001); see also doc-099 and (Rig Agreement, doc-004)."
    assert cited_in_text(text, foundry._retrieved(output)) == ["doc-001", "doc-004"]
