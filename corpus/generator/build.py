"""Deterministic corpus builder.

    uv run python -m corpus.generator          # (re)build corpus/ from content.py
    uv run python -m corpus.generator --check  # verify committed files match a fresh build

Outputs:
  corpus/originals/doc-NNN.(pdf|pptx)   text-bearing files, neutral names
  corpus/text/doc-NNN.txt               plain-text snapshots for offline tests
  corpus/manifest.json                  ids, files, sha256, titles - NO labels
  corpus/ground_truth/labels.json       labels + traps (evaluation only)
  corpus/eval_queries.json              questions, expected doc ids, evidence, desired filter
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .content import DOCUMENTS, EVAL_QUERIES

CORPUS_VERSION = "2026.1"
FIXED_DT = datetime(2026, 1, 1, 0, 0, 0)  # naive on purpose: deterministic file timestamps
ROOT = Path(__file__).resolve().parents[1]


def build_pdf(doc: dict[str, Any]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buf = io.BytesIO()
    template = SimpleDocTemplate(
        buf,
        pagesize=A4,
        title=doc["title"],
        author="Contoso Oil (fictional)",
        subject="Synthetic workshop document",
        creator="contoso-workshop corpus generator",
        invariant=1,
    )
    styles = getSampleStyleSheet()
    story: list[Any] = [Paragraph(_esc(doc["title"]), styles["Title"]), Spacer(1, 8)]
    story.append(Paragraph("FICTIONAL - synthetic workshop material", styles["Italic"]))
    story.append(Spacer(1, 10))
    for heading, paragraphs in doc["sections"]:
        if heading:
            story.append(Paragraph(_esc(heading), styles["Heading2"]))
        for para in paragraphs:
            story.append(Paragraph(_esc(para), styles["BodyText"]))
            story.append(Spacer(1, 4))
    template.build(story)
    return buf.getvalue()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_pptx(doc: dict[str, Any]) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    prs.core_properties.title = doc["title"]
    prs.core_properties.author = "Contoso Oil (fictional)"
    prs.core_properties.created = FIXED_DT
    prs.core_properties.modified = FIXED_DT
    prs.core_properties.last_modified_by = "corpus generator"
    prs.core_properties.revision = 1
    for idx, (title, bullets, notes) in enumerate(doc["slides"]):
        layout = prs.slide_layouts[0] if idx == 0 else prs.slide_layouts[1]
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = title
        body = slide.placeholders[1]
        tf = body.text_frame
        tf.text = bullets[0] if bullets else ""
        for bullet in bullets[1:]:
            p = tf.add_paragraph()
            p.text = bullet
            p.font.size = Pt(20)
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    raw = io.BytesIO()
    prs.save(raw)
    return _normalize_zip(raw.getvalue())


def _normalize_zip(data: bytes) -> bytes:
    """Rewrite zip with fixed timestamps and sorted-stable order for byte-identical output."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            new = zipfile.ZipInfo(info.filename, date_time=FIXED_DT.timetuple()[:6])
            new.compress_type = zipfile.ZIP_DEFLATED
            new.external_attr = 0o600 << 16
            dst.writestr(new, src.read(info.filename))
    return out.getvalue()


def build_all() -> dict[str, bytes]:
    """Return {relative path: bytes} for every generated artifact."""
    from contoso_workshop.data.extract import extract

    files: dict[str, bytes] = {}
    manifest_docs = []
    labels = []
    for doc in sorted(DOCUMENTS, key=lambda d: d["id"]):
        fmt = doc["fmt"]
        data = build_pdf(doc) if fmt == "pdf" else build_pptx(doc)
        rel = f"originals/{doc['id']}.{fmt}"
        files[rel] = data
        sha = hashlib.sha256(data).hexdigest()
        extraction = extract(rel, data)
        files[f"text/{doc['id']}.txt"] = (extraction.text + "\n").encode("utf-8")
        manifest_docs.append(
            {"doc_id": doc["id"], "file": rel, "sha256": sha, "title": doc["title"], "format": fmt,
             "bytes": len(data)}
        )
        labels.append(
            {
                "doc_id": doc["id"],
                "document_type": doc["label"],
                "acceptable": doc.get("acceptable", [doc["label"]]),
                "title": doc["title"],
                "trap": doc.get("trap"),
            }
        )
    files["manifest.json"] = _json(
        {
            "corpus_version": CORPUS_VERSION,
            "generator": "corpus/generator (python -m corpus.generator)",
            "license": "MIT - fully synthetic, fictional content",
            "documents": manifest_docs,
        }
    )
    files["ground_truth/labels.json"] = _json(
        {
            "corpus_version": CORPUS_VERSION,
            "warning": "Evaluation only. Never upload, index or send to the classifier.",
            "categories": ["contract", "invoice", "policy", "presentation", "technical_report",
                           "correspondence", "needs_review"],
            "documents": labels,
        }
    )
    files["eval_queries.json"] = _json({"corpus_version": CORPUS_VERSION, "k": 5, "queries": EVAL_QUERIES})
    return files


def _json(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m corpus.generator")
    parser.add_argument("--check", action="store_true", help="fail if committed corpus differs")
    parser.add_argument("--out", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    files = build_all()
    if args.check:
        diffs = [rel for rel, data in files.items()
                 if not (args.out / rel).exists() or (args.out / rel).read_bytes() != data]
        if diffs:
            print("corpus differs from a fresh build:", *diffs, sep="\n  ")
            return 1
        print(f"corpus OK ({len(DOCUMENTS)} documents, version {CORPUS_VERSION})")
        return 0
    for rel, data in files.items():
        path = args.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"wrote {len(files)} files for {len(DOCUMENTS)} documents to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
