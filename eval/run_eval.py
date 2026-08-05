"""Live evaluation: run the real pipeline over a scan and score it.

    python eval/run_eval.py <scan.pdf> [--pages 1,2,3] [--out /tmp/eval]

Scoring is against fixtures/truth.json, matching produced edits to ground-truth
edits by anchor similarity. Renumbering edits are excluded from scoring: they
are derived, not read, so they measure the writer rather than the reading.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import ingest                       # noqa: E402
from app.pipeline.interpret import read_page          # noqa: E402
from eval.make_fixtures import is_renumber            # noqa: E402


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def score(produced, truth, anchor_thresh=0.55, content_thresh=0.85):
    """Match produced -> truth by anchor, then compare the resulting text."""
    used, rows = set(), []
    for p in produced:
        best, bs = None, 0.0
        for i, t in enumerate(truth):
            if i in used:
                continue
            s = sim(p["anchor"], t["old"] or t.get("ctx", ""))
            if s > bs:
                bs, best = s, i
        if bs >= anchor_thresh:
            used.add(best)
            t = truth[best]
            # compare the *effect*: what the text becomes
            rows.append((p, t, sim(p["replacement"], t["new"] or t["old"])))
        else:
            rows.append((p, None, 0.0))

    matched = [r for r in rows if r[1]]
    good = [r for r in matched if r[2] >= content_thresh]
    return {
        "truth_total": len(truth),
        "produced_total": len(produced),
        "located": len(matched),
        "recall": len(matched) / max(1, len(truth)),
        "content_ok": len(good),
        "content_rate": len(good) / max(1, len(matched)),
        "false_pos": len(rows) - len(matched),
        "missed": [truth[i] for i in range(len(truth)) if i not in used],
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("--base", default="fixtures/base.docx")
    ap.add_argument("--truth", default="fixtures/truth.json")
    ap.add_argument("--pages", default="", help="comma-separated 1-based page numbers")
    ap.add_argument("--out", default="/tmp/eval")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pages = ingest.render_scan(args.scan)
    if args.pages:
        want = {int(x) for x in args.pages.split(",")}
        pages = [p for p in pages if p.index in want]
    base_paras = ingest.docx_paragraphs(args.base)
    base_text = "\n".join(base_paras)

    produced, glossary = [], []
    t0 = time.time()
    for p in pages:
        gl = "\n".join(f'{g["term"]} = {g["means"]}' for g in glossary)
        t1 = time.time()
        edits, terms = read_page(p.png, base_text, gl, p.index)
        produced.extend(e.__dict__ for e in edits)
        for t in terms:
            if t not in glossary:
                glossary.append(t)
        print(f"  page {p.index:>2}/{len(pages)}  {len(edits):>2} edits  "
              f"{time.time()-t1:>5.0f}s  (terms so far: {len(glossary)})", flush=True)

    (out / "produced.json").write_text(json.dumps(produced, indent=1))
    (out / "glossary.json").write_text(json.dumps(glossary, indent=1))

    truth = [t for t in json.loads(Path(args.truth).read_text()) if not is_renumber(t)]
    res = score(produced, truth)

    print()
    print(f"elapsed            : {time.time()-t0:.0f}s over {len(pages)} pages")
    print(f"ground-truth edits : {res['truth_total']}")
    print(f"produced edits     : {res['produced_total']}")
    print(f"located            : {res['located']}  ({100*res['recall']:.0f}% recall)")
    print(f"content correct    : {res['content_ok']}/{res['located']} "
          f"({100*res['content_rate']:.0f}%)")
    print(f"unmatched produced : {res['false_pos']}")
    print(f"missed             : {len(res['missed'])}")
    print(f"confidence         : {dict(Counter(p['confidence'] for p in produced))}")
    print(f"defined terms found: {[g['term'] for g in glossary]}")

    with (out / "score.json").open("w") as fh:
        json.dump({k: v for k, v in res.items() if k != "rows"}, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
