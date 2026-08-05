"""Read the whole document in ONE continuous conversation.

This is the literal shape of the hand-run process that produced the reference
output: the model reads the document, then works through the marked-up pages in
sequence, carrying everything it has seen and decided in working memory.

It is NOT what `interpret.read_page` does. That issues an independent API call
per page; nothing survives between pages except a small glossary. Earlier
experiments that pasted a document brief or a list of prior edits into those
isolated calls were testing a summary, not a conversation, and should not be
read as having tested this.

What a conversation can do that isolated calls cannot:
  * revise an earlier reading in light of something later on the page or a
    later page
  * hear whether an edited sentence still scans, having just written it
  * carry an argument's thread across a page break, which is an artifact of
    printing and lands mid-sentence
"""

from __future__ import annotations

import base64

from .interpret import MODEL, TOOL, _client, _sane_edits, _sane_terms
from .models import Edit
from .reference import reference_blocks

OPENING = """\
You are going to read a legal document that David S. Jennis has marked up in red
pen, one page at a time, in order.

Before the first page: read the base document below in full. Understand what it
seeks, how it is structured, the terms it defines and the exact words it uses
for them, and how formally it speaks. You will need that to judge whether a
proposed change is plausible for THIS document.

As each page arrives, report the red-pen edits on it. You keep everything from
earlier pages, so use it:

  * if he renamed something on an earlier page, expect the new name later
  * if a mark is ambiguous alone but obvious given what he has already done,
    say so and read it accordingly
  * having written an edit, check the sentence still reads. If it does not,
    transcribe what is actually written and raise a `query` - never quietly
    repair his grammar
  * if a later page shows an earlier reading was wrong, say so plainly

BASE DOCUMENT:

{base}
"""

PAGE = """\
Page {n} of {total}. Report every red mark on it.

`anchor` must be copied verbatim from the base document and occur exactly once;
for a short or repeated anchor (a table figure, a bare list number) set `scope`
to unique nearby text instead of padding the anchor. `replacement` is the anchor
span as it should end up. One edit per physical mark.
"""


def read_document(pages, base_text: str, progress=None) -> tuple[list[Edit], list[dict]]:
    """Walk every page in a single accumulating conversation."""
    client = _client()
    blocks = reference_blocks()
    blocks.append(
        {
            "type": "text",
            "text": OPENING.format(base=base_text),
            "cache_control": {"type": "ephemeral"},
        }
    )
    messages = [{"role": "user", "content": blocks}]

    out: list[Edit] = []
    terms: list[dict] = []
    total = len(pages)

    for i, p in enumerate(pages, 1):
        content = []
        for img in p.images():
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(img).decode("ascii"),
                    },
                }
            )
        note = p.note()
        content.append({"type": "text", "text": (note + "\n\n" if note else "") + PAGE.format(n=i, total=total)})
        if i > 1:
            messages.append({"role": "user", "content": content})
        else:
            messages[0]["content"].extend(content)

        resp = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "report_edits"},
            messages=messages,
        )
        # keep the assistant turn so the next page inherits it
        messages.append({"role": "assistant", "content": resp.content})

        edits = []
        for b in resp.content:
            if b.type == "tool_use":
                edits = _sane_edits(b.input.get("edits", []))
                terms.extend(_sane_terms(b.input.get("defined_terms", [])))
                # a tool_use turn must be answered before the next user turn
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": b.id,
                                "content": "Recorded.",
                            }
                        ],
                    }
                )
        for e in edits:
            out.append(
                Edit(
                    op=e.get("op", "replace"),
                    anchor=e.get("anchor", ""),
                    replacement=e.get("replacement", ""),
                    evidence=e.get("evidence", ""),
                    xref=e.get("xref"),
                    scope=e.get("scope", ""),
                    confidence="yellow",   # a single reader: no agreement signal
                    page=p.index,
                    readers=["conv"],
                )
            )
        if progress:
            progress(i, total, len(edits))
    return out, terms
