# Dave's markup grammar

Transcribed once from the cheat sheet. **Letterforms are NOT described here on
purpose** — they stay as images (`sheet_p1.png`..`sheet_p4.png`), because visual
few-shot matching against the real handwriting is what produces the accuracy. A
prose description of how someone shapes an "a" is a lossy copy of that signal.

What *is* captured here is the part that genuinely is rules rather than pixels:
his conventions, shorthand and abbreviations.

## Markup types (sheet p3 — each demonstrated on a sample sentence)

| Mark | Meaning | Operation |
|---|---|---|
| `^` + word in margin | insertion | `insert` |
| line from caret out to margin | insertion, content in margin | `insert` |
| strikethrough | deletion | `delete` |
| strike + write-in | replacement | `replace` |
| `TP` | new paragraph here | `split_para` |
| `FN` / `EN` + text below | footnote drops here | `footnote` |
| `[?]` | "verify this" — a question to the user | `query` |
| curved swap loop | transpose two words | `transpose` |

## Shorthand (sheet p2 §6)

`ad`=and · `tl`=the · `tle`=the · `w/`=with · `w/o`=without · `Bk`=Bank
`Recvr`=Receiver · `R`=Receiver · `Pl`=Plaintiff(s) · `Def`=Defendant(s)
`Ct`=Court · `Cmplt`=Complaint

## His own additions (sheet p2 §7)

- **`STET`** — ignore the prior revision. Cancels an earlier mark; it is an
  operation *on another operation*, so it must be applied after all edits are
  collected, not inline.
- **circled `A` / `B`** — insert text that is written elsewhere as circled A / B.
  The marker and its payload can be on different pages. Resolved document-wide.
- **"insert A"** — the payload may not be in the scan at all; it can be a
  separate memo or a voice recording. If unresolved, surface as a red `query`.

## Abbreviations (sheet p4 §11)

`Δ`=Defendant · `⫪`/`TP`=Plaintiff · `ICBL`/`ICBP`=Bank (Independent Bank Corp)
`ANDE`=affiliated non-debtor entities · `ANPE`=affiliated non-project entities
`LMLC`=Leestma Management Corporation · `LMCF`=Leestma Management Corporation

## ⚠ Known collisions — resolve by context, never by guessing

1. **`TP`** means *new paragraph* (p3) **and** *Plaintiff* (p4).
   Heuristic: in the margin at a line break → paragraph break; inline within a
   sentence where a party name belongs → Plaintiff.
2. **`Δ` (Defendant)** is a triangle and can be confused with the **insertion
   caret `^`** on a fast scan.
   Heuristic: a caret sits *below* the baseline pointing up into a gap and has
   an accompanying margin word; a Δ sits *on* the baseline as a standalone token.

Both collisions should normally surface as reader disagreement and route to the
tiebreaker rather than being silently resolved.

## Symbols (sheet p2 §8)

`$` · `%` · `§` section · `¶` paragraph · `&` and · `#` number · `@` at ·
`/` or/slash · `( )` parentheses
