"""Round-trip test of the tracked-changes writer. Needs no API key.

    base.docx + all 169 ground-truth edits  ->  must equal known_good.docx
    (compared by accepting every revision in both and diffing the text)

This isolates the document layer from the vision layer: if this passes, any
difference in a real run is an interpretation difference, not a writer bug.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.apply import apply_edits          # noqa: E402
from app.pipeline.models import Edit                # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def accept_all(path) -> str:
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    for d in root.findall(".//" + W + "del"):
        d.getparent().remove(d)
    for ins in root.findall(".//" + W + "ins"):
        par, idx = ins.getparent(), list(ins.getparent()).index(ins)
        for r in list(ins):
            par.insert(idx, r)
            idx += 1
        par.remove(ins)
    return "\n".join(
        "".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")
    )


def truth_to_edits(truth: list[dict]) -> list[Edit]:
    out = []
    for t in truth:
        if t["op"] == "insert_para":
            out.append(Edit(op="insert_para", anchor="", replacement=t["new"],
                            confidence=t["conf"] or "yellow", para_hint=t["para"]))
            continue
        anchor = t["old"] or t.get("ctx", "")
        if not anchor.strip():
            continue
        if t["op"] == "insert":
            replacement = anchor + t["new"]
        else:
            replacement = t["new"]
        out.append(
            Edit(
                op=t["op"],
                anchor=anchor,
                replacement=replacement,
                confidence=t["conf"] or "yellow",
                para_hint=t["para"],
                offset_hint=t.get("at") if t["op"] in ("replace", "delete") else None,
            )
        )
    return out


def main(fixtures="fixtures", out="/tmp/roundtrip.docx") -> int:
    fx = Path(fixtures)
    truth = json.loads((fx / "truth.json").read_text())
    edits = truth_to_edits(truth)
    res = apply_edits(fx / "base.docx", edits, out)

    mine, good = accept_all(out), accept_all(fx / "known_good.docx")
    ok = mine == good
    print(f"edits replayed : {len(edits)}")
    print(f"applied        : {res['applied']}   failed: {res['failed']}")
    print(f"accept-all text: {'IDENTICAL to known-good' if ok else 'DIFFERS'}")
    if not ok:
        import difflib

        sm = difflib.SequenceMatcher(None, good, mine)
        print(f"similarity     : {sm.ratio():.4f}")
        n = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                n += 1
                if n <= 10:
                    print(f"  [{tag}] good={good[i1:i2][:60]!r}")
                    print(f"          mine={mine[j1:j2][:60]!r}")
        print(f"differing regions: {n}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
