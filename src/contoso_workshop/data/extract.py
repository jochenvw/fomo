"""Text extraction for PDF and PPTX (no OCR). Document text is untrusted data."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

SUPPORTED = {".pdf", ".pptx", ".txt", ".md"}


@dataclass
class Extraction:
    text: str
    title: str | None
    fmt: str
    pages: int
    warnings: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf(data: bytes) -> Extraction:
    from pypdf import PdfReader

    warnings: list[str] = []
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        return Extraction("", None, "pdf", 0, ["encrypted PDF skipped"])
    texts = []
    for i, page in enumerate(reader.pages):
        try:
            texts.append(page.extract_text() or "")
        except Exception as exc:  # pypdf raises many types on odd PDFs
            warnings.append(f"page {i + 1}: {type(exc).__name__}")
    title = None
    try:
        meta = reader.metadata
        title = str(meta.title) if meta and meta.title else None
    except Exception:
        warnings.append("unreadable PDF metadata")
    text = _clean("\n\n".join(texts))
    if not text:
        warnings.append("no extractable text (scanned? OCR is out of scope)")
    return Extraction(text, title, "pdf", len(reader.pages), warnings)


def extract_pptx(data: bytes) -> Extraction:
    from pptx import Presentation

    warnings: list[str] = []
    prs = Presentation(io.BytesIO(data))
    slides_text = []
    first_title = None
    for idx, slide in enumerate(prs.slides, start=1):
        chunks = []
        title_shape = slide.shapes.title
        if title_shape is not None and title_shape.has_text_frame:
            t = title_shape.text_frame.text.strip()
            if t:
                chunks.append(t)
                first_title = first_title or t
        for shape in slide.shapes:
            if shape == title_shape:
                continue
            if shape.has_text_frame:
                chunks.append(shape.text_frame.text)
            elif getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    chunks.append(" | ".join(cell.text for cell in row.cells))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                chunks.append(f"Notes: {notes}")
        slides_text.append(f"[Slide {idx}]\n" + "\n".join(c for c in chunks if c.strip()))
    core_title = prs.core_properties.title or None
    text = _clean("\n\n".join(slides_text))
    if not text:
        warnings.append("deck has no text")
    return Extraction(text, core_title or first_title, "pptx", len(prs.slides), warnings)


def extract(name: str, data: bytes) -> Extraction:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(data)
    if suffix == ".pptx":
        return extract_pptx(data)
    if suffix in (".txt", ".md"):
        text = _clean(data.decode("utf-8", errors="replace"))
        title = text.splitlines()[0][:200] if text else None
        return Extraction(text, title, suffix[1:], 1, [] if text else ["empty file"])
    return Extraction("", None, suffix.lstrip("."), 0, [f"unsupported format {suffix}"])
