---
name: compliance-models
description: The dbt compliance group — dim_grid_emission_factors, fct_example_scope2_emissions and fct_cbam_exposure, plus the cbam_* seeds and the Scope 2 and CBAM pages. Use when editing those models or seeds, running scripts/build_cbam_seeds.py, or migrating the CBAM annex to a new implementing regulation.
---

# Compliance models (Scope 2 and CBAM)

These are the `compliance` group in `dbt/models/_groups.yml`: regulatory products
built on top of `country_stats`, read by someone filing something, which is why the
lineage columns and the caveats travel with the rows.

## Scope 2 emission factors (`dim_grid_emission_factors`, `reports/pages/scope2.md`)

`carbon_intensity_elec_g_kwh` is already in the wide fact. It is modelled a
second time as `marts.dim_grid_emission_factors` because under the GHG Protocol
that series **is** the location-based Scope 2 emission factor — the number a
multi-site company multiplies its metered kWh by for the electricity line of a
CSRD / SECR / CDP disclosure. No new ingestion, no new analysis: the work was
packaging.

### The factor table

- **It is a product table, so the columns beside the factor carry the weight.**
  The factor in both units (`g_co2_per_kwh` as published, `t_co2_per_mwh` as
  meter data arrives — shipping only the first is how a filing gains a factor of
  1000), the vintage, and the lineage (`factor_basis`, `source_dataset`,
  `source_loaded_at`). The three constant-per-row columns are deliberate: the
  table ships as a standalone Parquet in the data release, and a factor detached
  from its basis is the one thing a reporter must not be handed.
- **`is_latest_available` is a filter, not a year, and that is the whole vintage
  problem.** "The most recent published factor for country X" resolves to 2025
  for 90 countries, 2024 for 105, 2023 for 10 and 2022 for 2, so a
  `where year = 2025` cross-section drops more than half the world. Same lesson
  as `latest_years.sql`, load-bearing here for a number with a legal
  consequence. A `unique_combination_of_columns` test with
  `config: {where: "is_latest_available"}` is what holds it to one row per
  country. Grid size is no protection: Ukraine's newest factor is 2022, on a
  111 TWh grid.
- **Not built on the spine, unlike every other model here.** A country-year with
  no factor is an absence, not a reference value; `dim_country_year` is where
  absences are rows. The dimension is still authoritative for what a country is,
  so five OWID territories (Guadeloupe, Martinique, Réunion, French Guiana,
  Falklands) carry no factor — the page says so rather than leaving the count
  unexplained.
- **`source_loaded_at` is why `stg_energy` now selects `_dlt_load_id`.** Same
  expression `dbt source freshness` uses. It answers "which extract did this
  number come out of", which is the assurance question, and it is the only
  reason that column exists in staging.
### The worked example is fabricated, and must stay obviously so

- **`fct_example_scope2_emissions` is invented and must stay obviously invented.**
  Twelve hypothetical sites (`seeds/example_scope2_sites.csv`) x real factors:
  582.5 GWh, 232,456 tCO2e, and the two cleanest-grid plants drawing 17% of the
  power for 1.7% of the tonnes. It is the only fabricated data in the warehouse
  and it *ships in the public data release*, so the "example" in both names, the
  seed description, the mart description, the `<Alert status=warning>` above the
  table and the release notes bullet are all load-bearing. Don't quietly rename
  it to something that reads as real.
- **The seed's countries must come from the fixture slice**, i.e. `COUNTRIES` in
  `scripts/record_fixtures.py`. The `not_null` tests on the factor join are real
  gates, and CI builds against the 17-country fixtures — the first draft was a
  twelve-site *European* group, which passed locally and failed `dbt build` with
  8 null factors under `INGEST_FIXTURES=1`, because only six of the fixture
  countries are European. This is the usual fixture-slice trap (CLAUDE.md's
  "17 countries will happily pass a threshold the full 200+ would break") running
  the other way: the slice is too *narrow* for a seed that joins to it. The
  global footprint is the fix and the spread is better for it.
### Caveats and figures

- **The three caveats are stated on the page, not hidden.** Location-based only
  (market-based needs RECs/GOs, which no public dataset carries), an annual
  average rather than hourly matching, and production- rather than
  consumption-based. Naming them is the difference between a credible reference
  table and a liability; a practitioner checks all three first.
- The page quotes a 57x spread across grids above 10 TWh where `findings.md`
  quotes 24x above 150 TWh. Both are correct and the page says why — if one
  moves, check the other.

## CBAM exposure (`fct_cbam_exposure`, the `cbam_*` seeds, `reports/pages/cbam.md`)

Annex I of Implementing Regulation (EU) 2025/2621, as corrected by (EU) 2026/1740
— the country x good default values an importer uses from 2026 when they have no
verified supplier data — transcribed into two seeds, priced with a third, and
multiplied by a carbon price. 11,665 rows over 121 countries and 260 goods. The
only model here with **no year in its grain**: it is a regulatory schedule, not a
time series.

### Why a seed, and what the key is

- **A seed, not a dlt resource, and that is the interesting decision.**
  Regulatory reference data is versioned by *amendment*, not by scrape; there is
  no API, and the values change when a new implementing regulation says so.
  `scripts/build_cbam_seeds.py` regenerates both seeds from the Commission's
  published workbook, so the next amendment is a re-run and a reviewable diff
  rather than a re-transcription. `country_overrides.csv` is the precedent.
- **Two seeds because normalising the goods out is worth 1.6 MB.** 12,540 value
  rows share 283 (product group, CN code, description) triples and one
  description runs to 250 characters. `cbam_goods` is 60 kB; inlined it would be
  1.6 MB of CSV and the same again in the warehouse.
- **A CN code is not a key** — it was flatly true and is now only mostly true.
  `2523 10 00` was both white clinker and grey clinker, whose values differ by
  more than 2x, so the grain is (CN code, description) and `good_key` is a slug
  of the pair. The 2026/1740 correction gives those two 10-digit **TARIC** codes
  (`2523 10 00 10` / `2523 10 00 90`) and closes that particular case, but the
  annex still prints 4- and 6-digit headings above the rows carrying the numbers,
  so the composite key stays. It is also what kept those two rows apart for the
  six months the codes could not, which is the argument for not renumbering to a
  surrogate.
### The transcription is faithful, defects included

- **The transcription is faithful, defects included, and the mart is where they
  are handled.** The annex is a legal instrument; cleaning it in the seed would
  put this project's judgement between the regulation and a euro figure. **Three
  of the four documented quirks were fixed by the 2026/1740 correction** — which
  is the vindication of the policy, not a reason to drop it: the body that wrote
  the instrument corrected it, and this project would have baked its guesses in.
  Kept here because the *handling* is still the reason parts of the mart look the
  way they do:
  - Albania's white Portland cement used to be published with `-` for direct,
    indirect and total and its three values sitting in the *mark-up* columns
    instead. Clean `-` now.
  - Five cement rows (Angola, Argentina) used to **compound** the mark-up —
    x1.1, x1.21, x1.331 — where the other 10,926 added it. With no published
    mark-up column there is nothing left to compound, and
    `markup_schedule_is_irregular` went with it.
  - Chile's line pipe had a total and a blank 2026 cell. Gone too, but it is
    what proved the fallback is a **row-level rule, not a column-level one**: a
    per-column `coalesce` paired Chile's tonnage with the *fallback's* mark-up
    and produced a 100% implied rate — a row that exists nowhere in the
    regulation. The rule outlives the row; direct/indirect/total still have to be
    read off one source.
  - 23 of the goods carry no value in any country, including the fallback. They
    are 4-digit CN *headings* whose subheadings hold the numbers, and they are
    excluded from the mart — rows that could only be priced at null. **This one
    survives**, and it is where `see below` lives (below).
- **Fertilisers carry a 1% mark-up in all three years**, not 10/20/30% and not
  1/2/3%, so the mark-up is a property of the product group — hardcoding one rate
  overstates every fertiliser line by nine points in 2026 and twenty-seven in
  2028. **The mart used to derive this and now asserts it**, which is a real loss
  and not a refactor. The annex published each good's marked-up value for each
  year, so `mode()` over published/total read the schedule off the data and an
  amendment moving a rate needed no edit. 2026/1740 publishes only direct,
  indirect and total. The schedule is the `cbam_markup_schedule` seed now —
  a seed and not a var or a `case`, so the carve-out stays reviewable as data —
  and it is confirmed against both the articles and the February annex's own
  columns, where all 10,929 priced rows imply exactly those rates. The stated
  rates are actually *cleaner* than the published ones: those carried rounding
  noise from the OJ's three decimals, so some rows implied 9,9% or 1,1%.
  - **What replaced the mark-up tests is `direct + indirect = total`** — the only
    internal consistency the corrected source still offers, and it reaches
    **2,781 of the 12,540 rows**, which is the part worth knowing before trusting
    it. `indirect` is published only for cement, fertilisers and 34 iron-and-steel
    rows; 8,129 rows carry direct and total with no indirect and nothing checks
    them. Tolerance 0.02, measured rather than generous: the annex rounds each
    column *independently*, so 711 of those 2,781 are inexact by 0.001–0.01 with
    nothing wrong (Albania's nitric acid is 2,73 + 0,04 = 2,76). It still catches
    the failure that matters, a column read from the wrong position, which is off
    by whole units.
### Parsing rules that must not soften

- **Round half up, not `round()`.** The OJ prints three decimals and the
  Commission's XLSX mark-up cells are live formulas, so they arrive as binary
  floats. Python's banker's rounding turns 7,7165 into 7.716 where the regulation
  says 7,717. Eleven rows across the seven spot-checked countries differed in the
  third decimal before `Decimal` + `ROUND_HALF_UP`. Small, but the column is
  multiplied by a carbon price and shown as money.
- **An unreadable cell raises; it must never become a `None`.** `_number` used to
  `return None` on any `ValueError`, and `None` is not an error downstream — it
  is the annex's own "no value here", which `fct_cbam_exposure` reads as *use the
  fallback row* and prices. So a cell the parser merely failed to understand
  would not surface as a gap but as a plausible euro figure attributed to a
  country the regulation never assigned it to. `NO_VALUE` is now the accepted
  blanks and anything else stops the script. Checked against all 37,620 value
  cells of the real workbook: **one** token was reaching that catch-all —
  `see below`, on the 4-digit CN headings 3102 and 3105 whose numbers live in
  the subheadings under them (2,610 cells). Its null was *right*, and arrived by
  luck; it is in `NO_VALUE_PHRASES` now, so it is a decision. This is the same
  argument as the Chile row above — the fallback is a rule the annex states, not
  a landing zone for whatever didn't parse.
### The 2026/1740 amendment

- **The seeds are Annex I as corrected by IR 2026/1740** (adopted 20 July 2026,
  in force 3 August, applying retroactively from 1 January 2026), which replaced
  Annexes I and IV in full. Migrated 2026-08-18. The amendment path paid for
  itself — re-running `build_cbam_seeds.py` was most of the work — but four
  things about it are worth keeping, because none are visible in the values:
  - **The Commission republishes at the same URL.** There is no versioned link;
    the workbook's `Version History` sheet is the only thing that says which
    amendment you are holding, and the `?filename=…v20260204…` query parameter in
    `ANNEX_XLSX_URL` is *stale and cosmetic* — the document id is what resolves,
    and it served v2 under a v1 filename. Check that sheet, not the URL.
  - **It failed loudly, which was luck rather than design.** The layout went from
    9 columns to 6, so `_route(row[8])` raised `IndexError` on the first sheet.
    Had the correction *added* a column instead, every field would have shifted
    one right and parsed fine into the wrong meaning. `COLUMNS` names the
    positions now and `parse_annex` refuses a row that isn't the expected width.
  - **`SHEET_TO_ISO3` being exhaustive is what caught the relabelling.** Ten
    countries were renamed to ISO-style long forms with no change of country
    ("Russia" → "Russian Federation", "Côte d'Ivoire" → "Ivory Coast") and two
    are new (Liberia, New Caledonia). The script stopped and named all twelve
    rather than writing blank ISO3 codes and dropping them at the mart's join.
  - **The values barely moved**: 66 of 10,503 comparable rows, 38 of them down by
    2% or less, 28 now blank. Everything expensive about the migration was
    structural.
- **`Annex IV` is a new sheet and is deliberately not transcribed.** It is the
  single *highest* default value per good, with no country dimension — a
  different table answering a different question, and the circumstances in which
  a declarant must use it instead of the country value are set by the articles,
  not the annex. Those articles could not be confirmed from a primary source
  (EUR-Lex does not serve to the fetcher), and inventing a legal trigger is
  exactly what the rest of this layer refuses to do. In `SKIP_SHEETS` with that
  reason written next to it. Same posture as Annexes II and III, different
  ground: those are excluded on licence, this one on not knowing.
### Building and pricing it

- **Dropping a seed column needs `dbt seed --full-refresh`.** dbt-duckdb derives
  the CSV's column spec from the *existing* relation, so removing the three
  mark-up columns failed with a sniffer error naming 10 columns against a file
  that plainly had 7 — and `--no-partial-parse` does not help, because the stale
  shape is in the warehouse and not in the manifest.
- **The ETS price is a dbt var (`eu_ets_price_eur_per_t`, EUR 75), not a
  measurement.** There is no clean free API for EUA spot. The mart ships the
  tonnage columns beside the euro columns and states the price per row, so the
  page draws its EUR 60-120 sensitivity from one build and a release consumer can
  re-price without rebuilding.
### What the unit tests hold

Four of them, in `dbt/models/marts/_unit_tests.yml`. This model's 20 data tests
are `not_null` and generous `accepted_range`s bar one, and they cannot be much
else:
the numbers are transcribed from a legal instrument, so there is no independent
quantity to check them against. What is testable is the *rules*, and mutation
against a warehouse copy says how much they were worth:

| mutation | data tests at the time (19) | effect |
|---|---|---|
| fallback resolved per column, not per row | **all pass** | **nothing moves at all** |
| mark-up hardcoded at 10/20/30% | **all pass** | fertiliser avg EUR 105.76 -> 115.18 /t |
| `excess_over_cleanest_source` partitioned by product group | **all pass** | 18,989 t -> 30,599 t |
| `count(*)` for `count(<total>)` in `priced_goods` | 7 fail | +875 unpriced heading rows |

- **The one the data tests catch is the one with no near-miss, and that is what
  the table is really measuring.** `having count(*) > 0` is a tautology over a
  `group by` — 283 goods out where the real clause gives 260 — so the mutation
  deletes the filter rather than weakening it. `priced_goods` is a binary rule:
  a good has a total somewhere or it has not, and there is no subtly-wrong
  version to write. The other three rules all have a plausible wrong answer, and
  all three are invisible to those 19 data tests. Keep the unit test anyway: the seven
  `not_null`s report 875 nulls across three columns, the unit test reports the
  two heading rows by `good_key`, and a failing unit test stops the model
  materialising instead of finding it afterwards.

- **The fallback rule is now unreachable by data, which is the argument for
  testing it.** Zero of the 12,540 seed rows have a null total beside a non-null
  direct or indirect — the Chile line pipe that proved the rule was corrected out
  of the annex in July 2026. The mutation is therefore *completely* invisible:
  not one figure in the warehouse changes. Same category as `dim_date`'s eleven
  unbuilt fiscal policies.
- **`markup_2026_pct` cannot be asserted at all.** It is a ratio of two doubles
  and the warehouse holds three distinct values that all print as `10.0`
  (9.99999999999998578915, 10.00000000000000888178, 10.00000000000003197442).
  A column with no exact value can carry a range test and nothing else — which is
  the real cost of the correction forcing the schedule from measured to asserted.
  The fixtures therefore use totals that *are* float-exact under the mark-up:
  2.5, 5, 10, 20, 40 and 80 for the 10/20/30% groups, and **50 and almost nothing
  else** for the fertilisers' 1%. Mali's hydrogen total is `0.0`, which is the one
  row where `nullif(total, 0)` makes the column null — the guard on real data.
- **`production_route_code` broke the row-level rule until 2026-08-24, and
  "consistent by luck" was the wrong reading.** It was read off the country's row
  while the tonnages came from the fallback. The *output* was null on all 755
  fallen-back rows, which is what made it look harmless; the *input* was not —
  **202 of them took their tonnages from a fallback row that carries a route**,
  and the mart threw it away. Six rows of grey hydraulic cement state it best:
  identical 1.28 / 0.09 / 1.37, the fallback row showing route `A` and the five
  countries using that very number showing blank. Annex I publishes no such row.
  The route is a property of the *value* — `_route`'s own docstring says the code
  is what separates a 0,13 tCO2e/t semi-finished steel from an 8,21 — so it comes
  off the row the tonnages came from.
  - **Not one euro moved.** 202 rows gained a route; row count held at 11,665 and
    the euro total at EUR 2,462,927.40 to the cent. That is why it survived: every
    range and null test here is over a numeric column, and the defect lived in a
    VARCHAR that `_marts.yml` gave a `data_type` and no description or test.
  - **The mutation table above could not have found it.** A mutation breaks a rule
    that is written down; this rule was stated in the model's prose comment and
    never implemented. Treat those comments as claims to verify.
  - Held now by `dbt_utils.expression_is_true` on the column — 202 rows red when
    reverted, and `store_failures` names them. Two things about how it is written:
    `is not distinct from` rather than `=`, because 553 of the 755 correctly
    resolve to null and `=` is unknown on a null, which `where not(...)` discards
    so the test would pass by not looking; and the `or is_country_specific` scope
    is in the *expression* rather than a `config: where:`, because `where` makes
    dbt_utils wrap the model as `dbt_subquery` and the correlated subquery then
    has to name that alias instead of the relation.
- **The fallback row is `is_country_specific = true`.** All 260 of them, because
  the flag keys on "this row has a total of its own" and the fallback does.
  `is_fallback_table` is the column that identifies it. Reads oddly, so it is
  pinned rather than left to be rediscovered.

### Licence and scope limits

- **Annexes II and III are deliberately not ingested.** They are the country
  electricity emission factors, and they are IEA data under **CC BY-NC-SA 4.0** —
  redistributing them would put a non-commercial and share-alike restriction on a
  data release that is otherwise entirely CC BY 4.0. `dim_grid_emission_factors`
  (OWID) sits beside the annex's numbers as context and **is not the same
  measurement**; the page says so. This is also why the mart carries only the grid
  factor and no derived reconciliation against the annex's indirect column.
### Reading it

- **The story is production route, not grid** — the opposite of the Scope 2 page.
  Semi-finished steel runs 63x from Azerbaijan to Indonesia, and sorting by cost
  sorts almost perfectly by the annex's route indicator (`E` scrap/EAF against
  `C`/`F` ore/BF-BOF), not by the country's grid — the correlation between a
  country's steel default and its grid factor is 0.32 (it was 0.26 before the
  2026/1740 correction; the spread held at 63x through it).
- **Excel mangles the country names, so `country_display_name` exists.** Sheet
  names cap at 31 characters and forbid some punctuation, which is why the annex's
  Koreas arrive as `North Korea (Democratic People’` and
  `Korea, Republic of (South Korea`, both cut mid-parenthesis. The seed keeps the
  annex's label because it is the legally meaningful one; the mart coalesces to
  `stg_country.country_name` for anything that goes on a chart. **Which names are
  mangled moves with the amendment** — before 2026/1740 the pair to quote were
  `Democratic Republic of the Cong` and `Myanmar_Burma`, and both of those are
  clean now while two others became truncated. That is the argument for
  coalescing to the dimension rather than patching labels one at a time.
