"""Versioned synthetic corpus: manifest (no labels) and separately held ground truth."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "corpus"
MANIFEST = CORPUS_DIR / "manifest.json"
GROUND_TRUTH = CORPUS_DIR / "ground_truth" / "labels.json"
EVAL_QUERIES = CORPUS_DIR / "eval_queries.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


@dataclass(frozen=True)
class CorpusDocument:
    doc_id: str
    file: str  # relative to corpus/
    sha256: str
    title: str
    fmt: str

    @property
    def blob_name(self) -> str:
        return Path(self.file).name

    def path(self, corpus_dir: Path = CORPUS_DIR) -> Path:
        return corpus_dir / self.file


def load_manifest(path: Path = MANIFEST) -> tuple[str, list[CorpusDocument]]:
    if not path.exists():
        raise ConfigError(f"corpus manifest missing: {path}", fix="run `uv run python -m corpus.generator`")
    data = json.loads(path.read_text(encoding="utf-8"))
    docs = [
        CorpusDocument(d["doc_id"], d["file"], d["sha256"], d["title"], d["format"]) for d in data["documents"]
    ]
    return str(data["corpus_version"]), docs


def verify_corpus(docs: list[CorpusDocument], corpus_dir: Path = CORPUS_DIR) -> list[str]:
    """Return a list of problems (missing files / hash mismatches). Empty list == OK."""
    problems = []
    for doc in docs:
        p = doc.path(corpus_dir)
        if not p.exists():
            problems.append(f"{doc.doc_id}: missing {doc.file}")
        elif sha256_file(p) != doc.sha256:
            problems.append(f"{doc.doc_id}: sha256 mismatch for {doc.file}")
    return problems


def load_ground_truth(path: Path = GROUND_TRUTH) -> dict[str, dict[str, Any]]:
    """doc_id -> {document_type, ...}. Evaluation only; never sent to the classifier or index."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {d["doc_id"]: d for d in data["documents"]}


def load_eval_queries(path: Path = EVAL_QUERIES) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8"))["queries"])
