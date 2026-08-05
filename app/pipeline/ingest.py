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
# reverse side. Observed on the DIP motion: 56 images for a 28-page document.
#
# Filter on INK, never on the text layer. Three "blank" reverse sides carried
# no extractable text but were covered in red pen: one held a footnote body
# (his "FN" mark) and one held the payloads for two circled cross-references
# (his (A)/(B) convention). Dropping pages with no text would silently discard
# the content of those insertions.
#
# The two populations separate cleanly by two orders of magnitude:
#   truly blank  <= 0.0001     handwriting-only  >= 0.02
BLANK_INK_FRACTION = 0.002


# Vision downscales any image whose long edge exceeds this, so a full letter
# page rendered at 170 dpi actually reaches the model at ~143 dpi. Handwriting
# detail cannot be added by rendering higher - it is resampled away. Sending
# horizontal bands instead lets each band fill the budget: two bands give ~285
# effective dpi, three give ~428.
VISION_LONG_EDGE = 1568


@dataclass
class ScanPage:
    index: int          # 1-based sequence among *content* pages
    source_index: int   # 0-based index in the original PDF
    png: bytes
    tiles: list[bytes] = None   # optional high-resolution grid tiles
    backs: list[bytes] = None   # reverse sides he continued onto

    def images(self) -> list[bytes]:
        """Front (or its tiles) followed by any continuation sheets."""
        front = self.tiles if self.tiles else [self.png]
        return front + (self.backs or [])

    def note(self) -> str:
        """Caption telling the reader what the images are."""
        parts = []
        if self.tiles:
            parts.append(
                f"The page front is supplied as {len(self.tiles)} overlapping tiles "
                "at high magnification; a mark near a boundary appears twice - "
                "report it ONCE."
            )
        if self.backs:
            parts.append(
                f"After the front there {'is' if len(self.backs)==1 else 'are'} "
                f"{len(self.backs)} image(s) of the BACK of this same sheet. When he "
                "runs out of margin he turns the sheet over and keeps writing, "
                "drawing a line from the front to the back. Treat the back as a "
                "CONTINUATION OF THIS PAGE, not as a page of its own: it holds the "
                "text for carets and circled letters marked on the front. Match each "
                "circled letter on the back to the same circled letter on the front."
            )
        return " ".join(parts)


def _ink_fraction(png: bytes) -> float:
    im = Image.open(io.BytesIO(png)).convert("L")
    im.thumbnail((400, 400))
    px = im.getdata()
    dark = sum(1 for p in px if p < 200)
    return dark / max(1, len(px))


def _grid(page, cols: int, rows: int, overlap: float) -> list[bytes]:
    """Split a page into an overlapping grid, each tile filling the pixel budget.

    Tiles must be narrower as well as shorter. Horizontal bands alone do not
    help: the page WIDTH stays the long edge, so the scale factor is pinned at
    1568/8.5in ~ 184 dpi however many bands are cut - against 143 dpi for the
    whole page. Halving the width as well takes a letter page to ~285 dpi.

    Tiles overlap because a caret, a margin line or a struck phrase sitting on a
    boundary would otherwise be cut and read as two partial marks.
    """
    r = page.rect
    w, h = r.width / cols, r.height / rows
    px, py = w * overlap, h * overlap
    out = []
    for ry in range(rows):
        for cx in range(cols):
            clip = fitz.Rect(
                max(r.x0, r.x0 + cx * w - px), max(r.y0, r.y0 + ry * h - py),
                min(r.x1, r.x0 + (cx + 1) * w + px),
                min(r.y1, r.y0 + (ry + 1) * h + py),
            )
            want = VISION_LONG_EDGE / max(clip.width, clip.height)
            out.append(
                page.get_pixmap(matrix=fitz.Matrix(want, want), clip=clip).tobytes("png")
            )
    return out


# A scanned side carrying ink but essentially no printed text is not a page of
# its own: it is the BACK of the preceding sheet, which he turns over when the
# margin runs out, drawing a line from front to back. His circled-letter
# convention lives on exactly these sides - the marker on the front, the text on
# the back. Attaching them to their front makes that a local match rather than a
# document-wide search.
CONTINUATION_MAX_TEXT = 500


def render_scan(pdf_path: str | Path, dpi: int = 170, cols: int = 1,
                rows: int = 1, overlap: float = 0.12) -> list[ScanPage]:
    """Render the markup scan.

    Blank reverse sides are dropped. Reverse sides he has written on are
    attached to the front of their own sheet as continuations.

    cols/rows > 1 additionally renders each front as an overlapping grid of
    tiles at much higher effective resolution; see VISION_LONG_EDGE.
    """
    doc = fitz.open(str(pdf_path))
    pages: list[ScanPage] = []
    n = 0
    for i, page in enumerate(doc):
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        if _ink_fraction(png) < BLANK_INK_FRACTION:
            continue
        printed = len(page.get_text().strip()) > CONTINUATION_MAX_TEXT
        if not printed and pages:
            pages[-1].backs = (pages[-1].backs or []) + [png]
            continue
        n += 1
        band = _grid(page, cols, rows, overlap) if (cols > 1 or rows > 1) else None
        pages.append(ScanPage(index=n, source_index=i, png=png, tiles=band))
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
