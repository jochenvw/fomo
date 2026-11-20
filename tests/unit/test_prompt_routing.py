from fomo.agents.prompt import agent_specs, chosen_filters, document_scopes, search_openapi_spec
from fomo.decision.config import load_classifier_config


def test_scopes_are_classifier_labels_present_in_the_index():
    cfg = load_classifier_config()
    scopes = document_scopes(cfg.criteria, ["contract", "invoice", "needs_review", "unclassified", "cannot_tell"],
                             exclude=(cfg.abstain_label,))
    assert list(scopes) == ["contract", "invoice"]
    assert scopes["contract"] == " ".join(cfg.criteria["contract"].split())


def test_openapi_spec_lets_the_model_choose_only_query_and_document_type(env, names):
    spec = agent_specs(env, names)["smart"]
    body = search_openapi_spec("https://s.search.windows.net/", spec.index, {"contract": "Signed agreements."})
    op = body["paths"][f"/indexes/{spec.index}/docs/search"]["post"]
    props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert props["filter"]["enum"] == ["document_type eq 'contract'"]
    assert "Signed agreements." in props["filter"]["description"]
    assert props["top"]["enum"] == [spec.top_k]
    assert body["servers"] == [{"url": "https://s.search.windows.net"}]


def test_chosen_filters_records_each_search_call():
    calls = [{"arguments": '{"search": "payment", "filter": "document_type eq \'contract\'"}'},
             {"arguments": '{"search": "payment"}'}, {"arguments": "not json"}]
    assert chosen_filters(calls) == ["document_type eq 'contract'", None, None]
