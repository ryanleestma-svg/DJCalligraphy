"""Shared WordprocessingML traversal helpers.

The subtlety these exist for: a w:p can contain another w:p, via a textbox
(mc:AlternateContent / w:txbxContent inside a run). Naive `p.iter()` therefore
walks straight into the nested paragraph's text, which silently inflates every
character offset in the outer paragraph. Measured on the DIP motion: 482 runs
belonged to nested paragraphs.

Everything here works in terms of a paragraph's OWN content only.
"""

from __future__ import annotations

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
Wns = "{%s}" % W

T = Wns + "t"
DELTEXT = Wns + "delText"
P = Wns + "p"
R = Wns + "r"
INS = Wns + "ins"
DEL = Wns + "del"


def _owner_para(node):
    for a in node.iterancestors():
        if a.tag == P:
            return a
    return None


def own_runs(p):
    """Runs whose nearest enclosing w:p is `p` (skips textbox paragraphs)."""
    return [r for r in p.iter(R) if _owner_para(r) is p]


def text_nodes(p):
    """Yield (kind, node) for this paragraph's own text, in document order.

    kind is one of: 'base' (normal run), 'ins' (inside w:ins), 'del' (w:delText).
    """
    for node in p.iter(T, DELTEXT):
        if _owner_para(node) is not p:
            continue
        if any(a.tag == INS for a in node.iterancestors()):
            yield "ins", node
        elif node.tag == DELTEXT or any(a.tag == DEL for a in node.iterancestors()):
            yield "del", node
        else:
            yield "base", node


def para_text(p) -> str:
    """Current visible text of the paragraph (its own content only)."""
    return "".join(n.text or "" for kind, n in text_nodes(p) if kind != "del")


def base_text(p) -> str:
    """Pre-markup text: normal runs plus deletions, insertions excluded."""
    return "".join(n.text or "" for kind, n in text_nodes(p) if kind != "ins")


def explode_runs(p):
    """Split every run so it carries at most one text node.

    A single w:r may hold several w:t children (typically separated by a w:tab).
    Any offset arithmetic that reads only the first one silently loses the rest -
    measured on the DIP motion, this dropped 77 characters from the first run of
    a numbered paragraph and pushed every later edit in that paragraph off.
    """
    import copy as _copy

    for r in list(own_runs(p)):
        kids = [c for c in r if c.tag != Wns + "rPr"]
        if len(kids) <= 1:
            continue
        rpr = r.find(Wns + "rPr")
        anchor = r
        for kid in kids:
            nr = _copy.deepcopy(r)
            for c in list(nr):
                nr.remove(c)
            if rpr is not None:
                nr.append(_copy.deepcopy(rpr))
            nr.append(_copy.deepcopy(kid))
            anchor.addnext(nr)
            anchor = nr
        r.getparent().remove(r)


def runs_with_offsets(p):
    """[(run, t_element, start, end)] over the paragraph's own text runs."""
    out, pos = [], 0
    for r in own_runs(p):
        t = r.find(T)
        if t is None:
            continue
        s = t.text or ""
        out.append((r, t, pos, pos + len(s)))
        pos += len(s)
    return out
