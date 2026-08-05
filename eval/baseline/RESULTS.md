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
