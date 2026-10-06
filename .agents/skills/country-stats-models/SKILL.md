---
name: country-stats-models
description: The country-year domain — OWID emissions and energy, World Bank WDI and the country dimension, Eurostat electricity prices. Coverage that thins per column, the current-vs-constant dollar trap, the WDI incremental window and its restatements, World Bank naming and padding, and the semi-annual grain that must not be averaged. Use when editing anything in the country_stats dbt group, adding a WDI indicator, charting a country-year metric, or reasoning about which countries and years a column actually covers.
---

# Country statistics (the `country_stats` dbt group)

The dominant grain of this warehouse: `(country_iso3, year)`, joined on ISO3 code
and year, with `marts.dim_country` supplying `region` and `income_group`. Four
publishers land here — OWID CO2, OWID energy, the World Bank (the country
endpoint and WDI) and Eurostat electricity prices — and the models are
`stg_co2`, `stg_energy`, `stg_wdi`, `stg_country`,
`stg_eu_electricity_prices{,_semiannual}`, `int_country_year_observed`,
`dim_country_year`, `fct_emissions_energy` and
`fct_eu_electricity_prices_semiannual`.

**What is in here is what a chart gets wrong silently.** Every bullet below is a
number that comes out plausible on the wrong basis: an energy series cut to the
wrong year, a rank computed in current dollars, a semi-annual price averaged to
an annual one nobody paid. None of it is a build failure and none of it is
caught by a data test.

## The spine, and what the grain leaves out

**The country-year is the dominant grain, not a house rule.** The exceptions are
deliberate: Eurostat prices keep their published `(country_iso3, year, half)`
grain (the annual average is a price nobody paid — see below),
`fct_cbam_exposure` has no year, and the FX tables have no country. Reaching for
`dim_country_year` when the thing modelled isn't a country-year is how a fact
gets a fabricated dimension.

**The fact hangs off the spine, not off a source.** `dim_country_year` is
`dim_country` × every year the data covers (bounds read from the sources);
`fct_emissions_energy` inner-joins it to the union of country-years any source
reports, then left-joins each source. So:

- A country-year only one source reports still reaches the mart. Expect nulls in
  the columns the others don't cover, and filter charts for what they need.
- The dimension decides *what a country is*: codes it doesn't carry — the World
  Bank's aggregates (`WLD`, `EUU`), Antarctica — cannot reach the mart.
- `max(year)` reports whichever source is furthest ahead, so
  `mart_covers_recent_years` measures each source column separately.
- The spine is the full cross join; left-join a fact onto it to see coverage gaps
  as rows.

## `income_group` and `region` are today's answer, applied to every year

The World Bank `/country` endpoint publishes only the current classification, so
every rollup by income group inherits it. `marts.dim_country_income_history`,
transcribed from the publisher's own history (`OGHIST.xlsx`, by
`scripts/build_income_classification_seed.py`), measures the cost:

| Year | Economies classified | In a different group today |
|------|---------------------|----------------------------|
| 1990 | 174 | **89 (51%)** |
| 2000 | 203 | 102 (50%) |
| 2010 | 211 | 59 (28%) |
| 2020 | 212 | 22 (10%) |
| 2025 | 213 | 0 |

The current year's 0 is an invariant — the same classification reached two
ways — so a `warn` test fires when they stop agreeing, which is what a July
reclassification looks like before anyone re-runs the script. No model is
repointed at the history yet: that is a contract change on every relation
carrying `income_group`, and a separate decision.

- **A trend by income group reads the history directly**, as
  `reports/sources/warehouse/co2_intensity_by_income.sql` does for the Country
  Explorer. The Explorer's chart used to group every year by today's list *and*
  average country ratios (Bhutan weighted like China), and each choice alone
  moves it; together they invert 1990. Upper-middle income ranked most
  carbon-intensive at 1.00 kg/$ on the old basis. Grouped as classified and
  weighted by output (sum over sum), low income did at 1.39, because China was
  low income until 1998 and India until 2006. A single recent year is fine on
  today's list, and a before-and-after comparison wants a fixed list, which
  today's is.

## Revisions: `snap_co2_estimates`

`snap_co2_estimates` is an SCD2 snapshot of `stg_co2` (`co2_mt`,
`co2_per_capita`, 1990 onwards, `check` strategy, `hard_deletes='invalidate'`):
OWID restates published years, and every other model overwrites the old number.
`marts.fct_co2_estimate_versions` summarises it and
`reports/pages/restatements.md` renders it. A snapshot is state, not a build
artifact — deleting `data/warehouse.duckdb` destroys it — and how each release
carries it forward is `publishing-a-release`.

- **Verify a snapshot change by simulating a revision**, never in the real
  warehouse — the fake version stays in the history even after a re-ingest. With
  `WAREHOUSE_PATH` and `LAKEHOUSE_DIR` pointed at copies: build, `update
  lakehouse.raw.owid_co2 set co2 = co2 * 1.05 where iso_code = 'DEU' and year =
  2019` through `just sql write`, build again, and read
  `fct_co2_estimate_versions`.
- **Evidence cannot write a zero-row source to Parquet** ("too small to be a
  Parquet file"), so `reports/sources/warehouse/co2_estimate_versions.sql`
  selects every country-year and the page filters on `is_revised` itself. CI
  starts from an empty warehouse, so there every row is version 1, and the page's
  "nothing revised yet" branch is the honest state, not a broken build.

## Ingesting the World Bank

- **WDI's incremental window is 5 years, and that's about restatements.** The
  watermark (`max_year_by_indicator`, in dlt's resource state — one entry per
  indicator, so a newly added code still pulls its whole series) is *not* the
  fetch floor: `wdi_start_year()` subtracts `WDI_LOOKBACK_YEARS`, because the
  World Bank revises years it has already published. Merging on
  `(indicator, country_code, year)` is what makes the partial fetch safe. Two
  things it gives up, both deliberate: a country-year the World Bank *withdraws*
  stays in `raw.wb_wdi` until a full reload, and a restatement older than the
  window is never seen — `just ingest-wdi-full` (`INGEST_WDI_FULL=1`) re-fetches
  everything, and `just backfill-wdi 1997` re-fetches exactly that year, a year
  range passed to the asset as run config. The lookback and the backfill sit
  *beside* each other on purpose: the daily path stays cheap and unattended, and
  reaching further back is an explicit act with a window you can point at. dlt resets its own state when the destination is empty, so
  deleting the warehouse still gives you a full load; dropping *just* the raw
  table does not.

- **`wb_wdi`'s column types are declared, not inferred** (`WDI_COLUMNS`). It's
  the one resource whose schema isn't dropped and re-inferred each run, and
  `value` mixes counts with ratios — a lookback window that happened to hold only
  integers would infer bigint and shunt the next ratio into a
  `value__v_double` variant column.

## Coverage, naming and the shape of each source

- **Polars CSV type inference** defaults to the first 100 rows. OWID's early rows
  are empty for most metrics, so `pl.read_csv(..., infer_schema_length=None)` is
  required or numeric columns land as VARCHAR.
- **World Bank JSON is snake_cased by dlt**: API `iso2Code`/`capitalCity`/
  `incomeLevel.value` land as `iso2_code`/`capital_city`/`income_level__value`.
  Verify column names against `information_schema.columns` before writing SQL.
- **The World Bank doesn't list every ISO3 OWID emits for.** Taiwan (~286 Mt CO2,
  bigger than the Netherlands) and ten small territories arrive with a null
  `region`, so any `where region is not null` silently drops them from regional
  rollups. `dbt/seeds/country_overrides.csv` fills them in and `stg_country`
  unions it in. Antarctica is deliberately left out — a null `region` should mean
  "not a country". Coordinates use `try_cast`: the API sends `''` for territories.
- **World Bank region names are padded** — `'Sub-Saharan Africa '` and
  `'Latin America & Caribbean '` come back with a trailing space. `stg_country`
  trims them, so join and group on the trimmed values.
- **WDI is not only observations, and `stg_wdi` cuts it back to them.** The
  World Bank once served `SP.POP.TOTL` out to **2050** — 155
  countries, 2026-2050, every other indicator null on those rows — and it broke
  the nightly, the dashboard deploy and (had it run) the monthly release, because
  `stg_wdi.year` carried a literal `max_value: 2030`. Three things worth keeping:
  - **A ceiling in a test cannot keep a projection out of the warehouse**; it can
    only redden the build. The rule is a `where` clause in `stg_wdi.sql` now
    (`year <= extract(year from current_date)`), and the test's ceiling is gone
    rather than restated, which would make it a test that cannot fail.
  - **The literal was blind below itself.** The artifact evidences the 2031-2050
    block and only that — 3,100 country-years, which is what the test could see.
    Replaying a 2026-2050 projection against a throwaway lakehouse reproduces
    that number exactly *and* passes a further **775** rows, 2026 through 2030,
    into the spine, the latest-year queries and every per-capita join that reads
    `population`, indistinguishable from an observation. Whether the World Bank
    served that lower block is unknowable now; that the ceiling would not have
    stopped it is measured.
  - **The drop is visible without a test.** `pipeline_sources` takes its year
    span from `raw` and `pipeline_tables` takes `stg_wdi`'s from `staging`, so a
    publisher who does it again shows up on the Pipeline page as the two
    disagreeing — which is why no `severity: warn` guard was added for it.
  The API served the projections for at most ~28 hours before going back
  to 1960-2025; the recorded fixtures never held them, so
  nothing needed re-recording.
- **The World Bank's API can serve a stale copy of one URL for up to two days.**
  `NY.GDP.MKTP.KD?format=json&per_page=10000&page=1` returned a 2022 edition of
  the series (`lastupdated` 2022-07-22, 1990-2020, 8,091 rows) whose every
  `countryiso3code` is empty; any other `per_page` got the current one. The rows
  land, `stg_wdi` drops all of them, and `gdp_constant_usd` is null everywhere —
  which surfaced as an empty `analytics.co2_intensity` two layers down. It came
  back for other series on 22 and 27 September, always on the full-series URL.
  - **The fetch now asks again, once.** `_fetch_wdi_indicator` treats a series
    with rows but no three-letter code as stale, prints a line naming its
    `lastupdated`, and re-requests it at `WB_STALE_RETRY_PER_PAGE`. A second
    stale copy raises with the indicator, edition and URL — which the run log
    never showed while only the check could fail, because Dagster does not
    print a check's metadata.
  - **The retry changes a value, not the order.** The origin's cache key
    ignores query-parameter order and Cloudflare's does not, so a reordered URL
    gets past the edge to the same stale origin copy.
  - `wdi_indicators_all_present` still counts only rows `stg_wdi` keeps, so it
    fails at `raw` on what the retry cannot see: an empty series, or a copy
    with *some* codes.
  - **Two caches hold it, a day each.** The origin keeps a response for 24 hours
    and sends the time it has left as `max-age`. Each Cloudflare edge then keeps
    what it fetched for 24 hours from its own fetch, ignoring that `max-age`, so
    an edge that fetched late in the origin's day serves the copy well into the
    next. A runner reaches a different edge from a laptop: a fresh response here
    says nothing about the one CI will get.
  - **To confirm a suspect series**, compare `per_page` values with `curl -sD-`
    and read `cf-cache-status`, `age` (seconds since this edge fetched; the copy
    goes at 86,400), `last-modified` (when the origin made it) and the payload's
    `lastupdated`. **Never read `expires`**: Cloudflare recomputes it on every
    response as the time plus `max-age`, so it never says when a copy goes.

- **"Latest year" is per column, not per table.** `max(year)` on the mart is
  whichever source runs furthest ahead (Eurostat prices, a year beyond the rest),
  and coverage thins out unevenly before that: `co2_mt` holds 214 countries into
  the latest year, `primary_energy_twh` collapses from ~210 to **79**,
  `consumption_co2` stops a year earlier still. The collapse is a publishing
  lag: OWID joins the Energy Institute's review (79 countries, current, and the
  same 79 that `renewables_share_pct` holds) to the U.S. EIA's data (the rest,
  a year behind). Cutting an energy chart to the
  latest CO2 year quietly drops two thirds of its sample. The Evidence layer
  reads `sources/warehouse/latest_years.sql` — latest year per *metric family*,
  each with its own coverage floor — instead of hardcoding a literal; add a
  family there before charting a column whose coverage curve differs.
- **`renewables_share_pct` covers 79 countries; the `*_elec` columns cover ~210.**
  OWID's broad-coverage energy series is the *electricity* mix, not the
  primary-energy mix. For anything where country coverage matters, prefer
  `renewables_share_elec_pct`, `low_carbon_share_elec_pct` or
  `carbon_intensity_elec_g_kwh` (gCO2e/kWh, lifecycle, which
  also reads directly: coal grid ~800, gas ~400, nuclear/hydro under 50). They
  answer a narrower question — electricity is roughly a third of energy use — so
  the two are not interchangeable in levels, only in intent.
- **The mart has two renewable-electricity shares, and one is frozen.**
  `renew_elec_pct` is WDI's `EG.ELC.RNEW.ZS`, the IEA's figures republished by
  the World Bank, and it ends in 2021 while the rest of WDI moves on;
  `renewables_share_elec_pct` is OWID's and current. Take the OWID one: it comes
  from the same accounts as the other `*_share_elec_pct` columns (it plus
  nuclear is `low_carbon_share_elec_pct` exactly), where WDI's total renewables
  sits more than 0.5 pp below OWID's solar plus wind in 59 country-years.
  **Never `coalesce` them into one series**: they agree to a median 0.3 pp but
  differ by over 20 pp for some small grids (Equatorial Guinea 2021: 8.8% WDI,
  32.9% OWID), which a splice turns into a jump in 2021.
  - **The WDI series is also the one a watermark with no carried rows would
    fail on**: its five-year lookback holds no values, so
    `wdi_indicators_all_present` reports it missing. Every workflow today
    starts with neither the watermark nor the rows, so it fetches the whole
    series.
- **Territorial vs. consumption-based emissions.** `co2_mt` is what a country
  burns; `consumption_co2` adds the carbon embodied in imports and subtracts
  exports (~120 countries, one year behind). It exists so "the cut was just
  offshored" can be measured rather than caveated — **in tonnes, not by comparing
  the two percentage changes.** The UK's territorial fall since 2005 is 46% and
  its consumption fall 36%, which reads as a fifth of the cut moving abroad; in Mt
  the consumption fall is the *larger* (279 against 263), because a net
  importer's consumption total starts from a bigger base. Finding 4 once shipped
  the percentage reading; the test is the change in
  `consumption_co2 - co2_mt`. `trade_co2_share` is deliberately
  untested — the real range is about -98% to +1023% (Singapore imports ten times
  what it emits), so a 0–100 bound would fail on reality, not on a bug.
- **Two carbon-intensity columns, different bases.**
  `fct_emissions_energy.co2_kg_per_gdp_ppp_2011` is OWID's kg CO2 per 2011
  international-$ (PPP) and stops in 2022 / 164 countries.
  `analytics.co2_intensity.co2_per_gdp_const_usd` is derived in
  `transform/co2_intensity.py` and tracks the mart — ~197 countries through 2024,
  but only back to 1960, where WDI starts. Levels aren't comparable between the
  two; the rank uses only the derived one. The mart's column was called
  `co2_per_gdp` until the v2 rename, which is the whole reason that model is
  versioned — see `contracts-and-data-quality`.
- **Divide by `gdp_constant_usd`, never `gdp_usd`, for anything measured over
  time.** `gdp_usd` (`NY.GDP.MKTP.CD`) is *current* US$, so it moves with
  inflation and the exchange rate: on that basis Japan cut emissions 21% from
  2010–2024 and still scored 10% *worse* on carbon intensity.
  `gdp_constant_usd` (`NY.GDP.MKTP.KD`, constant 2015 US$) is the real-terms
  series. **The same failure is now measurable rather than narrated** — see the
  Currency section: the EU's average household electricity price rose about 26% or
  6% between 2021-S1 and 2022-S2 depending only on whether you counted in euros or
  dollars.
  - **The yen figure was wrong here and in `transform/co2_intensity.py` once,
    and it was wrong in the way a plausible number is.** It said the
    yen "fell 28% against the dollar"; 28% is Japan's *current-dollar GDP* fall
    (5.812 → 4.190 tn), i.e. the effect written down as the cause. The yen went
    **87.7 → 151.4 JPY/USD** on ECB annual averages — it lost **42%** of its
    dollar value. The full decomposition, which is worth keeping because it shows
    the currency term dominating: current-$ GDP ×0.721 = real growth ×1.104 ×
    dollar value of the yen ×0.579 × a ×1.128 residual (domestic prices).
  - **"Current US$ is fine for single-year cross-sections" is the shorthand, and
    it means *internally consistent*, not *the same answer*.** Ranking countries
    within income group for 2024 on each basis moves **166 of 194 — 86% — to a
    different rank**, worst move 26 places. Over time it is worse than a ranking
    change: of the 193 countries with both series in 2010 and 2024, **30 flip the
    sign of their decarbonisation trend**, five from improving to worsening
    (Nigeria −13.5% → +76.3%, then Brazil, Japan, Lesotho, Namibia).
- **World Bank WDI** is fetched long (one row per indicator/country/year) and
  pivoted to wide columns in `stg_wdi.sql`. Add indicators in two places:
  `WB_WDI_INDICATORS` in `ingest/sources/worldbank.py` and a `max(case …)` in `stg_wdi.sql`.
  The dict already carries the column name, so those two places restate the
  same mapping — `tests/test_ingest.py` holds them together, including against
  a code pointed at the *wrong* column, which no range test can see.
- **Eurostat is JSON-stat** — a flat `value` dict keyed by a row-major index over
  all dimensions. `eu_elec_prices` filters every dimension but `geo`/`time`
  server-side, then walks that grid (see `ingest/sources/eurostat.py`). Its `geo` codes are ISO2
  *except* `EL`=Greece (GR) and `UK`=UK (GB);
  `stg_eu_electricity_prices_semiannual.sql` remaps those and joins `stg_country`
  for ISO3. It prices 41 countries, so the mart column is null for the rest of
  the world. (The `length(geo) = 2` filter there drops `EU27_2020` and friends
  from the rows but *not* `EA` — two letters. That falls out at the inner join,
  which no ISO2 matches.)
- **The 41 are not the EU, and a mean over them is not the EU's average.** They
  are the 27 members (`is_eu_member`, today's membership at every half-year),
  Iceland, Liechtenstein, Norway, the United Kingdom and ten candidates and
  potential candidates, Turkey, Ukraine and Georgia among them; 13 of the 14
  non-members are cheaper than the members' mean. Three figures answer to "the
  EU average" for 2025-S2, and only the first is one:
  - **0.290 EUR/kWh, `eu27_price_eur_kwh`**: Eurostat's `EU27_2020` aggregate,
    each national price weighted by that country's latest household consumption
    (its reference metadata, section 18). It lands in `raw` as a row and is
    carried as a column, the same on every row of a half-year, because it is no
    country. Every chart titled an EU average reads it, through
    `reports/sources/warehouse/eu_average_price.sql`.
  - **0.255, the plain mean of the 27**: what the average member country
    charges. It runs 8-17% below the aggregate, and the gap is a finding, not
    noise: it was narrowest in 2022-S2, when the aggregate had risen 26% since
    2021-S1 and the plain mean 38%, because Germany rose 5% and France 13% while
    four smaller members at least doubled. The half-years page charts the two.
  - **0.227, the mean over the 36 countries priced in every half-year since
    2015**: the average of no group anyone would name. The site's "EU average"
    was this until it was measured against the other two.
  The aggregate's `not_null` test is what notices an enlargement: Eurostat
  renames the code when membership changes (`EU28` became `EU27_2020`), and the
  member list in `stg_eu_electricity_prices_semiannual.sql` is typed by hand.
  **The test skips the latest half-year**, because Eurostat publishes the early
  reporters' prices before the aggregate: 2026-S1 arrived with 13 countries,
  12 of them members, and no EU average. A rename nulls every period, so the
  test still sees one. Anything that needs a complete half-year filters on the
  aggregate being present, as `eu_average_price.sql` does: its fixed panel of
  members otherwise shrinks to the early reporters for the whole history.
- **Eurostat prices are semi-annual, and both grains are modelled.**
  `stg_eu_electricity_prices_semiannual` is the cleaning model at
  `(country_iso3, year, half)`; `stg_eu_electricity_prices` averages it to annual
  so it can join the country-year spine. Averaging is what the annual grain costs,
  and the cost is large enough to model around: the mean absolute half-over-half
  change was 19% across countries in 2022 and 13% in 2023 against 3–5% through the
  2010s, and the Netherlands went €0.034/kWh in 2022-S1 to €0.142 in S2 (+320%) as
  that year's energy-tax cuts landed in the first half. The annual €0.088 is a
  price nobody paid. Chart prices *over time* off
  `marts.fct_eu_electricity_prices_semiannual`; use the annual column only to
  join prices to emissions or GDP.
- **An "annual" price can be one half-year.** Eurostat publishes S1 around May and
  S2 the following spring, so `n_half_years` (staging) / `price_is_partial_year`
  (mart) exist to say when the average is over one half. It is *not* only a
  latest-year edge case — 29 country-years carry the flag, including 23 countries
  at the 2007 series start and one-offs like the UK in 2020 and Iceland in 2025.
  `sources/warehouse/latest_years.sql` counts only complete years for
  `price_year`, and the dashboard reports the partial count for the selected year
  rather than dropping those countries (in 2007 that would drop 23 of 27).
