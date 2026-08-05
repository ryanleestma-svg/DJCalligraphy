"""Re-score a completed run in BASE-DOCUMENT COORDINATES.

The naive scorer compares the text each edit quoted. That conflates two very
different things: whether the pipeline found and understood a mark, and how much
surrounding context it chose to quote. Two identical, correct readings of the
same mark score badly if one quotes a clause and the other quotes a sentence.

This scorer resolves every edit - produced and ground truth - to a character
range in the base document, matches by RANGE OVERLAP, and then compares only the
effective change (what text is removed, what text is added). Span choice becomes
irrelevant; only the actual edit is measured.

Runs offline against eval output; makes no API calls.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.ingest import docx_paragraphs      # noqa: E402
from app.pipeline.reconcile import minimal_span      # noqa: E402
from eval.make_fixtures import is_renumber           # noqa: E402


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def locate_produced(e: dict, paras: list[str]):
    """Resolve a produced edit to (para, start, end, removed, added)."""
    anchor = e.get("anchor") or ""
    if not anchor:
        return None
    pi = next((i for i, t in enumerate(paras) if anchor in t), None)
    if pi is None:
        return None
    at = paras[pi].index(anchor)
    pre, old_core, new_core, _ = minimal_span(anchor, e.get("replacement") or "")
    s = at + len(pre)
    return (pi, s, s + len(old_core), old_core, new_core)


def locate_truth(t: dict, paras: list[str]):
    pi = t["para"]
    if not (0 <= pi < len(paras)):
        return None
    if t["op"] == "insert":
        s = t.get("at", 0)
        return (pi, s, s, "", t["new"])
    pre, old_core, new_core, _ = minimal_span(t["old"], t["new"])
    s = t.get("at", 0) + len(pre)
    return (pi, s, s + len(old_core), old_core, new_core)


def overlaps(a, b, slack: int = 40) -> bool:
    """Same paragraph and the spans touch (or nearly touch)."""
    if a[0] != b[0]:
        return False
    return a[1] - slack <= b[2] and b[1] - slack <= a[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--produced", default="/tmp/eval/produced.json")
    ap.add_argument("--truth", default="fixtures/truth.json")
    ap.add_argument("--base", default="fixtures/base.docx")
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()

    paras = docx_paragraphs(args.base)
    produced = json.loads(Path(args.produced).read_text())
    truth = [t for t in json.loads(Path(args.truth).read_text()) if not is_renumber(t)]

    ploc, unplaceable = [], 0
    for e in produced:
        L = locate_produced(e, paras)
        if L is None:
            unplaceable += 1
        else:
            ploc.append((L, e))
    tloc = [(locate_truth(t, paras), t) for t in truth]
    tloc = [x for x in tloc if x[0]]

    # a truth edit is FOUND if any produced edit overlaps it
    found, exact, close, wrong = [], 0, 0, 0
    matched_p = set()
    for T, t in tloc:
        cands = [(i, L, e) for i, (L, e) in enumerate(ploc) if overlaps(T, L)]
        if not cands:
            continue
        found.append((T, t, cands))
        for i, _, _ in cands:
            matched_p.add(i)
        # best candidate by added-text similarity
        best = max(cands, key=lambda c: sim(c[2].get("replacement", ""), t["new"] or t["old"]))
        combined_added = " ".join(c[2].get("replacement", "") for c in cands)
        s_best = sim(best[2].get("replacement", ""), t["new"] or t["old"])
        s_comb = sim(combined_added, t["new"] or t["old"])
        s = max(s_best, s_comb)
        if s >= 0.90:
            exact += 1
        elif s >= 0.70:
            close += 1
        else:
            wrong += 1

    spurious = [e for i, (L, e) in enumerate(ploc) if i not in matched_p]

    print("=" * 66)
    print("RE-SCORED IN BASE-DOCUMENT COORDINATES")
    print("=" * 66)
    print(f"ground-truth edits          : {len(tloc)}")
    print(f"produced edits              : {len(produced)}  "
          f"({unplaceable} could not be placed in the base text)")
    print()
    print(f"truth edits FOUND           : {len(found)}/{len(tloc)}  "
          f"= {100*len(found)/len(tloc):.0f}% recall")
    print(f"   content exact  (>=0.90)  : {exact}")
    print(f"   content close  (>=0.70)  : {close}")
    print(f"   content wrong  ( <0.70)  : {wrong}")
    print(f"truth edits MISSED          : {len(tloc)-len(found)}")
    print()
    print(f"produced overlapping truth  : {len(matched_p)}")
    print(f"produced NOT near any truth : {len(spurious)}")
    frag = len(matched_p) / max(1, len(found))
    print(f"fragmentation               : {frag:.2f} produced edits per truth edit")
    print()
    print(f"confidence (produced)       : {dict(Counter(e['confidence'] for e in produced))}")
    print(f"ops (produced)              : {dict(Counter(e['op'] for e in produced))}")

    if args.show:
        print()
        print("-" * 66)
        print("SAMPLE: truth edits not found")
        for T, t in tloc:
            if not any(overlaps(T, L) for L, e in ploc):
                print(f"  [{t['conf']}] {t['old'][:60]!r} -> {t['new'][:60]!r}")
                args.show -= 1
                if args.show <= 0:
                    break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
