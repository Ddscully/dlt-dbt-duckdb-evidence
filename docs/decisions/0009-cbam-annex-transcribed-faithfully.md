# 0009. The CBAM seeds transcribe the annex faithfully, and the mart handles its defects

Status: accepted 2026-08-09

## Context

The `cbam_*` seeds are transcribed from Annex I of the CBAM default-values
regulation, a legal instrument. As first published it had documented quirks:

- Albania's white Portland cement was published with `-` for direct, indirect
  and total, and its three values sitting in the *mark-up* columns.
- Five cement rows (Angola, Argentina) **compounded** the mark-up (x1.1, x1.21,
  x1.331) where the other rows added it; the mart flagged them with
  `markup_schedule_is_irregular`.
- Chile's line pipe had a total and a blank 2026 cell.
- Some goods carried no value in any country, including the fallback.

## Decision

The seeds carry the annex as published, defects included, and the mart is where
they are handled. Cleaning it in the seed would put this project's judgement
between the regulation and a euro figure.

## Rejected

- **Correcting the quirks in the seed.** Implementing Regulation 2026/1740
  corrected three of the four, which vindicates the policy: the body that wrote
  the instrument corrected it, where this project would have baked its guesses
  in. The migration to the corrected annex (2026-08-18) left Albania's cement a
  clean `-` and removed the compounding rows and Chile's blank cell.

## Consequences

- **Chile's row outlived itself as a rule.** It proved the fallback is a
  **row-level rule, not a column-level one**: a per-column `coalesce` paired
  Chile's tonnage with the fallback's mark-up and produced a 100% implied rate,
  a row that exists nowhere in the regulation. Direct, indirect and total still
  have to be read off one source (`compliance-models`).
- The goods with no value anywhere survive the correction: they are 4-digit CN
  headings whose subheadings hold the numbers, and the mart excludes them.
