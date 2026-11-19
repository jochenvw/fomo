from __future__ import annotations

import pytest

from fomo.search.schema import CLASSIFICATION_FIELDS, datasource_definition, participant_definitions


@pytest.fixture
def raw(env, names):
    return participant_definitions(env, names, smart=False)


@pytest.fixture
def smart(env, names):
    return participant_definitions(env, names, smart=True, pending=True)


def _fields(defn):
    return {f["name"]: f for f in defn["index"]["fields"]}


def _skill(defn, name):
    return next(s for s in defn["skillset"]["skills"] if s["name"] == name)


def test_raw_and_smart_share_chunking_and_embedding(raw, smart):
    for skill in ("chunk", "embed"):
        assert _skill(raw, skill) == _skill(smart, skill)
    assert raw["index"]["vectorSearch"] == smart["index"]["vectorSearch"]
    assert raw["indexer"]["parameters"] == smart["indexer"]["parameters"]
    assert _fields(raw)["content_vector"] == _fields(smart)["content_vector"]
    assert set(_fields(smart)) - set(_fields(raw)) == set(CLASSIFICATION_FIELDS)


def test_raw_index_cannot_see_metadata(raw):
    assert not set(CLASSIFICATION_FIELDS) & set(_fields(raw))
    assert "scoringProfiles" not in raw["index"]
    mappings = raw["skillset"]["indexProjections"]["selectors"][0]["mappings"]
    assert not any("document_type" in m["source"] for m in mappings)


def test_smart_index_filterable_document_type(smart, names):
    f = _fields(smart)["document_type"]
    assert f["filterable"] and f["facetable"]
    selector = smart["skillset"]["indexProjections"]["selectors"][0]
    assert selector["targetIndexName"] == names.smart_index
    mapped = {m["name"]: m["source"] for m in selector["mappings"]}
    for key in CLASSIFICATION_FIELDS:
        assert mapped[key] == f"/document/{key}_v"
        cond = _skill(smart, f"default-{key}")
        assert cond["inputs"][2]["source"] == f"= $(/document/{key})"


def test_names_and_pending_flag(raw, smart, names):
    assert raw["indexer"]["disabled"] is False
    assert smart["indexer"]["disabled"] is True
    assert (smart["indexer"]["name"], smart["indexer"]["skillsetName"], smart["indexer"]["targetIndexName"]) == (
        names.smart_indexer, names.smart_skillset, names.smart_index)
    assert raw["indexer"]["dataSourceName"] == smart["indexer"]["dataSourceName"] == names.datasource


def test_datasource_points_at_participant_container(env, names):
    ds = datasource_definition(env, names)
    assert ds["container"] == {"name": "cosmic-wombat-inbox"}
    assert ds["credentials"]["connectionString"] == f"ResourceId={env.resolved_storage_account_id()};"
    assert "AccountKey" not in str(ds)
