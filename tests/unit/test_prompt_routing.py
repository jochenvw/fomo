import base64
import json

import pytest

from fomo.agents.prompt import agent_specs, doc_id_from_url, document_scopes, retrieved_doc_ids
from fomo.decision.config import load_classifier_config
from fomo.errors import ConfigError
from fomo.search.knowledge import (
    FILTER_INSTRUCTIONS_MAX,
    filter_instructions,
    knowledge_base_definition,
    knowledge_source_definition,
)


def test_scopes_are_classifier_labels_present_in_the_index():
    cfg = load_classifier_config()
    scopes = document_scopes(cfg.criteria, ["contract", "invoice", "needs_review", "unclassified", "cannot_tell"],
                             exclude=(cfg.abstain_label,))
    assert list(scopes) == ["contract", "invoice"]
    assert scopes["contract"] == " ".join(cfg.criteria["contract"].split())


def test_filter_instructions_fit_the_limit_and_keep_every_label():
    cfg = load_classifier_config()
    scopes = {label: text * 5 for label, text in cfg.criteria.items()}
    text = filter_instructions(scopes)
    assert len(text) <= FILTER_INSTRUCTIONS_MAX
    assert all(f"{label}: " in text for label in scopes)


def test_knowledge_source_hints_the_classifier_labels_on_the_smart_index(names):
    ks = knowledge_source_definition(names, {"contract": "Signed agreements.", "invoice": "Bills."})
    params = ks["searchIndexParameters"]
    assert params["searchIndexName"] == names.smart_index
    hint = params["queryHints"]["filters"][0]
    assert hint["field"] == "document_type" and hint["fieldValues"] == ["contract", "invoice"]
    assert "Signed agreements." in hint["filterInstructions"]


def test_knowledge_base_rejects_minimal_effort(env, names):
    env.kb_reasoning_effort = "minimal"
    with pytest.raises(ConfigError, match="minimal"):
        knowledge_base_definition(env, names)
    env.kb_reasoning_effort = "medium"
    kb = knowledge_base_definition(env, names)
    assert kb["knowledgeSources"] == [{"name": names.knowledge_source}]
    assert kb["models"][0]["azureOpenAIParameters"]["deploymentId"] == env.chat_deployment


def test_smart_spec_uses_the_participant_knowledge_base(env, names):
    smart = agent_specs(env, names)["smart"]
    assert smart.knowledge_base == names.knowledge_base and smart.index == names.smart_index


def test_mcp_output_and_citation_urls_resolve_to_doc_ids():
    output = json.dumps({"documents": [
        {"content": json.dumps({"chunk_id": "c1", "doc_id": "doc-004", "document_type": "contract"})},
        {"content": "not json"}]})
    assert retrieved_doc_ids(output) == ["doc-004"]
    blob = "https://st.blob.core.windows.net/c/doc-007-rig-agreement.md"
    b64 = base64.urlsafe_b64encode(blob.encode()).decode()
    key = f"ab12cd_{b64.rstrip('=')}{len(b64) - len(b64.rstrip('='))}_pages_0"
    assert doc_id_from_url(f"https://s.search.windows.net/indexes/i/docs/{key}?api-version=x") == "doc-007"
