"""Full pipeline: dual conversation + continuations + coherence sweep.

Everything measured to help, run together, scored end to end on the whole scan.
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import ingest, reconcile, sweep as sweep_mod    # noqa: E402
from app.pipeline.apply import apply_edits                        # noqa: E402
from app.pipeline.conversation import read_document_consensus     # noqa: E402
from eval.roundtrip import accept_all                             # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("--base", default="fixtures/base.docx")
    ap.add_argument("--out", default="/tmp/full")
    ap.add_argument("--no-sweep", action="store_true")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    pages = ingest.render_scan(args.scan)
    conts = sum(1 for p in pages if p.backs)
    print(f"pages: {len(pages)} ({conts} with continuation backs)", flush=True)
    paras = ingest.docx_paragraphs(args.base)
    base_text = "\n".join(paras)

    t0 = time.time()
    def prog(i, total, n):
        print(f"  page {i}/{total}: {n} edits", flush=True)
    edits, terms = read_document_consensus(pages, base_text, prog)
    print(f"read: {len(edits)} edits in {time.time()-t0:.0f}s", flush=True)

    edits = reconcile.apply_stet(edits)
    edits = reconcile.resolve_xrefs(edits)
    edits = reconcile.normalise(edits)
    before_n = len(edits)
    edits = reconcile.consolidate(edits, paras)
    print(f"consolidated: {before_n} -> {len(edits)}", flush=True)

    apply_edits(args.base, edits, out / "draft.docx")

    if not args.no_sweep:
        after = accept_all(out / "draft.docx").split("\n")
        t1 = time.time()
        edits, flags = sweep_mod.sweep(paras, after, edits)
        print(f"sweep: {len(flags)} passages flagged in {time.time()-t1:.0f}s", flush=True)
        (out / "flags.json").write_text(json.dumps(flags, indent=1))

    apply_edits(args.base, edits, out / "final.docx")
    (out / "edits.json").write_text(json.dumps([e.__dict__ for e in edits], indent=1))

    import difflib
    good = accept_all("fixtures/known_good.docx"); base = accept_all(args.base)
    mine = accept_all(out / "final.docx")
    sm = lambda a,b: difflib.SequenceMatcher(None,a,b).ratio()
    from collections import Counter
    print()
    print(f"edits            : {len(edits)}  {dict(Counter(e.confidence for e in edits))}")
    print(f"do nothing       : {sm(good,base):.4f}")
    print(f"THIS RUN         : {sm(good,mine):.4f}")
    print(f"prior best       : 0.8533")
    print(f"total seconds    : {time.time()-t0:.0f}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
