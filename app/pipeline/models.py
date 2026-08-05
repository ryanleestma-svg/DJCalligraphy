"""Core data types. Deliberately free of any API-client dependency so the
document layer (reconcile, apply) and the evaluation harness can be used and
tested without network access or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Edit:
    op: str
    anchor: str
    replacement: str
    evidence: str = ""
    xref: str | None = None
    scope: str = ""            # unique nearby text, for short/table anchors
    confidence: str = "red"      # green | yellow | red - derived from agreement
    page: int = 0
    para_hint: int | None = None   # paragraph index when known
    offset_hint: int | None = None # exact char offset when known (renumbering)
    readers: list[str] = field(default_factory=list)


@dataclass
class DefinedTerm:
    term: str
    means: str
    page: int = 0
