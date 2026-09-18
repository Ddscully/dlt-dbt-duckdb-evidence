# 0010. The CBAM mark-up schedule is a seed, asserted rather than derived

Status: accepted 2026-08-18

## Context

The CBAM default value carries a mark-up that rises by year: 10/20/30% for most
product groups, and 1% in all three years for fertilisers, a food-security
carve-out. The original annex published each good's marked-up value for each
year, so the mart read the schedule off the data with `mode()` over
published/total, and an amendment moving a rate needed no edit.
Implementing Regulation 2026/1740 publishes only direct, indirect and total.

## Decision

The schedule is the `cbam_markup_schedule` seed: a seed rather than a var or a
`case`, so the carve-out stays reviewable as data. It is confirmed against the
regulation's articles and against the February annex's own columns, where every
priced row implies exactly those rates.

## Rejected

- **Hardcoding one rate.** It overstates every fertiliser line by nine points in
  2026 and twenty-seven in 2028.
- **A var or a `case` expression**, which hides the carve-out in code.

## Consequences

- **This is a real loss, not a refactor.** An amendment that moves a rate now
  needs an edit to the seed; the mart can no longer notice it.
- The stated rates are cleaner than the published ones were, which carried
  rounding noise from the Official Journal's three decimals, so some rows
  implied 9.9% or 1.1%.
- The mark-up tests are replaced by `direct + indirect = total`, the only
  internal consistency the corrected source offers (`compliance-models`).
