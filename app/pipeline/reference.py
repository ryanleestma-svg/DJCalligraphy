"""Dave's permanently-stored handwriting reference.

Loaded once at import and sent as a cached prompt prefix on every run, so the
four sheet images are billed and transferred once rather than per page.
"""

from __future__ import annotations

import base64
import functools
from pathlib import Path

REF_DIR = Path(__file__).resolve().parents[2] / "reference" / "dave"
SHEET_PAGES = sorted(REF_DIR.glob("sheet_p*.png"))
PROFILE_PATH = REF_DIR / "profile.md"


@functools.lru_cache(maxsize=1)
def profile_text() -> str:
    return PROFILE_PATH.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _sheet_b64() -> tuple[str, ...]:
    return tuple(
        base64.b64encode(p.read_bytes()).decode("ascii") for p in SHEET_PAGES
    )


def reference_blocks() -> list[dict]:
    """Content blocks for the cached prefix: sheet images + markup profile.

    The final block carries cache_control, so everything above it is reused
    across every page of every run.
    """
    blocks: list[dict] = [
        {
            "type": "text",
            "text": (
                "REFERENCE — the handwriting of David S. Jennis. He has "
                "difficulty typing with his left hand and marks up printed "
                "drafts in red pen. The four images below are his own hand: "
                "alphabet, digits, ligatures, shorthand, symbols, and a worked "
                "demonstration of every markup convention he uses. Match his "
                "letterforms against these images directly."
            ),
        }
    ]
    for b64 in _sheet_b64():
        blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            }
        )
    blocks.append(
        {
            "type": "text",
            "text": "MARKUP GRAMMAR\n\n" + profile_text(),
            "cache_control": {"type": "ephemeral"},
        }
    )
    return blocks
