"""Repeat one configuration N times to separate signal from run-to-run noise.

Two runs of the identical baseline configuration differed by 2 exact edits, 3
wrong edits and 17 produced edits. Any single-run comparison between arms is
therefore uninterpretable at that magnitude, and the four-arm experiment's
apparent winner sat inside that band. This runs each arm repeatedly and reports
mean and spread so a difference can actually be believed.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import ingest                      # noqa: E402
from app.pipeline.brief import build_brief           # noqa: E402
from eval.ab_context import run_arm                  # noqa: E402
from eval.make_fixtures import is_renumber           # noqa: E402

METRICS = ["found", "exact", "close", "wrong", "spurious", "produced", "frag", "green"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("--pages", default="2,4,5,7,8,10,12")
    ap.add_argument("--arms", default="A,C")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--base", default="fixtures/base.docx")
    ap.add_argument("--out", default="/tmp/rep")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    want = {int(x) for x in args.pages.split(",")}
    pages = [p for p in ingest.render_scan(args.scan) if p.index in want]
    paras = ingest.docx_paragraphs(args.base)
    base_text = "\n".join(paras)
    truth = [t for t in json.loads(Path("fixtures/truth.json").read_text())
             if not is_renumber(t)]

    arms = [a.strip() for a in args.arms.split(",")]
    brief = ""
    if any(a in ("C", "D") for a in arms):
        print("building document brief...", flush=True)
        brief = build_brief(base_text)
        (out / "brief.md").write_text(brief)
        print(f"  {len(brief)} chars\n", flush=True)

    CONF = {"A": (False, ""), "B": (True, ""), "C": (False, None), "D": (True, None)}
    results: dict[str, list[dict]] = {a: [] for a in arms}

    for rep in range(args.reps):
        for a in arms:
            use_prior, br = CONF[a]
            if br is None:
                br = brief
            t0 = time.time()
            r = run_arm(f"{a}#{rep+1}", pages, base_text, paras, truth, use_prior, br)
            r.pop("_produced", None)
            results[a].append(r)
            print(f"  {a}#{rep+1}: found={r['found']} exact={r['exact']} "
                  f"close={r['close']} wrong={r['wrong']} spur={r['spurious']} "
                  f"frag={r['frag']} ({time.time()-t0:.0f}s)", flush=True)

    print()
    hdr = f'{"metric":<10}' + "".join(f'{a:>22}' for a in arms)
    print(hdr)
    print("-" * len(hdr))
    for m in METRICS:
        row = f"{m:<10}"
        for a in arms:
            vals = [r[m] for r in results[a]]
            mean = st.mean(vals)
            spread = f"{min(vals)}-{max(vals)}" if len(vals) > 1 else str(vals[0])
            row += f"{mean:>10.1f}  [{spread:>8}]"
        print(row)

    print()
    for a in arms:
        acc = [r["exact"] + r["close"] for r in results[a]]
        print(f'{a}: exact+close mean {st.mean(acc):.1f}'
              + (f'  sd {st.pstdev(acc):.1f}' if len(acc) > 1 else ""))

    (out / "repeats.json").write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
