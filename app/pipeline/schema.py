"""Edit schema.

The operation vocabulary comes directly from Dave's cheat sheet (page 3,
"Demonstrate each markup type") plus the shorthand he defined in section 7.
It is deliberately wider than insert/delete/replace: four of his eight markup
types are not plain text edits.
"""

# Confidence is derived from reader agreement, never self-reported.
#   green  = both independent readers produced the same text
#   yellow = readers disagreed, tiebreaker resolved it
#   red    = tiebreaker also uncertain, or an unresolved cross-reference
CONFIDENCE_HIGHLIGHT = {"green": "green", "yellow": "yellow", "red": "red"}

OPS = (
    "replace",       # strike + write-in
    "insert",        # caret + margin word, or line-to-margin
    "delete",        # strikethrough
    "transpose",     # swap two adjacent words
    "split_para",    # his "TP" mark -> new paragraph
    "footnote",      # his "FN"/"EN" mark -> real Word footnote
    "query",         # his "[?]" -> a question back to the user, NOT an edit
    "stet",          # cancels a previously marked revision
)

EDIT_SCHEMA = {
    "type": "object",
    "required": ["edits"],
    "properties": {
        "defined_terms": {
            "type": "array",
            "description": (
                "Any defined term Dave introduces on this page, e.g. "
                '(the "Condo Building 1"). These propagate to later pages.'
            ),
            "items": {
                "type": "object",
                "required": ["term", "means"],
                "properties": {
                    "term": {"type": "string"},
                    "means": {"type": "string"},
                },
            },
        },
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["op", "anchor", "replacement", "evidence"],
                "properties": {
                    "op": {"type": "string", "enum": list(OPS)},
                    "anchor": {
                        "type": "string",
                        "description": (
                            "VERBATIM text copied from the supplied base document "
                            "that this edit applies to. It must appear EXACTLY ONCE "
                            "in that text. Prefer a whole clause. If the text you "
                            "need is short or repeated - a table cell like "
                            "'$375,000', or a bare list number - keep the anchor "
                            "short and put nearby unique text in `scope` instead of "
                            "padding the anchor."
                        ),
                        "minLength": 2,
                    },
                    "scope": {
                        "type": "string",
                        "description": (
                            "Only needed when `anchor` is not unique on its own. "
                            "Verbatim nearby text that IS unique - typically the "
                            "row label of the table row the cell sits in, or the "
                            "opening words of the paragraph. Used to disambiguate."
                        ),
                    },
                    "replacement": {
                        "type": "string",
                        "description": (
                            "The full anchor span rewritten as Dave wants it. For "
                            "'delete' this is the anchor with the struck words "
                            "removed. For 'query' this is the question text."
                        ),
                    },
                    "evidence": {
                        "type": "string",
                        "description": (
                            "What was physically on the page: which mark, where. "
                            "e.g. \"caret before 'professional'; margin word 'proposed'\". "
                            "Used by the tiebreaker, never shown to the user."
                        ),
                    },
                    "xref": {
                        "type": "string",
                        "description": (
                            "If the insertion content lives elsewhere as a circled "
                            "letter (his (A)/(B) convention), the letter. The payload "
                            "is resolved document-wide in a later pass."
                        ),
                    },
                },
            },
        },
    },
}


def resolve_anchor(anchor: str, base_text: str, scope: str = "") -> int | None:
    """Character offset of `anchor` in `base_text`, or None if unplaceable.

    The real requirement is UNIQUENESS, not length. An earlier version of this
    rejected anchors under 20 characters, on the correct observation that short
    anchors are usually ambiguous - but that made table cells inexpressible.
    Six of the twelve edits missed on the first full run were cells like
    "$375,000", including dollar figures in the DIP budget table. Scoping a
    short anchor to unique nearby text places it exactly; excluding it loses
    the edit silently, which is far worse.
    """
    a = (anchor or "").strip()
    if not a:
        return None
    n = base_text.count(a)
    if n == 1:
        return base_text.index(a)
    if n == 0:
        return None
    # ambiguous: narrow the search window using `scope`
    s = (scope or "").strip()
    if s and base_text.count(s) == 1:
        at = base_text.index(s)
        window = base_text[at : at + len(s) + 600]
        if a in window:
            return at + window.index(a)
    return None


def anchor_is_safe(anchor: str, base_text: str = "", scope: str = "") -> bool:
    if not base_text:                      # no corpus to check against
        return bool((anchor or "").strip())
    return resolve_anchor(anchor, base_text, scope) is not None
