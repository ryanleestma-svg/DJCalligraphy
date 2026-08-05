# Red Pen

Drop a base Word document and a scan of David S. Jennis's red-pen markup, press
GO, and get back a `.docx` with real Word tracked changes — colour-coded by
confidence — that you review by right-clicking Accept or Reject.

```
   [ base .docx ]        [ red-pen scan ]
          \                    /
           \                  /
            +----> GO <------+          Dave's handwriting reference is
                    |                   baked in; you never upload it.
                    v
        .docx with tracked changes
        green = high · yellow = medium · red = low
```

When you're done reviewing: **Select All → Highlighter → No Color** clears every
confidence band in one gesture. The tracked changes themselves are untouched.

## Running it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

## How it works

| Stage | What happens |
|---|---|
| **Ingest** | Scan → page images. Blank reverse sides from duplex scanning are dropped automatically (the reference scan was 56 images for a 28-page document). |
| **Interpret** | Each page is read **twice, independently**, by readers given deliberately different search strategies. Both readers see Dave's four reference sheets and the full base text. |
| **Consensus** | Readers agree → **green**. They disagree and a tiebreaker resolves it → **yellow**. Tiebreaker also unsure → **red**. |
| **Reconcile** | Document-wide: resolve circled-letter cross references, apply `STET`, derive renumbering, propagate defined terms. |
| **Apply** | Real OOXML `w:ins`/`w:del` revisions with a `w:highlight` confidence band. |

### Confidence is measured, not self-reported

A model asked "how confident are you?" is poorly calibrated and says *high* too
often. Inter-reader agreement is an empirical signal, so that is what drives the
colour. This is the mechanism that made the original hand-run three-person-team
prompt work; here it costs a third call only when the first two disagree.

### Queries are never silently applied

Dave's `[?]` mark is a question, not an edit. It is emitted as a tracked
**insertion in red**, so **Reject removes it cleanly** and it can never end up in
a filed document. The same is true of a circled-letter insertion whose payload
never made it into the scan.

## What the reference document taught us

Everything below was measured against `DIP-Motion-v7_JENNIS_REDPEN_TRACKED.docx`
(28 pages, 169 logical edits) and drove a design decision.

- **Anchoring by quoted text works.** Of 166 anchored edits, **0 failed to
  resolve** and 88.6% were unique document-wide; with paragraph scope,
  substantive edits resolve **100%**. Placement does not need pixel coordinates.
- **27% of the "edits" were one edit.** 45 of 169 were the renumbering cascade
  (`4.→5.` … `48.→49.`) caused by inserting a single paragraph, because the
  document types its list numbers as literal text. These are now *derived*, not
  read — removing 45 chances to fumble a transcription, and the only real source
  of ambiguous anchors (`"6."` matches inside `"26."`).
- **Confidence looked better than it was.** All 45 renumbers were green. On the
  121 substantive edits the real split was 35% green / 47% yellow / 18% red —
  the human review was carrying more weight than the raw file suggested.
- **Page-independent reading is unsafe.** An edit on page 1 introduced the
  defined term `(the "Condo Building 1")`, which changed how `Condos` on page 2
  had to be read. Defined terms now propagate forward.
- **OCR is useless here.** The scan's text layer renders Dave's pen as garbage
  (`Adelaide 7 l.JJJ: ;lL ii/Jj::J`) and corrupts the printed text underneath.
  Vision on page images is mandatory.
- **Word XML has two traps** that silently corrupt offsets: a `w:p` can contain
  another `w:p` (textboxes — 482 runs in this document), and one `w:r` can hold
  several `w:t` children. Both are handled in `app/pipeline/wordxml.py`.

## Evaluation

The document layer is tested without any API key:

```bash
python eval/make_fixtures.py fixtures/known_good.docx fixtures
python eval/roundtrip.py
```

`make_fixtures` recovers the true base by rejecting every revision in the
known-good output. `roundtrip` replays all 161 ground-truth edits into it and
compares by accepting every revision in both documents.

```
edits replayed : 161
applied        : 161   failed: 0
accept-all text: IDENTICAL to known-good
```

So any difference in a live run is an *interpretation* difference, not a writer
bug — which is the whole point of separating the two.

## Layout

```
app/
  main.py               FastAPI: upload, job status, download
  static/index.html     drag-and-drop UI
  pipeline/
    ingest.py           scan -> page images; .docx -> text
    reference.py        Dave's baked-in reference, as a cached prompt prefix
    schema.py           edit vocabulary + anchor safety rules
    interpret.py        two readers + tiebreaker  (needs ANTHROPIC_API_KEY)
    reconcile.py        span normalisation, STET, xrefs, renumbering
    apply.py            OOXML tracked-changes writer
    wordxml.py          safe paragraph traversal
reference/dave/         four sheet images + markup grammar
eval/                   fixtures + round-trip test
```

## Notes

- Dave's letterforms are stored as **images**, never as prose descriptions. The
  accuracy comes from visual matching against his actual hand; a written account
  of how he shapes an "a" is a lossy copy of that signal. Only his *conventions*
  — shorthand, symbols, `STET`, circled cross-references — are text
  (`reference/dave/profile.md`).
- The reference is sent as a cached prompt prefix, so the four sheets are paid
  for once per run rather than once per page.
- Two collisions in his notation are documented and deliberately routed to the
  tiebreaker rather than guessed: `TP` means both *new paragraph* and
  *Plaintiff*; `Δ` (Defendant) resembles an insertion caret.

### Worth fixing at the source

These motions type their list numbers by hand. Switching to Word's automatic
numbering would make the entire renumbering class of edit disappear — Dave
wouldn't have to mark it and the app wouldn't have to read it.
