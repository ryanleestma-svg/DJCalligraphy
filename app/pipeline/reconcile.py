"""Document-wide passes that run after every page has been read.

Three of these exist because of failure modes measured on the DIP motion:

* span normalisation - boundary disagreement (not misreading) was the single
  largest source of apparent error between two correct readings of the same mark.
* renumber cascade   - 45 of 169 edits in the known-good output were the knock-on
  renumbering from ONE inserted paragraph, and were also the only real source of
  ambiguous anchors.
* glossary / xref    - an edit on page 1 introduced a defined term that changed
  how page 2 had to be read. Page-independent processing cannot see that.
"""

from __future__ import annotations

import re
from dataclasses import replace as _dc_replace

from .models import Edit

LIST_NUM = re.compile(r"^(\s*)(\d+)(\.\s*)$")


# --------------------------------------------------------------------------- #
# span normalisation
# --------------------------------------------------------------------------- #
def minimal_span(anchor: str, replacement: str) -> tuple[str, str, str, str]:
    """Strip the common prefix/suffix shared by anchor and replacement.

    Returns (prefix, old_core, new_core, suffix). Applying the edit then means
    deleting old_core and inserting new_core, which keeps the tracked change as
    tight as possible instead of restriking a whole clause.
    """
    a, b = anchor, replacement
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    # back off to a word boundary so we never split mid-token
    while i > 0 and (a[i - 1].isalnum() or a[i - 1] in "'’"):
        i -= 1
    j = 0
    while (
        j < min(len(a), len(b)) - i
        and a[len(a) - 1 - j] == b[len(b) - 1 - j]
    ):
        j += 1
    while j > 0 and (a[len(a) - j].isalnum() or a[len(a) - j] in "'’"):
        j -= 1
    return a[:i], a[i : len(a) - j], b[i : len(b) - j], a[len(a) - j :] if j else ""


# A wholesale deletion of this much text is nearly always a reporting error -
# a partial strike returned with an empty replacement - rather than a genuine
# instruction to remove the passage. Measured on the reference document, 58
# such edits existed and one of them deleted 155 characters where the reported
# evidence described a 12-character strike.
SUSPECT_DELETE_CHARS = 60


def normalise(edits: list[Edit]) -> list[Edit]:
    out = []
    for e in edits:
        if e.op in ("query", "footnote", "split_para", "stet"):
            out.append(e)
            continue
        if not (e.replacement or "").strip() and len(e.anchor) > SUSPECT_DELETE_CHARS:
            # Do NOT delete the passage. A strike across a long span nearly always
            # means "rewrite this", with the replacement written in the margin or
            # on the back of the sheet - and the reader has evidently not captured
            # it. Removing the text represents a rewrite as an amputation, and it
            # is the one error the reviewer cannot spot by reading the result:
            # what is gone leaves no trace. Measured on the reference document, 15
            # such edits removed 2,169 characters and cost 0.042 of document
            # similarity outright.
            #
            # Emit a red query instead. The passage survives, the strike is
            # surfaced, and one click removes the marker.
            e.op = "query"
            e.confidence = "red"
            e.replacement = (
                "[?? this passage appears struck through in full - confirm whether "
                "it should be deleted, and supply the replacement text if he wrote "
                "one in the margin or on the back of the sheet ??]"
            )
        _, old_core, new_core, _ = minimal_span(e.anchor, e.replacement)
        if not old_core and not new_core:
            continue  # no-op
        out.append(e)
    return out


# --------------------------------------------------------------------------- #
# STET - cancels a previously marked revision
# --------------------------------------------------------------------------- #
def apply_stet(edits: list[Edit]) -> list[Edit]:
    stets = [e for e in edits if e.op == "stet"]
    if not stets:
        return edits
    keep = []
    for e in edits:
        if e.op == "stet":
            continue
        cancelled = any(
            s.page == e.page and (s.anchor in e.anchor or e.anchor in s.anchor)
            for s in stets
        )
        if not cancelled:
            keep.append(e)
    return keep


# --------------------------------------------------------------------------- #
# circled-letter cross references
# --------------------------------------------------------------------------- #
def resolve_xrefs(edits: list[Edit]) -> list[Edit]:
    """Match each circled-letter marker to its payload, wherever it landed.

    Unresolved markers become red queries rather than disappearing - the payload
    may be in a memo or voice recording that never reached the scan.
    """
    payloads = {
        e.xref: e.replacement
        for e in edits
        if e.xref and e.replacement.strip() and e.op != "insert"
    }
    out = []
    for e in edits:
        if e.xref and not e.replacement.strip():
            body = payloads.get(e.xref)
            if body:
                out.append(_dc_replace(e, replacement=e.anchor + " " + body))
            else:
                out.append(
                    _dc_replace(
                        e,
                        op="query",
                        confidence="red",
                        replacement=(
                            f"[?? insertion ({e.xref}) referenced here but its text "
                            f"was not found in the scan - supply the memo or recording ??]"
                        ),
                    )
                )
        else:
            out.append(e)
    return out


# --------------------------------------------------------------------------- #
# renumbering cascade
# --------------------------------------------------------------------------- #
def renumber_after_split(paragraphs: list[str], split_at: int) -> list[tuple[int, str, str]]:
    """Derive the renumbering caused by inserting a paragraph.

    Returns (paragraph_index, old_number_text, new_number_text).

    The document types its list numbers as literal text ("4.", "5.") rather than
    using Word's automatic numbering, so every subsequent number has to change.
    Deriving them here removes 45 chances to fumble a transcription.
    """
    out = []
    for idx in range(split_at + 1, len(paragraphs)):
        m = LIST_NUM.match(paragraphs[idx][:8])
        if not m:
            continue
        n = int(m.group(2))
        out.append((idx, f"{n}.", f"{n + 1}."))
    return out


# --------------------------------------------------------------------------- #
# overlap consolidation
# --------------------------------------------------------------------------- #
_RANK = {"green": 0, "yellow": 1, "red": 2}


def consolidate(edits: list[Edit], paragraphs: list[str]) -> list[Edit]:
    """Ensure no two applied edits touch the same text.

    Two readers, plus a tiebreaker allowed to split a cluster, can emit several
    edits covering the same span. Applying all of them rewrites the same run
    twice and corrupts it - measured end to end, applying every produced edit
    left the document FURTHER from the correct answer than making no edits at
    all. Where spans overlap, keep exactly one: best confidence first, then the
    widest span (which usually carries the whole change rather than a fragment).

    Non-textual ops (queries, footnotes, paragraph inserts) are passed through:
    they add rather than rewrite, so they cannot collide.
    """
    passthrough = [e for e in edits if e.op in ("query", "footnote", "split_para", "insert_para")]
    textual = [e for e in edits if e not in passthrough]

    spans = []
    for e in textual:
        pi = next((i for i, t in enumerate(paragraphs) if e.anchor and e.anchor in t), None)
        if pi is None:
            spans.append((e, None))
            continue
        at = paragraphs[pi].index(e.anchor)
        pre, old_core, _new, _sfx = minimal_span(e.anchor, e.replacement)
        s = at + len(pre)
        spans.append((e, (pi, s, s + max(len(old_core), 1))))

    placed = [(e, sp) for e, sp in spans if sp]
    unplaced = [e for e, sp in spans if not sp]

    # Ordering decides which edit wins each overlap group.
    #
    # Edits that CARRY REPLACEMENT TEXT must outrank pure deletions. When Dave
    # strikes a passage and writes its replacement in the margin, the readers
    # report that as two overlapping edits - a delete and an insert. Dropping
    # the one with the text turns a rewrite into a wipe: 20 paragraphs were
    # being emptied that should merely have been reworded, every one of them
    # green because both readers had agreed on each half.
    def rank(item):
        e, sp = item
        has_text = 0 if (e.replacement or "").strip() else 1
        return (has_text, _RANK.get(e.confidence, 3), -(sp[2] - sp[1]))

    placed.sort(key=rank)

    kept: list[tuple[Edit, tuple]] = []
    dropped = 0
    for e, sp in placed:
        clash = any(
            k[1][0] == sp[0] and sp[1] < k[1][2] and k[1][1] < sp[2]
            for k in kept
        )
        if clash:
            dropped += 1
            continue
        kept.append((e, sp))

    out = [e for e, _ in kept] + unplaced + passthrough
    return out
