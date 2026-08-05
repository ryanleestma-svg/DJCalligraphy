"""Isolated per-page calls vs ONE continuous conversation over the document.

The direct test of the structural claim: that reading each sheet standalone
destroys the thread of the argument and makes it impossible to hear whether an
edited sentence still flows.
"""
from __future__ import annotations
import argparse, json, statistics as st, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import ingest                       # noqa: E402
from app.pipeline.conversation import read_document   # noqa: E402
from eval.ab_context import run_arm, score            # noqa: E402
from eval.make_fixtures import is_renumber            # noqa: E402


def run_conv(pages, base_text, paras, truth):
    t0 = time.time()
    def prog(i, total, n):
        print(f"    [conv] page {i}/{total}: {n} edits", flush=True)
    edits, _terms = read_document(pages, base_text, prog)
    produced = [e.__dict__ for e in edits]
    r = score([e for e in produced if e["op"] != "query"], truth, paras)
    r["seconds"] = round(time.time() - t0)
    r["_produced"] = produced
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("--pages", default="2,4,5,7,8,10,12")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--base", default="fixtures/base.docx")
    ap.add_argument("--out", default="/tmp/conv")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    want = {int(x) for x in args.pages.split(",")}
    pages = [p for p in ingest.render_scan(args.scan) if p.index in want]
    paras = ingest.docx_paragraphs(args.base)
    base_text = "\n".join(paras)
    truth = [t for t in json.loads(Path("fixtures/truth.json").read_text())
             if not is_renumber(t)]
    print(f"pages: {[p.index for p in pages]}\n")

    res = {"per-page (current)": [], "one conversation": []}
    for rep in range(args.reps):
        r = run_arm(f"per-page#{rep+1}", pages, base_text, paras, truth, False, "")
        p = r.pop("_produced"); res["per-page (current)"].append(r)
        (out / f"perpage_{rep+1}.json").write_text(json.dumps(p, indent=1))
        print(f"  per-page#{rep+1}: found={r['found']} exact={r['exact']} close={r['close']} "
              f"wrong={r['wrong']} spur={r['spurious']} frag={r['frag']} ({r['seconds']}s)", flush=True)

        r = run_conv(pages, base_text, paras, truth)
        p = r.pop("_produced"); res["one conversation"].append(r)
        (out / f"conv_{rep+1}.json").write_text(json.dumps(p, indent=1))
        print(f"  conv#{rep+1}:     found={r['found']} exact={r['exact']} close={r['close']} "
              f"wrong={r['wrong']} spur={r['spurious']} frag={r['frag']} ({r['seconds']}s)", flush=True)

    print()
    names = list(res)
    hdr = f'{"metric":<12}' + "".join(f'{n:>26}' for n in names)
    print(hdr); print("-"*len(hdr))
    for m in ["found","exact","close","wrong","spurious","produced","frag","seconds"]:
        row = f"{m:<12}"
        for n in names:
            v=[x[m] for x in res[n]]
            row += f"{st.mean(v):>14.1f}  [{min(v)}-{max(v)}]".ljust(26)
        print(row)
    print()
    for n in names:
        acc=[x["exact"]+x["close"] for x in res[n]]
        print(f'{n}: exact+close mean {st.mean(acc):.1f}')
    (out/"summary.json").write_text(json.dumps(res, indent=1))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
