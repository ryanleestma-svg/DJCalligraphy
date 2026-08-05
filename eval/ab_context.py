"""A/B/C/D test: does document-level context improve accuracy?

The hand-run process that produced the reference output read the whole document
for understanding BEFORE interpreting any edit, and worked through the edits in
one continuous conversation. The per-page pipeline threw both of those away:
each page is read in isolation, with the base text supplied as reference data
rather than as something the reader has formed a view of.

Four arms, identical pages, identical everything else:

    A  baseline          - page + base text (what ships today)
    B  + prior edits     - plus every edit already decided on earlier pages
    C  + document brief  - plus a one-time comprehension pass over the document
    D  both

Scored against the same ground truth. Because all arms see the same pages, the
comparison between them is valid even though recall is partial.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import ingest                       # noqa: E402
from app.pipeline.brief import build_brief            # noqa: E402
from app.pipeline.interpret import read_page          # noqa: E402
from app.pipeline.reconcile import minimal_span       # noqa: E402
from eval.make_fixtures import is_renumber            # noqa: E402
from eval.rescore import (                            # noqa: E402
    effective, locate_produced, locate_truth, overlaps, sim,
)


def prior_edits_block(edits: list[dict], limit: int = 60) -> str:
    if not edits:
        return ""
    lines = []
    for e in edits[-limit:]:
        rem, add = effective(e.get("anchor", ""), e.get("replacement", ""))
        if not (rem or add):
            continue
        lines.append(f'  p{e.get("page","?")}: "{rem[:70]}" -> "{add[:70]}"')
    if not lines:
        return ""
    return (
        "EDITS ALREADY DECIDED ON EARLIER PAGES (his running intent - use them to "
        "read this page consistently, e.g. terms he has renamed):\n" + "\n".join(lines)
    )


def score(produced: list[dict], truth: list[dict], paras: list[str]) -> dict:
    ploc = [(locate_produced(e, paras), e) for e in produced]
    ploc = [x for x in ploc if x[0]]
    tloc = [(locate_truth(t, paras), t) for t in truth]
    tloc = [x for x in tloc if x[0]]

    found = exact = close = wrong = 0
    matched = set()
    for T, t in tloc:
        cands = [(i, L, e) for i, (L, e) in enumerate(ploc) if overlaps(T, L)]
        if not cands:
            continue
        found += 1
        for i, _, _ in cands:
            matched.add(i)
        _tr, ta = effective(t["old"], t["new"]) if t["old"] else ("", t["new"])
        best = max(sim(effective(e.get("anchor", ""), e.get("replacement", ""))[1], ta)
                   for _i, _L, e in cands)
        comb = " ".join(effective(e.get("anchor", ""), e.get("replacement", ""))[1]
                        for _i, _L, e in cands)
        s = max(best, sim(comb, ta))
        if s >= 0.90:
            exact += 1
        elif s >= 0.70:
            close += 1
        else:
            wrong += 1
    return {
        "produced": len(produced),
        "found": found,
        "exact": exact,
        "close": close,
        "wrong": wrong,
        "spurious": len(ploc) - len(matched),
        "frag": round(len(matched) / max(1, found), 2),
        "green": sum(1 for e in produced if e["confidence"] == "green"),
    }


def run_arm(name: str, pages, base_text, paras, truth, use_prior, brief) -> dict:
    produced: list[dict] = []
    glossary: list[dict] = []
    t0 = time.time()
    for p in pages:
        ctx_parts = []
        if brief:
            ctx_parts.append(brief)
        if use_prior:
            blk = prior_edits_block(produced)
            if blk:
                ctx_parts.append(blk)
        gl = "\n".join(f'{g["term"]} = {g["means"]}' for g in glossary)
        edits, terms = read_page(p.images(), base_text, gl, p.index,
                                 "\n\n".join(ctx_parts), p.note())
        produced.extend(e.__dict__ for e in edits)
        for t in terms:
            if t not in glossary:
                glossary.append(t)
        print(f"    [{name}] page {p.index:>2}: {len(edits):>2} edits", flush=True)
    res = score([e for e in produced if e["op"] != "query"], truth, paras)
    res["seconds"] = round(time.time() - t0)
    res["_produced"] = produced
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("--pages", default="2,4,5,7,8,10,12")
    ap.add_argument("--base", default="fixtures/base.docx")
    ap.add_argument("--out", default="/tmp/ab")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    want = {int(x) for x in args.pages.split(",")}
    pages = [p for p in ingest.render_scan(args.scan) if p.index in want]
    paras = ingest.docx_paragraphs(args.base)
    base_text = "\n".join(paras)
    truth = [t for t in json.loads(Path("fixtures/truth.json").read_text())
             if not is_renumber(t)]

    print(f"pages: {[p.index for p in pages]}")
    print("building document brief...", flush=True)
    brief = build_brief(base_text)
    (out / "brief.md").write_text(brief)
    print(f"  brief: {len(brief)} chars\n")

    arms = {
        "A baseline":        (False, ""),
        "B +prior edits":    (True, ""),
        "C +brief":          (False, brief),
        "D +both":           (True, brief),
    }
    results = {}
    for name, (use_prior, br) in arms.items():
        print(f"--- {name}")
        results[name] = run_arm(name, pages, base_text, paras, truth, use_prior, br)

    print()
    hdr = f'{"arm":<16}{"prod":>6}{"found":>7}{"exact":>7}{"close":>7}{"wrong":>7}{"spur":>6}{"frag":>6}{"green":>7}{"secs":>6}'
    print(hdr)
    print("-" * len(hdr))
    for name, r in results.items():
        print(f'{name:<16}{r["produced"]:>6}{r["found"]:>7}{r["exact"]:>7}{r["close"]:>7}'
              f'{r["wrong"]:>7}{r["spurious"]:>6}{r["frag"]:>6}{r["green"]:>7}{r["seconds"]:>6}')

    for name, r in results.items():
        (out / f'{name.split()[0]}.json').write_text(json.dumps(r.pop("_produced"), indent=1))
    (out / "summary.json").write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
