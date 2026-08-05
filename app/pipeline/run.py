"""End-to-end orchestration: base .docx + markup scan -> tracked-changes .docx."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import ingest, reconcile
from .apply import apply_edits
from .models import Edit

Progress = Callable[[str, float], None]


def _noop(msg: str, frac: float) -> None:
    pass


def process(
    base_docx: str | Path,
    scan_pdf: str | Path,
    out_docx: str | Path,
    progress: Progress = _noop,
) -> dict:
    from .interpret import read_page  # imported late: needs an API key

    base_docx, scan_pdf, out_docx = Path(base_docx), Path(scan_pdf), Path(out_docx)

    progress("Rendering scan", 0.02)
    pages = ingest.render_scan(scan_pdf)
    if not pages:
        raise ValueError("No content pages found in the markup scan.")

    base_paras = ingest.docx_paragraphs(base_docx)
    base_text = "\n".join(base_paras)

    all_edits: list[Edit] = []
    glossary: list[dict] = []

    for i, page in enumerate(pages, 1):
        progress(f"Reading page {i} of {len(pages)}", 0.05 + 0.75 * i / len(pages))
        gloss_text = "\n".join(f'{g["term"]} = {g["means"]}' for g in glossary)
        edits, terms = read_page(page.png, base_text, gloss_text, i)
        all_edits.extend(edits)
        # Defined terms propagate forward: an edit on page 1 can change how a
        # later page must be read (e.g. "Condos" -> the "Condo Building 1").
        for t in terms:
            if t not in glossary:
                glossary.append(t)

    progress("Reconciling", 0.85)
    all_edits = reconcile.apply_stet(all_edits)
    all_edits = reconcile.resolve_xrefs(all_edits)
    all_edits = reconcile.normalise(all_edits)
    # Must run before anything is written: two edits over the same text rewrite
    # the same run twice and corrupt it. Measured end to end, skipping this left
    # the document further from correct than making no edits at all.
    before = len(all_edits)
    all_edits = reconcile.consolidate(all_edits, base_paras)
    progress(f"Consolidated {before} -> {len(all_edits)} edits", 0.88)

    # Derive the renumbering cascade rather than reading 45 of them off the page.
    for e in [e for e in all_edits if e.op in ("split_para", "insert_para")]:
        # The reader reports an anchor, never a paragraph index, so resolve it
        # here. Guarding on para_hint alone meant this loop never ran and every
        # list number downstream of an inserted paragraph stayed stale.
        if e.para_hint is None and e.anchor:
            e.para_hint = next(
                (i for i, t in enumerate(base_paras) if e.anchor in t), None
            )
        if e.para_hint is None:
            continue
        for idx, old, new in reconcile.renumber_after_split(base_paras, e.para_hint):
            all_edits.append(
                Edit(op="replace", anchor=old, replacement=new,
                     confidence="green", para_hint=idx, offset_hint=0,
                     evidence="derived from paragraph insertion")
            )

    progress("Writing tracked changes", 0.92)
    res = apply_edits(base_docx, all_edits, out_docx)

    counts = {c: sum(1 for e in all_edits if e.confidence == c) for c in ("green", "yellow", "red")}
    queries = [e for e in all_edits if e.op == "query"]
    progress("Done", 1.0)
    return {
        "edits": len(all_edits),
        "applied": res["applied"],
        "unplaced": res["failed"],
        "confidence": counts,
        "queries": len(queries),
        "pages": len(pages),
        "detail": [asdict(e) for e in all_edits],
    }
