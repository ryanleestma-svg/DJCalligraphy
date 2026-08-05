"""Input normalisation: scan PDF -> page images, base .docx -> text."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf
from lxml import etree
from PIL import Image

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A duplex scan of a single-sided document yields a blank image for every
# reverse side. Observed on the DIP motion: 56 scanned images for 28 pages.
BLANK_INK_FRACTION = 0.002


@dataclass
class ScanPage:
    index: int          # 1-based sequence among *content* pages
    source_index: int   # 0-based index in the original PDF
    png: bytes


def _ink_fraction(png: bytes) -> float:
    im = Image.open(io.BytesIO(png)).convert("L")
    im.thumbnail((400, 400))
    px = im.getdata()
    dark = sum(1 for p in px if p < 200)
    return dark / max(1, len(px))


def render_scan(pdf_path: str | Path, dpi: int = 170) -> list[ScanPage]:
    """Render the markup scan, dropping blank reverse sides."""
    doc = fitz.open(str(pdf_path))
    pages: list[ScanPage] = []
    n = 0
    for i, page in enumerate(doc):
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        if _ink_fraction(png) < BLANK_INK_FRACTION:
            continue
        n += 1
        pages.append(ScanPage(index=n, source_index=i, png=png))
    doc.close()
    return pages


def docx_paragraphs(docx_path: str | Path) -> list[str]:
    """Plain text of every body paragraph, in order."""
    with zipfile.ZipFile(str(docx_path)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(W + "p"):
        out.append("".join(t.text or "" for t in p.iter(W + "t")))
    return out


def docx_text(docx_path: str | Path) -> str:
    return "\n".join(docx_paragraphs(docx_path))


def original_text_from_tracked(docx_path: str | Path) -> str:
    """Reconstruct the pre-markup text of a document that already has
    tracked changes: keep normal runs and deletions, drop insertions.

    Used by the evaluation harness, and by round-2 runs where Dave marks up
    a draft that already carries revisions.
    """
    with zipfile.ZipFile(str(docx_path)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(W + "p"):
        buf = []
        for r in p.iter(W + "r"):
            if any(a.tag == W + "ins" for a in r.iterancestors()):
                continue
            for t in r:
                if t.tag in (W + "t", W + "delText"):
                    buf.append(t.text or "")
        paras.append("".join(buf))
    return "\n".join(paras)
