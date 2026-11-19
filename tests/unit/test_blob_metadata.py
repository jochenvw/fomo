from __future__ import annotations

import pytest

from contoso_workshop.data.blob import MAX_METADATA_BYTES, merge_metadata, validate_metadata
from contoso_workshop.data.corpus import CorpusDocument
from contoso_workshop.data.ingest import ascii_metadata, source_metadata
from contoso_workshop.errors import WorkshopError


def test_validate_metadata_accepts_workshop_keys():
    validate_metadata({"doc_id": "doc-001", "document_type": "contract", "_private": "x"})


@pytest.mark.parametrize(
    "meta",
    [
        {"bad-key": "x"},
        {"1abc": "x"},
        {"title": "Café"},
        {"n": 3},
        {"big": "x" * MAX_METADATA_BYTES},
    ],
)
def test_validate_metadata_rejects(meta):
    with pytest.raises(WorkshopError):
        validate_metadata(meta)


def test_merge_keeps_unrelated_and_overwrites_case_insensitively():
    existing = {"doc_id": "doc-001", "owner": "legal", "Document_Type": "invoice", "classification_version": "v0"}
    merged = merge_metadata(existing, {"document_type": "contract", "classification_version": "v1"})
    assert merged == {"doc_id": "doc-001", "owner": "legal", "document_type": "contract",
                      "classification_version": "v1"}
    assert existing["Document_Type"] == "invoice"  # input not mutated


def test_merge_validates_result():
    with pytest.raises(WorkshopError):
        merge_metadata({"doc_id": "x"}, {"document_type": "contrat\u00e9"})


def test_ascii_metadata():
    assert ascii_metadata("Nordsj\u00f8 \u2014 Caf\u00e9  Supply\u2019s   deal") == "Nordsj - Cafe Supply's deal"
    assert ascii_metadata("x" * 400) == "x" * 256
    assert ascii_metadata("\u00c5sgard", limit=3) == "Asg"


def test_source_metadata_is_valid_blob_metadata():
    doc = CorpusDocument("doc-009", "originals/doc-009.pdf", "ab" * 32, "R\u00e9sum\u00e9 \u2013 Well Test", "pdf")
    meta = source_metadata(doc, "2026.1")
    assert meta == {"doc_id": "doc-009", "title": "Resume - Well Test", "source_sha256": "ab" * 32,
                    "corpus_version": "2026.1", "workshop": "fomo"}
    validate_metadata(meta)
    assert not set(meta) & {"document_type", "classification_version"}
