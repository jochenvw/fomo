"""Live, read-only checks against an already provisioned participant (model calls cost a little).

Opt-in: FOMO_LIVE=1, a populated .workshop/environment.json and FOMO_TEST_PARTICIPANT=<hackername>
(a participant that has completed the workshop, e.g. via cheatcode). Run with:

    $env:FOMO_LIVE = "1"; $env:FOMO_TEST_PARTICIPANT = "cosmic-wombat"
    uv run pytest -m integration tests/integration
"""

from __future__ import annotations

import os

import pytest

from fomo.config import ENV_FILE, Environment
from fomo.data.corpus import CORPUS_DIR, REPO_ROOT, load_manifest
from fomo.naming import ParticipantNames, pick_free_hackername

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("FOMO_LIVE") != "1"
                       or not (REPO_ROOT / ENV_FILE).exists(),
                       reason="live tests need FOMO_LIVE=1 and .workshop/environment.json"),
]


@pytest.fixture(scope="module")
def live_env() -> Environment:
    return Environment.load(env_file=REPO_ROOT / ENV_FILE).require()


@pytest.fixture(scope="module")
def participant() -> ParticipantNames:
    name = os.environ.get("FOMO_TEST_PARTICIPANT")
    if not name:
        pytest.skip("set FOMO_TEST_PARTICIPANT to an existing, completed participant")
    names = ParticipantNames(name)
    names.validate()
    return names


def test_blob_listing_and_metadata(live_env, participant):
    from fomo.provision.participant import container_client

    container = container_client(live_env, participant)
    assert container.exists()
    blobs = list(container.list())
    _, docs = load_manifest()
    assert {b.name for b in blobs} >= {d.blob_name for d in docs}
    for blob in blobs:
        meta = {k.lower(): v for k, v in blob.metadata.items()}
        assert meta.get("doc_id") and meta.get("source_sha256")
    assert any("document_type" in {k.lower() for k in b.metadata} for b in blobs)


def test_decision_choice_call(live_env):
    from fomo.data.extract import extract
    from fomo.decision.client import DecisionClient, build_state
    from fomo.decision.config import load_classifier_config

    cfg = load_classifier_config()
    doc = next(d for d in load_manifest()[1] if d.doc_id == "doc-001")
    ex = extract(doc.blob_name, doc.path(CORPUS_DIR).read_bytes())
    client = DecisionClient(live_env.resolved_decision_endpoint(), live_env.decision_deployment, retries=3)
    result = client.classify(build_state(ex.text, cfg, title=ex.title, fmt=ex.fmt), cfg)
    assert result.choice in cfg.criteria
    assert result.probabilities
    assert all(0.0 <= p <= 1.0001 for p in result.probabilities.values())


def test_smart_index_filter_returns_only_contracts(live_env, participant):
    from fomo.agents.prompt import CONTRACT_FILTER
    from fomo.search.client import SearchClient

    hits = SearchClient(live_env).query(participant.smart_index, "payment terms and termination",
                                        top=10, filter_=CONTRACT_FILTER, select="doc_id,document_type")
    assert hits, "smart index returned nothing for contracts; run `fomo smart --apply`"
    assert {h["document_type"] for h in hits} == {"contract"}


def test_smart_agent_routes_to_contracts_and_cites_documents(live_env, participant):
    from fomo.agents.prompt import CONTRACT_FILTER, PromptAgents

    answer = PromptAgents(live_env).ask(participant.smart_rag_agent,
                                        "Which contracts define payment terms? Cite the documents.")
    assert answer.text.strip()
    assert CONTRACT_FILTER in answer.filters
    assert answer.cited_doc_ids


def test_existing_participant_is_detected_as_taken(live_env, participant):
    from fomo.provision.participant import container_client
    from fomo.search.client import SearchClient

    taken_indexes = set(SearchClient(live_env).list_names("indexes"))

    def is_taken(name: str) -> bool:
        n = ParticipantNames(name)
        return n.raw_index in taken_indexes or container_client(live_env, n).exists()

    assert is_taken(participant.hackername)
    fresh = pick_free_hackername(is_taken, preferred=[participant.hackername])
    assert fresh != participant.hackername
