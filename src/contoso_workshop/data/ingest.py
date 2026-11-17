"""Upload the versioned corpus into a participant container (idempotent, never overwrites)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .. import WORKSHOP_TAG
from ..errors import ConfigError
from .blob import CONTENT_TYPES, ParticipantContainer  # noqa: F401  (re-export for callers)
from .corpus import CORPUS_DIR, CorpusDocument, load_manifest, verify_corpus


def ascii_metadata(value: str, limit: int = 256) -> str:
    """Blob metadata must be ASCII: fold accents, replace dashes, drop the rest."""
    value = value.replace("\u2014", "-").replace("\u2013", "-").replace("\u2019", "'")
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(folded.split())[:limit]


def source_metadata(doc: CorpusDocument, corpus_version: str) -> dict[str, str]:
    return {
        "doc_id": doc.doc_id,
        "title": ascii_metadata(doc.title),
        "source_sha256": doc.sha256,
        "corpus_version": corpus_version,
        "workshop": WORKSHOP_TAG,
    }


@dataclass
class IngestReport:
    corpus_version: str
    uploaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.uploaded) + len(self.unchanged) + len(self.conflicts)


def ingest_corpus(container: ParticipantContainer, *, overwrite_if_changed: bool = False) -> IngestReport:
    version, docs = load_manifest()
    problems = verify_corpus(docs)
    if problems:
        raise ConfigError("corpus integrity check failed: " + "; ".join(problems[:5]),
                          fix="run `uv run python -m corpus.generator` and do not edit corpus/originals")
    container.ensure(metadata={"workshop": WORKSHOP_TAG})
    report = IngestReport(version)
    for doc in docs:
        data = doc.path(CORPUS_DIR).read_bytes()
        result = container.upload(doc.blob_name, data, source_metadata(doc, version),
                                  overwrite_if_changed=overwrite_if_changed)
        getattr(report, {"uploaded": "uploaded", "unchanged": "unchanged"}.get(result, "conflicts")).append(
            doc.blob_name)
    return report
