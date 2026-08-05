"""Adversarial verification of consolidated edits.

The two-reader consensus decides WHAT a mark says. It does not ask whether the
mark exists at all, or whether the final consolidated wording is actually
supported by the ink on the page. Measured on the reference document, 62 of 352
produced edits corresponded to nothing in the ground truth, and 46 of the 110
real edits found carried wrong text.

This pass shows the verifier the page and one finished edit and asks it to
refute. Default-to-reject on uncertainty is deliberate: a dropped edit is a
visible omission the reviewer can catch against the paper original, whereas a
confidently wrong edit reads as correct and can be accepted by mistake.
"""

from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor

import anthropic

from .interpret import MODEL, _client
from .models import Edit
from .reference import reference_blocks

VERDICT_TOOL = {
    "name": "verdict",
    "description": "Judge whether a proposed edit is supported by the page.",
    "input_schema": {
        "type": "object",
        "required": ["supported", "reason"],
        "properties": {
            "supported": {
                "type": "string",
                "enum": ["yes", "partly", "no"],
                "description": (
                    "yes  - the red ink clearly says this; "
                    "partly - a real mark is here but the wording is off; "
                    "no   - there is no such mark, or it cannot be made out"
                ),
            },
            "corrected": {
                "type": "string",
                "description": "If 'partly', the wording the ink actually supports.",
            },
            "reason": {"type": "string", "description": "One short sentence."},
        },
    },
}

PROMPT = """\
Here is ONE edit that a previous reader claims is written in red pen on this page.

  anchor (printed text it applies to): {anchor}
  proposed result                    : {replacement}
  reported evidence                  : {evidence}

Your job is to REFUTE it if you can. Look at the page. Is that mark really \
there, and does it really say that?

Answer `no` if you cannot find the mark, or cannot make it out well enough to \
confirm the wording. Answer `partly` if a mark is genuinely there but the \
wording is wrong, and supply what the ink actually supports. Answer `yes` only \
if the red ink plainly says this.

Being wrong in a legal filing is worse than being incomplete. When in doubt, \
do not confirm.
"""


def _verify_one(page_png: bytes, base_text: str, e: Edit) -> tuple[Edit, str, str]:
    blocks = reference_blocks()
    # The verifier MUST see the printed text it is judging against. Without it
    # the model can only guess at what a replacement should say, and its
    # "corrections" made the document measurably worse (0.8532 -> 0.7105).
    blocks.append(
        {
            "type": "text",
            "text": "BASE DOCUMENT (the unmarked draft, full text):\n\n" + base_text,
            "cache_control": {"type": "ephemeral"},
        }
    )
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
    blocks.append(
        {
            "type": "text",
            "text": PROMPT.format(
                anchor=e.anchor[:300], replacement=e.replacement[:300],
                evidence=(e.evidence or "(none given)")[:200],
            ),
        }
    )
    try:
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=1000,
            tools=[VERDICT_TOOL],
            tool_choice={"type": "tool", "name": "verdict"},
            messages=[{"role": "user", "content": blocks}],
        )
    except anthropic.APIError:
        return e, "error", ""
    for b in resp.content:
        if b.type == "tool_use":
            v = b.input
            return e, v.get("supported", "no"), v.get("corrected", "") or ""
    return e, "no", ""


def verify(edits: list[Edit], pages: dict[int, bytes], base_text: str,
           max_workers: int = 8) -> tuple[list[Edit], dict]:
    """Return (surviving edits, counts). Unsupported edits are dropped."""
    jobs = [e for e in edits if e.op not in ("query", "split_para", "insert_para")]
    passthrough = [e for e in edits if e not in jobs]

    results: list[tuple[Edit, str, str]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_verify_one, pages[e.page], base_text, e)
            for e in jobs
            if e.page in pages
        ]
        for f in futures:
            results.append(f.result())

    counts = {"yes": 0, "partly": 0, "no": 0, "error": 0}
    kept = []
    for e, verdict, corrected in results:
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict == "no":
            continue                      # refuted - drop it
        if verdict == "partly":
            # Flag rather than rewrite. A correction that disagrees with the
            # readers is not automatically better than what they agreed on;
            # colouring it red puts it in front of the reviewer instead.
            e.confidence = "red"
        kept.append(e)
    return kept + passthrough, counts


def verdicts_only(edits, pages, base_text, max_workers: int = 8):
    """Verify without mutating - returns [(edit, verdict, corrected)]."""
    jobs = [e for e in edits if e.op not in ("query", "split_para", "insert_para")]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_verify_one, pages[e.page], base_text, e)
                   for e in jobs if e.page in pages]
        return [f.result() for f in futures]
