"""Two-reader consensus interpretation of one marked-up page.

Confidence is *measured*, not self-reported:
    both readers agree              -> green
    disagreed, tiebreaker resolved  -> yellow
    tiebreaker unsure / unresolved  -> red

Self-reported confidence from a single model is poorly calibrated; inter-reader
agreement is an empirical signal. This mirrors the three-person-team prompt that
produced the known-good output, but only pays for a third call on disagreement.
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import anthropic

from .models import Edit
from .reference import reference_blocks
from .schema import EDIT_SCHEMA, anchor_is_safe

MODEL = os.environ.get("MARKUP_MODEL", "claude-opus-5")
MAX_TOKENS = 8000

TOOL = {
    "name": "report_edits",
    "description": "Report every red-pen edit found on this page.",
    "input_schema": EDIT_SCHEMA,
}

BASE_TASK = """\
You are reading ONE page of a legal document that David S. Jennis has marked up \
in red pen. The printed text is the draft; everything in red is his edit.

Report EVERY red mark on the page. Rules that matter:

* `anchor` must be copied VERBATIM from the base-document text supplied above, \
at least 20 characters, and unique. Never anchor on a bare list number.
* `replacement` is the whole anchor span rewritten as he wants it.
* Do NOT silently fix his grammar. If the sentence does not read correctly as \
marked, transcribe what is actually written and raise a `query` alongside it.
* A `[?]` mark is a question to the author, not an edit - use op `query`.
* A circled letter means the insertion text is written elsewhere; set `xref`.
* Renumbering of list items (4.->5., 5.->6., ...) is handled automatically \
downstream. Report only the paragraph insertion that caused it, never the \
individual number changes.
"""

READER_LENS = {
    "A": (
        "Work down the page mark by mark, in reading order. For each red mark, "
        "identify what kind of mark it is first, then what it says."
    ),
    "B": (
        "Work from the printed text outward. Take each printed line in turn and "
        "ask whether anything red touches it; only then read the red. This is a "
        "deliberately different search strategy from the other reader - do not "
        "try to guess what they would say."
    ),
}


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _call(page_png: bytes, base_text: str, glossary: str, lens: str) -> list[dict]:
    blocks = reference_blocks()
    blocks.append(
        {
            "type": "text",
            "text": "BASE DOCUMENT (the unmarked draft, full text):\n\n" + base_text,
            "cache_control": {"type": "ephemeral"},
        }
    )
    if glossary:
        blocks.append({"type": "text", "text": "DEFINED TERMS ESTABLISHED EARLIER:\n" + glossary})
    blocks.append(
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(page_png).decode("ascii"),
            },
        }
    )
    blocks.append({"type": "text", "text": BASE_TASK + "\n" + lens})

    resp = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "report_edits"},
        messages=[{"role": "user", "content": blocks}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input.get("edits", []), block.input.get("defined_terms", [])
    return [], []


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _pair(a_edits: list[dict], b_edits: list[dict], thresh: float = 0.6):
    """Match reader A's edits to reader B's by anchor similarity."""
    pairs, used = [], set()
    for ea in a_edits:
        best, score = None, 0.0
        for i, eb in enumerate(b_edits):
            if i in used:
                continue
            s = _sim(ea.get("anchor", ""), eb.get("anchor", ""))
            if s > score:
                score, best = s, i
        if score >= thresh:
            used.add(best)
            pairs.append((ea, b_edits[best]))
        else:
            pairs.append((ea, None))
    for i, eb in enumerate(b_edits):
        if i not in used:
            pairs.append((None, eb))
    return pairs


TIEBREAK = """\
Two readers disagree about one red-pen edit on this page. Decide which is right, \
or supply a third reading if both are wrong.

Reader A: {a}
Reader B: {b}

Answer with the single correct edit. If you cannot tell what he wrote with \
reasonable certainty, return op `query` with a `replacement` that states plainly \
what needs checking - do not guess.
"""


def _tiebreak(page_png: bytes, base_text: str, glossary: str, a, b) -> tuple[dict, str]:
    lens = TIEBREAK.format(
        a=json.dumps(a, ensure_ascii=False) if a else "(found no edit here)",
        b=json.dumps(b, ensure_ascii=False) if b else "(found no edit here)",
    )
    edits, _ = _call(page_png, base_text, glossary, lens)
    if not edits:
        return None, "red"
    e = edits[0]
    return e, ("red" if e.get("op") == "query" else "yellow")


def read_page(page_png: bytes, base_text: str, glossary: str, page_no: int) -> tuple[list[Edit], list[dict]]:
    # The two readers are independent by construction, so run them together.
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(_call, page_png, base_text, glossary, READER_LENS["A"])
        fb = pool.submit(_call, page_png, base_text, glossary, READER_LENS["B"])
        a_edits, a_terms = fa.result()
        b_edits, b_terms = fb.result()

    out: list[Edit] = []
    for ea, eb in _pair(a_edits, b_edits):
        if ea and eb and _sim(ea.get("replacement", ""), eb.get("replacement", "")) > 0.95:
            src, conf, who = ea, "green", ["A", "B"]
        else:
            src, conf = _tiebreak(page_png, base_text, glossary, ea, eb)
            who = ["A", "B", "T"]
            if src is None:
                continue
        if not anchor_is_safe(src.get("anchor", "")):
            conf = "red"
        out.append(
            Edit(
                op=src.get("op", "replace"),
                anchor=src.get("anchor", ""),
                replacement=src.get("replacement", ""),
                evidence=src.get("evidence", ""),
                xref=src.get("xref"),
                confidence=conf,
                page=page_no,
                readers=who,
            )
        )
    return out, (a_terms + b_terms)
