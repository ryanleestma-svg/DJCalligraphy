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
from .reconcile import minimal_span
from .reference import reference_blocks
from .schema import EDIT_SCHEMA, anchor_is_safe, resolve_anchor

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

* `anchor` must be copied VERBATIM from the base document supplied above and \
must occur EXACTLY ONCE in it. Prefer a whole clause.
* For a TABLE CELL or any short repeated text (a figure like "$375,000", a bare \
list number), keep `anchor` short and set `scope` to unique nearby text - \
normally the row label. Do NOT skip an edit because its text is short: the \
dollar figures in the budget tables matter more than anything else on the page.
* ONE edit per physical red mark. Do not split a single mark into several \
edits, and do not merge two separate marks into one.
* `replacement` is the whole anchor span rewritten as he wants it.
* Do NOT silently fix his grammar. If the sentence does not read correctly as \
marked, transcribe what is actually written and raise a `query` alongside it.
* A `[?]` mark is a question to the author, not an edit - use op `query`.
* A circled letter means the insertion text is written elsewhere; set `xref`.
* Renumbering of list items (4.->5., 5.->6., ...) is handled automatically \
downstream. Report only the paragraph insertion that caused it, never the \
individual number changes.
"""

# Both lenses must be EXHAUSTIVE. An earlier version paired an exhaustive
# line-by-line lens against a free "work down the page" lens, which was not
# forced to cover anything: on one dense page that reader reported 1 mark where
# the other reported 15. Every such cluster is a false disagreement, so it burns
# a tiebreak and comes out yellow. The diversity that is wanted here is in HOW
# the page is searched, not in HOW MUCH of it gets searched.
_EXHAUSTIVE = (
    "You must account for the WHOLE page. Before answering, satisfy yourself "
    "that every red stroke on the page has been assigned to an edit. Missing a "
    "mark is worse than reporting one you are unsure of - an unsure one can be "
    "flagged, a missed one is invisible."
)

READER_LENS = {
    "A": (
        "Search RED-FIRST: scan for red ink anywhere on the page - body, margins, "
        "between lines, top and bottom edges - and for each stroke work out which "
        "printed text it attaches to. " + _EXHAUSTIVE
    ),
    "B": (
        "Search TEXT-FIRST: take each printed line in turn, in order, and ask "
        "whether anything red touches it; only then read the red. This is a "
        "deliberately different search order from the other reader - do not try "
        "to guess what they would say. " + _EXHAUSTIVE
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


def _span(e: dict, base_text: str) -> tuple[int, int] | None:
    at = resolve_anchor(e.get("anchor", ""), base_text, e.get("scope", ""))
    if at is None:
        return None
    return (at, at + len(e.get("anchor", "")))


def _cluster(a_edits, b_edits, base_text, slack: int = 0):
    """Group both readers' edits into one cluster per physical mark.

    Clustering is by OVERLAP OF POSITION IN THE BASE DOCUMENT, not by similarity
    of the quoted text. Two correct readings of the same mark routinely quote
    different amounts of context - that was the dominant discrepancy measured on
    this document - so text-similarity pairing splits them apart and sends each
    to the tiebreaker as a separate edit. On the first full run that produced
    2.22 edits per real edit and left 194 of 292 marked yellow, because every
    unpaired singleton counts as a disagreement.

    Clustering requires spans to genuinely OVERLAP, not merely sit near each
    other. This is single-linkage clustering, so any positive slack chains
    transitively: with 40 characters of slack, six distinct marks 30 characters
    apart collapse into one cluster. Measured directly - the dense page 2 fell
    from 29 reported edits to 3, against 17 real ones. Two readings of the same
    mark overlap by construction, so slack is not needed.

    Returns [(a_items, b_items, span)].
    """
    items = []
    for who, lst in (("A", a_edits), ("B", b_edits)):
        for e in lst:
            items.append((who, e, _span(e, base_text)))

    placed = [i for i in items if i[2]]
    unplaced = [i for i in items if not i[2]]

    placed.sort(key=lambda i: i[2][0])
    clusters: list[list] = []
    for it in placed:
        s, en = it[2]
        for c in clusters:
            cs, ce = c[0][2][0], max(x[2][1] for x in c)
            if s - slack <= ce and cs - slack <= en:
                c.append(it)
                break
        else:
            clusters.append([it])

    out = []
    for c in clusters:
        a = [x[1] for x in c if x[0] == "A"]
        b = [x[1] for x in c if x[0] == "B"]
        out.append((a, b, (min(x[2][0] for x in c), max(x[2][1] for x in c))))
    # anchors we could not place get their own singleton clusters
    for who, e, _ in unplaced:
        out.append(([e] if who == "A" else [], [e] if who == "B" else [], None))
    return out


TIEBREAK = """\
Two readers examined the SAME place on this page and did not agree. Each list \
below is what that reader reported there; a list may hold several fragments of \
what is really one mark, or be empty if that reader saw nothing.

Reader A: {a}
Reader B: {b}

Return one edit per DISTINCT physical red mark in that region - normally one. \
If the two lists are fragments of a single mark, consolidate them into one \
edit. If the region genuinely holds two separate marks, return both. If you \
cannot tell what he wrote with reasonable certainty, return op `query` with a \
`replacement` stating plainly what needs checking - do not guess.
"""


def _tiebreak(page_png: bytes, base_text: str, glossary: str, a_items, b_items):
    """Adjudicate one cluster. Also CONSOLIDATES: a cluster may hold several
    fragments from one reader, and the tiebreaker is asked for a single edit."""
    lens = TIEBREAK.format(
        a=json.dumps(a_items, ensure_ascii=False) if a_items else "(found no edit here)",
        b=json.dumps(b_items, ensure_ascii=False) if b_items else "(found no edit here)",
    )
    edits, _ = _call(page_png, base_text, glossary, lens)
    # The tiebreaker may legitimately split a cluster that holds two marks,
    # so take everything it returns rather than only the first edit.
    return [(e, "red" if e.get("op") == "query" else "yellow") for e in edits]


def read_page(page_png: bytes, base_text: str, glossary: str, page_no: int) -> tuple[list[Edit], list[dict]]:
    # The two readers are independent by construction, so run them together.
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(_call, page_png, base_text, glossary, READER_LENS["A"])
        fb = pool.submit(_call, page_png, base_text, glossary, READER_LENS["B"])
        a_edits, a_terms = fa.result()
        b_edits, b_terms = fb.result()

    clusters = _cluster(a_edits, b_edits, base_text)

    settled: list[tuple[dict, str, list[str]]] = []
    contested: list[tuple[list, list]] = []
    for a_items, b_items, _span_ in clusters:
        # Compare the EFFECTIVE CHANGE each reader describes, combining any
        # fragments, rather than requiring one edit each with matching text.
        # A reader may legitimately split one mark into two adjacent edits.
        agreed = False
        if a_items and b_items:
            ca = " ".join(
                minimal_span(x.get("anchor", ""), x.get("replacement", ""))[2] for x in a_items
            )
            cb = " ".join(
                minimal_span(x.get("anchor", ""), x.get("replacement", ""))[2] for x in b_items
            )
            agreed = _sim(ca, cb) > 0.90
        if agreed:
            settled.append((a_items[0], "green", ["A", "B"]))
        else:
            contested.append((a_items, b_items))

    # Tiebreaks are independent of one another - fan them out. Running them
    # serially made dense pages take 200s+ on the first full run.
    if contested:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(_tiebreak, page_png, base_text, glossary, a, b)
                for a, b in contested
            ]
            for f in futures:
                for src, conf in f.result():
                    settled.append((src, conf, ["A", "B", "T"]))

    out: list[Edit] = []
    for src, conf, who in settled:
        if not anchor_is_safe(src.get("anchor", ""), base_text, src.get("scope", "")):
            conf = "red"
        out.append(
            Edit(
                op=src.get("op", "replace"),
                anchor=src.get("anchor", ""),
                replacement=src.get("replacement", ""),
                evidence=src.get("evidence", ""),
                xref=src.get("xref"),
                scope=src.get("scope", ""),
                confidence=conf,
                page=page_no,
                readers=who,
            )
        )
    return out, (a_terms + b_terms)
