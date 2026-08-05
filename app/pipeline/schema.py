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
                            "VERBATIM text copied from the supplied base-document "
                            "page that this edit applies to. Must be at least 20 "
                            "characters and must appear exactly once on the page. "
                            "Never quote a bare list number such as '6.'."
                        ),
                        "minLength": 20,
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


def anchor_is_safe(anchor: str) -> bool:
    """Reject anchors that cannot be placed reliably.

    Measured on the DIP motion: every ambiguous anchor in the 166-edit
    ground truth was a short one, and 45 of them were bare list numbers
    from a renumbering cascade.
    """
    a = anchor.strip()
    if len(a) < 20:
        return False
    # bare list number, e.g. "6." / "12."
    if a.rstrip(".").isdigit():
        return False
    return True
