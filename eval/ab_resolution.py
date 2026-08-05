"""Does giving the model more pixels per stroke improve accuracy?

Vision downscales anything over 1568 px on the long edge, so a whole letter page
rendered at any dpi reaches the model at ~143 dpi. Rendering higher changes
nothing. Cutting the page into an overlapping grid lets each tile fill the
budget instead: 2x2 lands at ~230 dpi.

This is the only lever tried so far that acts on the legibility of the ink
rather than on the scaffolding around it.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import ingest                      # noqa: E402
from eval.ab_context import run_arm                  # noqa: E402
from eval.make_fixtures import is_renumber           # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("--pages", default="2,4,5,7,8,10,12")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--base", default="fixtures/base.docx")
    ap.add_argument("--out", default="/tmp/res")
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    want = {int(x) for x in args.pages.split(",")}
    paras = ingest.docx_paragraphs(args.base)
    base_text = "\n".join(paras)
    truth = [t for t in json.loads(Path("fixtures/truth.json").read_text())
             if not is_renumber(t)]

    configs = {
        "whole page": dict(cols=1, rows=1),
        "2x2 tiles":  dict(cols=2, rows=2),
    }
    rendered = {}
    for name, cfg in configs.items():
        pgs = [p for p in ingest.render_scan(args.scan, **cfg) if p.index in want]
        rendered[name] = pgs
        print(f"{name}: {len(pgs)} pages, {len(pgs[0].images())} image(s) each", flush=True)

    results = {n: [] for n in configs}
    for rep in range(args.reps):
        for name, pgs in rendered.items():
            r = run_arm(f"{name}#{rep+1}", pgs, base_text, paras, truth, False, "")
            r.pop("_produced", None)
            results[name].append(r)
            print(f"  {name}#{rep+1}: found={r['found']} exact={r['exact']} "
                  f"close={r['close']} wrong={r['wrong']} spur={r['spurious']} "
                  f"frag={r['frag']}", flush=True)

    print()
    names = list(configs)
    hdr = f'{"metric":<10}' + "".join(f'{n:>24}' for n in names)
    print(hdr); print("-" * len(hdr))
    for m in ["found", "exact", "close", "wrong", "spurious", "produced", "frag", "green"]:
        row = f"{m:<10}"
        for n in names:
            v = [r[m] for r in results[n]]
            row += f"{st.mean(v):>12.1f}  [{min(v)}-{max(v)}]".ljust(24)
        print(row)
    print()
    for n in names:
        acc = [r["exact"] + r["close"] for r in results[n]]
        print(f'{n}: exact+close mean {st.mean(acc):.1f}'
              + (f'  sd {st.pstdev(acc):.1f}' if len(acc) > 1 else ""))
    (out / "results.json").write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
