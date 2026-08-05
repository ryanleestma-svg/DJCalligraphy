# Fixtures

- `known_good.docx` — the tracked-changes output from the original hand-run
  workflow (28 pages, 169 logical edits). The reference answer.
- `base.docx` — generated: `known_good.docx` with every revision rejected.
- `truth.json` — generated: the ground-truth edit list.

Regenerate the two derived files with:

    python eval/make_fixtures.py fixtures/known_good.docx fixtures

The 17 MB markup scan (`dip motion reddits_0001.pdf`) is kept out of git for
repo size, not secrecy. Point the pipeline at it by path.
