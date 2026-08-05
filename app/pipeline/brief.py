"""One-time document comprehension pass.

The hand-run process that produced the reference output began by having the
team read the whole base document and understand "the structure, the tone,
where it's trying to go... a base level understanding of what we are
modifying" BEFORE looking at a single edit.

The per-page pipeline supplies the full base text as reference data, which is
not the same thing: nothing obliges a reader to form a view of the document
before interpreting marks on one page of it. This builds that view once and
carries it into every page read.
"""

from __future__ import annotations

from .interpret import MODEL, _client

PROMPT = """\
Read this legal document in full and build a working understanding of it before
any editing begins. Cover, briefly:

* what relief it seeks and on what statutory basis
* its structure - the sections and what each is doing
* the defined terms it establishes, and the exact wording of each
* its register: how formal, how hedged, how it refers to the parties
* the numbers and dollar figures that carry weight, and where they recur

Be specific and compact. This will be given to readers who must interpret
handwritten edits on individual pages, so favour the things that let someone
judge whether a proposed change is plausible for THIS document.

DOCUMENT:

{text}
"""


def build_brief(base_text: str, max_tokens: int = 16000) -> str:
    """Build the brief. Raises if the model returned no prose.

    max_tokens must leave room for BOTH reasoning and the answer. At 3000 the
    entire budget was consumed by a thinking block, the response carried no text
    block at all, and the brief came back as a bare header - which silently
    turned an experimental arm into a duplicate of its control.
    """
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": PROMPT.format(text=base_text)}],
    )
    body = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not body:
        raise RuntimeError(
            f"document brief came back empty (stop_reason={resp.stop_reason}, "
            f"blocks={[b.type for b in resp.content]}); raise max_tokens"
        )
    return "UNDERSTANDING OF THIS DOCUMENT (built by reading it in full):\n\n" + body
