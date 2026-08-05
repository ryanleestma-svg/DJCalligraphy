"""Write real Word tracked changes into the base .docx.

The XML shape is copied from the known-good output
(DIP-Motion-v7_JENNIS_REDPEN_TRACKED.docx), which used:

  * ONE author string for every revision - confidence lives in w:highlight,
    not in the author name. This is what makes "select all -> highlighter ->
    No Color" wipe every confidence band in a single gesture.
  * highlight applied to the deletion as well as the insertion.
"""

from __future__ import annotations

import copy
import shutil
import zipfile
from pathlib import Path

from lxml import etree

from . import wordxml
from .models import Edit
from .reconcile import minimal_span

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
Wns = "{%s}" % W
NSMAP = {"w": W}

AUTHOR = "David S. Jennis (red pen, decoded)"
DATE = "2026-01-01T00:00:00Z"


def _q(tag: str) -> str:
    return Wns + tag


def _para_text(p) -> str:
    """This paragraph's own text - never a nested textbox paragraph's."""
    return wordxml.para_text(p)


def _runs_with_text(p):
    """Runs that carry a w:t, with their (start, end) char offsets."""
    return wordxml.runs_with_offsets(p)


def _split_run(p, run, t_el, local_offset):
    """Split `run` at local_offset, returning (left_run, right_run)."""
    text = t_el.text or ""
    left, right = text[:local_offset], text[local_offset:]
    t_el.text = left
    t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    new_run = copy.deepcopy(run)
    nt = new_run.find(_q("t"))
    nt.text = right
    nt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    run.addnext(new_run)
    return run, new_run


def _isolate(p, start: int, end: int):
    """Split runs so that [start, end) is covered by whole runs. Returns them."""
    for boundary in (end, start):  # end first so offsets stay valid
        for r, t, s, e in _runs_with_text(p):
            if s < boundary < e:
                _split_run(p, r, t, boundary - s)
                break
    return [r for r, t, s, e in _runs_with_text(p) if s >= start and e <= end and e > s]


def _highlight(run, colour: str):
    rpr = run.find(_q("rPr"))
    if rpr is None:
        rpr = etree.SubElement(run, _q("rPr"))
        run.remove(rpr)
        run.insert(0, rpr)
    for old in rpr.findall(_q("highlight")):
        rpr.remove(old)
    hl = etree.SubElement(rpr, _q("highlight"))
    hl.set(_q("val"), colour)


class _Ids:
    def __init__(self, start=9000):
        self.n = start

    def next(self) -> str:
        self.n += 1
        return str(self.n)


def _wrap_delete(p, runs, colour, ids):
    if not runs:
        return None
    d = etree.Element(_q("del"))
    d.set(_q("id"), ids.next())
    d.set(_q("author"), AUTHOR)
    d.set(_q("date"), DATE)
    runs[0].addprevious(d)
    for r in runs:
        r.getparent().remove(r)
        t = r.find(_q("t"))
        if t is not None:
            dt = etree.Element(_q("delText"))
            dt.text = t.text
            dt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            r.replace(t, dt)
        _highlight(r, colour)
        d.append(r)
    return d


def _make_insert(text, colour, ids, template_run=None):
    ins = etree.Element(_q("ins"))
    ins.set(_q("id"), ids.next())
    ins.set(_q("author"), AUTHOR)
    ins.set(_q("date"), DATE)
    r = copy.deepcopy(template_run) if template_run is not None else etree.Element(_q("r"))
    for child in list(r):
        if child.tag != _q("rPr"):
            r.remove(child)
    t = etree.SubElement(r, _q("t"))
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    _highlight(r, colour)
    ins.append(r)
    return ins


def apply_edits(base_docx: str | Path, edits: list[Edit], out_docx: str | Path) -> dict:
    """Apply edits as tracked changes.

    All character offsets are resolved against the *pristine* paragraph text
    first, then edits are applied right-to-left within each paragraph. Applying
    left-to-right would corrupt later offsets in the same paragraph, because a
    deleted run leaves the direct-child list (w:t becomes w:delText inside w:del)
    and would no longer be visible to a subsequent anchor search.
    """
    base_docx, out_docx = Path(base_docx), Path(out_docx)
    shutil.copyfile(base_docx, out_docx)

    with zipfile.ZipFile(out_docx) as z:
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}

    root = etree.fromstring(blobs["word/document.xml"])
    paras = root.findall(".//" + _q("p"))
    for p in paras:
        wordxml.explode_runs(p)
    pristine = [_para_text(p) for p in paras]
    ids = _Ids()
    failed: list[Edit] = []

    # ---- whole new paragraphs (his "TP" mark) --------------------------------
    new_paras = [e for e in edits if e.op in ("insert_para", "split_para")]
    for e in new_paras:
        idx = e.para_hint if e.para_hint is not None else len(paras)
        idx = max(0, min(idx, len(paras) - 1))
        np = etree.Element(_q("p"))
        ref = paras[idx]
        ppr = ref.find(_q("pPr"))
        if ppr is not None:
            np.append(copy.deepcopy(ppr))
        template = next(iter(ref.findall(_q("r"))), None)
        np.append(_make_insert(e.replacement or e.anchor, e.confidence, ids, template))
        ref.addprevious(np)

    # ---- resolve every edit to (paragraph, start, end) up front --------------
    planned: dict[int, list[tuple[int, int, str, str, Edit]]] = {}
    for e in edits:
        if e.op in ("insert_para", "split_para"):
            continue
        # Explicitly targeted edit (e.g. a derived renumber): no text search.
        # Searching for a bare list number is unsafe - "6." matches inside "26.".
        if e.para_hint is not None and e.offset_hint is not None:
            pi = e.para_hint
            if not (0 <= pi < len(pristine)):
                failed.append(e)
                continue
            start = e.offset_hint
            end = start + len(e.anchor)
            if pristine[pi][start:end] != e.anchor:
                failed.append(e)
                continue
            _pre, old_core, new_core, _sfx = minimal_span(e.anchor, e.replacement)
            start += len(_pre)
            end = start + len(old_core)
            planned.setdefault(pi, []).append((start, end, old_core, new_core, e))
            continue
        if not e.anchor:
            failed.append(e)
            continue
        pi = None
        if e.para_hint is not None and 0 <= e.para_hint < len(pristine) \
                and e.anchor in pristine[e.para_hint]:
            pi = e.para_hint
        else:
            pi = next((i for i, t in enumerate(pristine) if e.anchor in t), None)
        if pi is None:
            failed.append(e)
            continue
        at = pristine[pi].index(e.anchor)
        if e.op == "query":
            start = end = at + len(e.anchor)
            old_core, new_core = "", " " + e.replacement + " "
        else:
            prefix, old_core, new_core, _sfx = minimal_span(e.anchor, e.replacement)
            start = at + len(prefix)
            end = start + len(old_core)
        planned.setdefault(pi, []).append((start, end, old_core, new_core, e))

    # ---- apply, rightmost span first ----------------------------------------
    applied = len(new_paras)
    for pi, spans in planned.items():
        p = paras[pi]
        for start, end, old_core, new_core, e in sorted(spans, key=lambda s: -s[0]):
            colour = "red" if e.op == "query" else e.confidence
            target_runs = _isolate(p, start, end) if old_core else []
            template = target_runs[0] if target_runs else None
            del_el = _wrap_delete(p, target_runs, colour, ids) if target_runs else None

            if new_core:
                ins = _make_insert(new_core, colour, ids, template)
                if del_el is not None:
                    del_el.addnext(ins)
                else:
                    _isolate(p, start, start)
                    runs = _runs_with_text(p)
                    prev = None
                    for r, t, s, en in runs:
                        if en <= start:
                            prev = r
                    (prev.addnext(ins) if prev is not None else p.insert(0, ins))
            applied += 1

    blobs["word/document.xml"] = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )

    with zipfile.ZipFile(out_docx, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, blobs[n])

    return {"applied": applied, "failed": len(failed), "failed_edits": failed}
