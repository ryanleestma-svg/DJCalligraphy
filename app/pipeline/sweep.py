"""Final coherence sweep over the REVISED document.

Every other stage in this pipeline looks at the original text and the ink. None
of them ever looks at the result. So nothing checks the thing a human checks
last: with all the edits in, does the document still say something, and does it
still say what it was trying to say?

That is the step that catches the failure this system is most prone to. A
misread word produces a sentence that is locally plausible and globally wrong -
"dedicated to and debt the Debtors' obligations" reads as an edit until you read
the sentence. Marks are interpreted one at a time; prose has to work as a whole.

The sweep does not rewrite anything. It reads the revised text section by
section against an understanding of the original, and recolours suspect edits
red so they land in front of the reviewer. Silent repair is exactly what must
not happen to a legal filing.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from .interpret import MODEL, _client
from .models import Edit

FLAG_TOOL = {
    "name": "flag_passages",
    "description": "Flag revised passages that do not read correctly.",
    "input_schema": {
        "type": "object",
        "required": ["passages"],
        "properties": {
            "passages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["quote", "problem"],
                    "properties": {
                        "quote": {
                            "type": "string",
                            "description": (
                                "Verbatim text from the REVISED passage that does "
                                "not work - as short as possible while still unique."
                            ),
                        },
                        "problem": {
                            "type": "string",
                            "enum": [
                                "ungrammatical",
                                "contradicts_document",
                                "term_inconsistent",
                                "meaning_lost",
                                "duplicated_text",
                            ],
                        },
                        "note": {"type": "string", "description": "One sentence."},
                    },
                },
            }
        },
    },
}

PROMPT = """\
Below is one section of a legal document BEFORE and AFTER a set of handwritten
edits was applied. The edits were transcribed from an attorney's red pen, and a
misread word can produce a sentence that looks like an edit but does not
actually parse.

Read the AFTER text as prose. Flag any passage that:

  * does not parse, or has words obviously dropped or doubled
  * contradicts something the document establishes elsewhere
  * uses a defined term inconsistently with how the document defines it
  * has lost the meaning the BEFORE text was carrying

Do NOT flag: changes of style, changes you merely disagree with, or anything
that reads correctly. An attorney chose these words. You are looking only for
passages that came out broken.

BEFORE:
{before}

AFTER:
{after}
"""


def _sections(before: list[str], after: list[str], size: int = 25):
    """Walk both texts in aligned blocks of paragraphs."""
    for i in range(0, max(len(before), len(after)), size):
        yield "\n".join(before[i:i + size]), "\n".join(after[i:i + size])


def _flag_one(before: str, after: str) -> list[dict]:
    if not after.strip():
        return []
    try:
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=4000,
            tools=[FLAG_TOOL],
            tool_choice={"type": "tool", "name": "flag_passages"},
            messages=[{"role": "user", "content": PROMPT.format(before=before, after=after)}],
        )
    except Exception:
        return []
    for b in resp.content:
        if b.type == "tool_use":
            raw = b.input.get("passages", [])
            return [p for p in raw if isinstance(p, dict) and isinstance(p.get("quote"), str)]
    return []


def sweep(before_paras: list[str], after_paras: list[str], edits: list[Edit],
          max_workers: int = 6) -> tuple[list[Edit], list[dict]]:
    """Read the revised document and recolour edits inside flagged passages red.

    Returns (edits, flags). Edits are never rewritten - only recoloured, so a
    suspect passage is surfaced for review rather than silently altered.
    """
    blocks = list(_sections(before_paras, after_paras))
    flags: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for res in pool.map(lambda ba: _flag_one(*ba), blocks):
            flags.extend(res)

    for f in flags:
        q = f.get("quote", "")
        if len(q) < 8:
            continue
        for e in edits:
            new = (e.replacement or "").strip()
            if not new:
                continue
            # the edit contributed text to a passage that came out broken
            if new in q or (len(new) > 12 and new[:40] in q) or q in new:
                e.confidence = "red"
                f.setdefault("edits", []).append(e.anchor[:60])
    return edits, flags
