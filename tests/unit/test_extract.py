from __future__ import annotations

import json

import pytest

from fomo.data.corpus import CORPUS_DIR, MANIFEST, load_ground_truth, load_manifest, verify_corpus
from fomo.data.extract import extract

VERSION, DOCS = load_manifest()
BY_ID = {d.doc_id: d for d in DOCS}


def _extract(doc_id):
    doc = BY_ID[doc_id]
    return extract(doc.blob_name, doc.path(CORPUS_DIR).read_bytes())


@pytest.mark.parametrize("fmt", ["pdf", "pptx"])
def test_extract_each_format(fmt):
    doc = next(d for d in DOCS if d.fmt == fmt)
    ex = _extract(doc.doc_id)
    assert ex.fmt == fmt
    assert ex.chars > 200
    assert ex.pages >= 1
    assert ex.title
    assert not ex.warnings


def test_adversarial_training_deck_is_a_pptx_about_contracts():
    ex = _extract("doc-003")
    assert ex.fmt == "pptx"
    assert ex.title == BY_ID["doc-003"].title
    assert "[Slide 1]" in ex.text
    assert "contract" in ex.text.lower()


def test_every_corpus_doc_extracts_text():
    for doc in DOCS:
        assert _extract(doc.doc_id).chars > 40, doc.doc_id


def test_extract_txt_and_unsupported():
    ex = extract("note.txt", b"Hello title\n\n\n\nbody\x00text")
    assert (ex.title, ex.fmt, ex.text) == ("Hello title", "txt", "Hello title\n\nbody text")
    assert extract("image.png", b"\x89PNG").warnings == ["unsupported format .png"]


def test_corpus_checksums_match():
    assert verify_corpus(DOCS) == []


def test_verify_corpus_detects_tampering(tmp_path):
    doc = DOCS[0]
    (tmp_path / "originals").mkdir()
    (tmp_path / doc.file).write_bytes(doc.path(CORPUS_DIR).read_bytes() + b"x")
    problems = verify_corpus(DOCS[:2], tmp_path)
    assert any("sha256 mismatch" in p for p in problems)
    assert any("missing" in p for p in problems)


def test_manifest_has_no_answer_labels():
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in raw["documents"]:
        assert not {"document_type", "acceptable", "trap", "label"} & set(entry), entry["doc_id"]
    truth = load_ground_truth()
    assert set(truth) == set(BY_ID)
    assert truth["doc-003"]["document_type"] == "presentation"
    assert all("document_type" in t for t in truth.values())
