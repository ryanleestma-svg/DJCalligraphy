"""Build evaluation fixtures from the known-good tracked output.

Rejecting every revision in DIP-Motion-v7_JENNIS_REDPEN_TRACKED.docx recovers
the exact base document Dave marked up, as a real .docx (not just text). That
gives a round-trip test for the tracked-changes writer that needs no API key:

    base.docx + ground-truth edits  ->  should reproduce the known-good output
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import wordxml  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def reject_all(tracked: Path, out: Path) -> None:
    """Reject every tracked change -> the original base document."""
    shutil.copyfile(tracked, out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}
    root = etree.fromstring(blobs["word/document.xml"])

    # A paragraph that exists only because it was inserted must not survive in
    # the base at all - leaving an empty w:p behind would shift every later
    # paragraph index by one.
    for p in list(root.iter(W + "p")):
        runs = p.findall(W + "r")
        insl = p.findall(W + "ins")
        dell = p.findall(W + "del")
        txt_r = "".join(t.text or "" for r in runs for t in r.iter(W + "t"))
        txt_i = "".join(t.text or "" for e in insl for t in e.iter(W + "t"))
        if txt_i and not txt_r.strip() and not dell:
            p.getparent().remove(p)

    # drop remaining insertions
    for ins in root.findall(".//" + W + "ins"):
        ins.getparent().remove(ins)
    # unwrap deletions, restoring delText -> t
    for dele in root.findall(".//" + W + "del"):
        parent, idx = dele.getparent(), list(dele.getparent()).index(dele)
        for r in list(dele):
            for dt in r.findall(W + "delText"):
                t = etree.Element(W + "t")
                t.text = dt.text
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                r.replace(dt, t)
            # strip the confidence highlight so the base is clean
            rpr = r.find(W + "rPr")
            if rpr is not None:
                for hl in rpr.findall(W + "highlight"):
                    rpr.remove(hl)
            parent.insert(idx, r)
            idx += 1
        parent.remove(dele)

    blobs["word/document.xml"] = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, blobs[n])


def _unique_left_context(para_text: str, pos: int, minimum: int = 30) -> str:
    """Text ending exactly at `pos`, grown leftwards until it is unique.

    A fixed-width context window is not safe: an insertion that follows a
    deletion, or a second insertion in the same paragraph, can otherwise match
    an earlier occurrence and land the edit in the wrong place.
    """
    n = minimum
    while n <= pos:
        ctx = para_text[pos - n : pos]
        if para_text.count(ctx) == 1:
            return ctx
        n += 10
    return para_text[:pos]


def extract_truth(tracked: Path, out_json: Path) -> list[dict]:
    """Ground-truth edit list, with every edit resolved to a pristine offset.

    Walking each paragraph's children in order and tracking the offset in the
    *base* text (w:r and w:del contribute, w:ins does not) is the only reliable
    way to know where an insertion actually belongs.
    """
    with zipfile.ZipFile(tracked) as z:
        root = etree.fromstring(z.read("word/document.xml"))

    raw = []
    base_pi = -1                      # paragraph index in the BASE document
    for p in root.iter(W + "p"):
        runs = p.findall(W + "r")
        insl = p.findall(W + "ins")
        dell = p.findall(W + "del")
        txt_r = "".join(t.text or "" for r in runs for t in r.iter(W + "t"))
        txt_i = "".join(t.text or "" for e in insl for t in e.iter(W + "t"))
        if txt_i and not txt_r.strip() and not dell:
            # whole paragraph is new -> Dave's "TP" mark. It does not exist in
            # the base, so it must not consume a base paragraph index.
            raw.append(dict(para=base_pi + 1, kind="newpara", text=txt_i,
                            at=0, conf=_hl(insl[0]), _ptext=""))
            continue
        base_pi += 1
        pi = base_pi
        pos, base_buf = 0, []
        pending = None   # accumulate consecutive nodes of the same kind
        for kind, node in wordxml.text_nodes(p):
            s = node.text or ""
            if not s:
                continue
            if kind == "base":
                base_buf.append(s)
                pos += len(s)
                pending = None
            elif kind == "del":
                if pending and pending["kind"] == "del":
                    pending["text"] += s
                else:
                    pending = dict(para=pi, kind="del", text=s, at=pos,
                                   conf=_hl_node(node))
                    raw.append(pending)
                base_buf.append(s)
                pos += len(s)
            else:  # ins
                if pending and pending["kind"] == "ins":
                    pending["text"] += s
                else:
                    pending = dict(para=pi, kind="ins", text=s, at=pos,
                                   conf=_hl_node(node))
                    raw.append(pending)
        for e in raw:
            if e["para"] == pi:
                e["_ptext"] = "".join(base_buf)

    merged, i = [], 0
    while i < len(raw):
        e = raw[i]
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        if (e["kind"] == "del" and nxt and nxt["kind"] == "ins"
                and nxt["para"] == e["para"] and nxt["at"] == e["at"] + len(e["text"])):
            merged.append(dict(para=e["para"], op="replace", old=e["text"],
                               new=nxt["text"], at=e["at"],
                               conf=e["conf"] or nxt["conf"], ctx=""))
            i += 2
        elif e["kind"] == "newpara":
            merged.append(dict(para=e["para"], op="insert_para", old="", new=e["text"],
                               at=0, conf=e["conf"], ctx=""))
            i += 1
        elif e["kind"] == "del":
            merged.append(dict(para=e["para"], op="delete", old=e["text"],
                               new="", at=e["at"], conf=e["conf"], ctx=""))
            i += 1
        else:  # bare insertion - anchor on unique left context
            ctx = _unique_left_context(e["_ptext"], e["at"])
            merged.append(dict(para=e["para"], op="insert", old="", new=e["text"],
                               at=e["at"], conf=e["conf"], ctx=ctx))
            i += 1

    out_json.write_text(json.dumps(merged, indent=1), encoding="utf-8")
    return merged


def _hl_node(node):
    """Highlight colour on the run that owns this text node."""
    for a in node.iterancestors():
        if a.tag == W + "r":
            rpr = a.find(W + "rPr")
            if rpr is not None:
                h = rpr.find(W + "highlight")
                if h is not None:
                    return h.get(W + "val")
            return None
    return None


def _hl(el):
    h = el.find(".//" + W + "highlight")
    return h.get(W + "val") if h is not None else None


NUM = re.compile(r"^\s*(\d+)\.\s*$")


def is_renumber(e: dict) -> bool:
    a, b = NUM.match(e.get("old", "")), NUM.match(e.get("new", ""))
    return bool(a and b and int(b.group(1)) == int(a.group(1)) + 1)


if __name__ == "__main__":
    tracked = Path(sys.argv[1])
    outdir = Path(sys.argv[2] if len(sys.argv) > 2 else "fixtures")
    outdir.mkdir(parents=True, exist_ok=True)
    reject_all(tracked, outdir / "base.docx")
    truth = extract_truth(tracked, outdir / "truth.json")
    sub = [t for t in truth if not is_renumber(t)]
    print(f"base.docx written")
    print(f"truth.json: {len(truth)} logical edits "
          f"({len(truth) - len(sub)} renumber cascade, {len(sub)} substantive)")
