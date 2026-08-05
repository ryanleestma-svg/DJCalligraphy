# Measured results

All figures against the reference document: `DIP-Motion-v6 copy.docx` marked up
by hand, 31 scanned pages, 116 substantive ground-truth edits (the renumbering
cascade excluded, since it is derived rather than read).

## Document-level score — the metric that matters

Similarity of the finished document (all revisions accepted) to the known-good
output. Queries excluded, since they are designed to be rejected.

| configuration | score |
|---|---|
| do nothing (base document) | 0.8147 |
| all produced edits, no consolidation | 0.7967 |
| green edits only | 0.7899 |
| **consolidated (shipping config)** | **0.8533** |
| consolidated + verification pass | 0.8539 |
| consolidated + verifier corrections applied | 0.7932 |

Two results worth keeping in mind:

* **Applying only the green edits is worse than doing nothing.** A partial set
  leaves the document half-updated. The colour bands are for deciding what to
  scrutinise, never for deciding what to apply.
* **Overlapping edits were the whole problem.** Before consolidation the
  pipeline was net-harmful; the surplus edits rewrote the same runs twice.

## Edit-level

| | run 1 | run 4 (shipping) | run 5 |
|---|---|---|---|
| recall | 90% | 95% | 94% |
| missed | 12 | 6 | 7 |
| content exact | 26 | 49 | 49 |
| fragmentation | 2.22 | 2.53 | 2.99 |
| green / yellow / red | 52/194/46 | 73/190/89 | 72/241/89 |
| runtime | 2465s | 832s | 912s |

## Things tried and rejected

* **Verification pass** (`app/pipeline/verify.py`) — +0.0007 for 194 extra API
  calls as a filter; actively harmful as a rewriter (0.7932). Not enabled.
* **Stronger `replacement` wording** in the reader prompt, spelling out that
  every surviving word must be copied through. Intended to stop bulk deletions;
  instead pushed readers toward wider overlapping edits. Fragmentation
  2.53 -> 2.99, document 0.8533 -> 0.7370. Reverted; the risk is handled in
  `reconcile.normalise` instead, which does not depend on prompt compliance.
* **40-character slack in cluster matching** — single-linkage chaining collapsed
  six distinct marks into one. Page 2 fell from 29 reported edits to 3 against
  17 real ones. Overlap must be genuine.

## Writer

`eval/roundtrip.py` replays all 161 ground-truth edits into the recovered base
and reproduces the known-good document exactly (161/161 applied, accept-all text
identical). Any difference in a live run is an interpretation difference, not a
writer bug.

## Interventions tested with repeated measures

Two runs of the identical baseline differ by ~2 exact and ~3 wrong edits, so
single-run comparisons at this scale mean nothing. Everything below is repeated.

### Document context — REJECTED (n=4 each)

The hand-run process read the whole document for understanding first, and
worked through every edit in one conversation. Both were tested:

| metric | baseline | + document brief |
|---|---|---|
| found | 60.8 [60-61] | 61.2 [60-62] |
| exact | **32.5** [30-35] | 30.2 [29-32] |
| wrong | **20.2** [18-23] | 22.0 [20-25] |
| spurious | **16.5** [14-22] | 18.8 [13-27] |
| fragmentation | **1.5** | 1.6 |
| exact+close | **40.5** sd 1.7 | 39.2 sd 2.2 |

Carrying prior edits forward (arm B) was likewise a wash: a few edits moved
from "close" to "exact", more moved into "wrong".

**Per-page isolation is not what costs accuracy.** More context made the
readers more expansive, not more accurate.

### Image resolution — REJECTED (n=3 / n=2)

Vision downscales anything over 1568 px on the long edge, so whole pages have
always reached the model at ~143 dpi. A 2x2 overlapping grid raises that to
~230 dpi.

| | found | exact | wrong | exact+close |
|---|---|---|---|---|
| whole page | 61.3 | **32.0** | **19.7** | **41.7** |
| 2x2 tiles | **63.0** | 28.0 | 26.0 | 37.0 |

Magnification finds *more* marks but transcribes them *worse*. Losing the whole
page from view costs more than the extra detail gains: a caret only means
something in relation to the line it points into, and margin lines run across
the full width.

### Standing conclusion

Every lever that acts on reasoning or perception has come back a wash or a
regression: document brief, prior-edit carry-forward, adversarial verification,
stronger replacement wording, higher magnification. Recall is stable at ~95% and
wording agreement sits at ~60%. The residual error looks like genuine ambiguity
in the ink rather than anything the scaffolding can recover, which is what the
human review pass exists for.

## Conversation vs per-page — the 7-page result did NOT hold at 28

At 7 pages a single conversation clearly beat isolated per-page calls: exact
34.0 [32-36] against 29.5 [29-30], ranges not overlapping. On that evidence a
commit message claimed conversation was "adopted". It was not, and should not
have been: run.py has always called read_page, and the full-document numbers
say per-page is still ahead.

Full 28-page document, after bulk deletions are surfaced as queries:

| | edits | document score |
|---|---|---|
| do nothing | 0 | 0.8147 |
| conversation | 150 | 0.8449 |
| **per-page** | 180 | **0.8615** |

What conversation does better, and it is not nothing:

* fragmentation **1.26** against 2.15 - much cleaner, far fewer duplicate edits
* page alignment: 26 of 28 pages agree with ground truth on whether the page
  has any edits at all, including all six genuinely empty pages
* 35% faster

Why the 7-page win evaporated - two candidates, not yet separated:

1. **Image pruning.** Accumulating 28 pages of images exceeds the request size
   limit outright, so images from read pages must be dropped. The 7-page test
   retained every image; the shippable configuration cannot.
2. **Drift over a long thread.** Output tracks ground truth closely for the
   first ten pages and then over-produces roughly 2x: p21 truth 6 / conv 8,
   p22 4 / 9, p24 4 / 10, p26 4 / 10.

If drift is the cause, the fix is a conversation bounded to a SECTION rather
than the whole document - enough context for the argument to be continuous,
short enough not to wander. That is the obvious next experiment.

## Bulk deletions must never be applied — ADOPTED

A strike across a long span nearly always means "rewrite this", with the
replacement in the margin or on the back of the sheet. When the reader misses
the replacement it emits an empty-replacement delete, and the passage is
amputated. This is the one error a reviewer cannot catch by reading the result:
deleted text leaves no trace to notice.

Such edits now become red queries. The passage survives, the strike is
surfaced, one click clears the marker.

| | before | after |
|---|---|---|
| per-page | 0.8533 | **0.8615** |
| conversation | 0.8025 | 0.8449 |
