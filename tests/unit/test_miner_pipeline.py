from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ResourceNotFoundError

from fomo.data.blob import SKIP_PREFIXES, BlobInfo, merge_metadata
from fomo.data.corpus import sha256_bytes
from fomo.decision.client import DecisionResult
from fomo.decision.config import load_classifier_config
from fomo.errors import PermissionDenied
from fomo.miner.pipeline import CHECKPOINT_BLOB, Miner, classification_metadata, is_current


@pytest.fixture
def cfg():
    return load_classifier_config()


def test_is_current(cfg):
    meta = {"Classified_SHA256": "abc", "classification_version": cfg.version}
    assert is_current(meta, "abc", cfg)
    assert not is_current(meta, "def", cfg)
    assert not is_current(meta, None, cfg)
    assert not is_current({"classified_sha256": "abc", "classification_version": "old"}, "abc", cfg)
    assert not is_current({}, "abc", cfg)


def test_classification_metadata(cfg):
    outcome = SimpleNamespace(label="contract", probability=0.91234, reason="accepted")
    result = SimpleNamespace(model="decision-1", confidence=0.5)
    meta = classification_metadata(outcome, result, "sha", cfg)
    assert set(meta) == {"document_type", "classification_confidence", "classification_version",
                         "classification_model", "classification_reason", "classified_sha256", "classified_at"}
    assert meta["classification_confidence"] == "0.9123"
    assert meta["classification_version"] == cfg.version
    assert meta["classified_sha256"] == "sha"
    outcome.probability, result.confidence = None, None
    assert classification_metadata(outcome, result, "sha", cfg)["classification_confidence"] == ""


class FakeContainer:
    """In-memory stand-in for ParticipantContainer (only what Miner uses)."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.data = dict(blobs)
        self.meta: dict[str, dict[str, str]] = {
            name: {"doc_id": name.split(".")[0], "source_sha256": sha256_bytes(body)} for name, body in blobs.items()}
        self.client = SimpleNamespace(upload_blob=self._upload)
        self.metadata_writes: list[str] = []

    def _upload(self, name, body, overwrite=False):
        self.data[name] = body

    def list(self, *, include_metadata=True):
        for name in sorted(self.data):
            if not name.startswith(SKIP_PREFIXES):
                yield BlobInfo(name, f"etag-{name}", len(self.data[name]), dict(self.meta[name]), None)

    def download(self, name):
        if name not in self.data:
            raise ResourceNotFoundError("missing")
        return self.data[name], f"etag-{name}"

    def update_metadata(self, name, updates, *, etag=None):
        self.metadata_writes.append(name)
        self.meta[name] = merge_metadata(self.meta[name], updates)
        return self.meta[name]

    def put(self, name, body):
        self.data[name] = body
        self.meta[name] = {"doc_id": name.split(".")[0], "source_sha256": sha256_bytes(body)}


class FakeDecision:
    deployment = "decision-1"

    def __init__(self) -> None:
        self.calls = 0

    def classify(self, state, cfg):
        self.calls += 1
        label = "contract" if "agreement" in state.lower() else "correspondence"
        other = "invoice" if label == "contract" else "policy"
        return DecisionResult(cfg.question, label, {label: 0.9, other: 0.1}, "decision-1",
                              {"input_tokens": 100}, 5, None)


LONG = " This text is long enough to be classified by the miner pipeline."


def _miner(container, decision, cfg):
    return Miner(container, decision, cfg, participant="cosmic-wombat", requests_per_second=0)


def test_miner_delta_reprocesses_only_changed_blob(cfg):
    container = FakeContainer({
        "doc-001.txt": b"Supply Agreement between A and B." + LONG.encode(),
        "doc-002.txt": b"Dear colleague, a short memo." + LONG.encode(),
        "doc-003.txt": b"tiny",
    })
    decision = FakeDecision()
    first = _miner(container, decision, cfg).run()
    assert first.counts() == {"classified": 2, "skipped": 1}
    assert first.labels() == {"contract": 1, "correspondence": 1}
    assert container.meta["doc-001.txt"]["document_type"] == "contract"
    assert container.meta["doc-001.txt"]["doc_id"] == "doc-001"  # source keys kept
    checkpoint = json.loads(container.data[CHECKPOINT_BLOB])
    assert set(checkpoint["items"]) == {"doc-001.txt", "doc-002.txt"}
    assert any(n.startswith("_miner/runs/") for n in container.data)

    rerun = _miner(container, decision, cfg).run()
    assert rerun.counts() == {"unchanged": 2, "skipped": 1}
    assert decision.calls == 2

    container.put("doc-002.txt", b"Service Agreement, signed by both parties." + LONG.encode())
    container.metadata_writes.clear()
    third = _miner(container, decision, cfg).run()
    assert {i.blob: i.status for i in third.items} == {
        "doc-001.txt": "unchanged", "doc-002.txt": "classified", "doc-003.txt": "skipped"}
    assert container.metadata_writes == ["doc-002.txt"]
    assert decision.calls == 3

    cfg.version = "ref-next"
    assert _miner(container, decision, cfg).run(mode="delta").counts()["classified"] == 2


def test_miner_dry_run_and_deletions(cfg):
    container = FakeContainer({"doc-001.txt": b"Agreement." + LONG.encode(), "doc-002.txt": b"Memo." + LONG.encode()})
    decision = FakeDecision()
    dry = _miner(container, decision, cfg).run(dry_run=True, only=["doc-001"])
    assert [i.status for i in dry.items] == ["classified"]
    assert container.metadata_writes == [] and CHECKPOINT_BLOB not in container.data

    _miner(container, decision, cfg).run()
    del container.data["doc-002.txt"]
    report = _miner(container, decision, cfg).run()
    assert {i.blob: i.status for i in report.items} == {"doc-001.txt": "unchanged", "doc-002.txt": "deleted"}
    assert set(json.loads(container.data[CHECKPOINT_BLOB])["items"]) == {"doc-001.txt"}


def test_miner_permission_denied_fails_fast(cfg):
    container = FakeContainer({"doc-001.txt": b"Agreement." + LONG.encode()})

    def deny(*a, **kw):
        raise PermissionDenied("HTTP 403. Needs Storage Blob Data Contributor.")

    container.update_metadata = deny
    with pytest.raises(PermissionDenied):
        _miner(container, FakeDecision(), cfg).run()
