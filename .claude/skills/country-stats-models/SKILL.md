---
name: country-stats-models
description: The country-year domain — OWID emissions and energy, World Bank WDI and the country dimension, Eurostat electricity prices. Coverage that thins per column, the current-vs-constant dollar trap, the WDI incremental window and its restatements, World Bank naming and padding, and the semi-annual grain that must not be averaged. Use when editing anything in the country_stats dbt group, adding a WDI indicator, charting a country-year metric, or reasoning about which countries and years a column actually covers.
---

# Country statistics (the `country_stats` dbt group)

The dominant grain of this warehouse: `(country_iso3, year)`, joined on ISO3 code
and year, with `stg_country` supplying `region` and `income_group`. Four
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

## Ingesting the World Bank

- **WDI's incremental window is 5 years, and that's about restatements.** The
  watermark (`max_year_by_indicator`, in dlt's resource state — one entry per
  indicator, so a newly added code still pulls its whole series) is *not* the
  fetch floor: `wdi_start_year()` subtracts `WDI_LOOKBACK_YEARS`, because the
  World Bank revises years it has already published. Merging on
  `(indicator, country_iso3, year)` is what makes the partial fetch safe. Two
  things it gives up, both deliberate: a country-year the World Bank *withdraws*
  stays in `raw.wb_wdi` until a full reload, and a restatement older than the
  window is never seen — `just ingest-wdi-full` (`INGEST_WDI_FULL=1`) re-fetches
  everything, and `just backfill-wdi 1997` re-fetches exactly that year through
  the partitioned asset. The window and the partitions sit *beside* each other on
  purpose: the daily path stays cheap and unattended, and reaching further back
  is an explicit act with a key you can point at. dlt resets its own state when the destination is empty, so
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
- **"Latest year" is per column, not per table.** `max(year)` on the mart is
  whichever source runs furthest ahead (Eurostat prices, a year beyond the rest),
  and coverage thins out unevenly before that: `co2_mt` holds 214 countries into
  the latest year, `primary_energy_twh` collapses from ~210 to **79**,
  `consumption_co2` stops a year earlier still. Cutting an energy chart to the
  latest CO2 year quietly drops two thirds of its sample. The Evidence layer
  reads `sources/warehouse/latest_years.sql` — latest year per *metric family*,
  each with its own coverage floor — instead of hardcoding a literal; add a
  family there before charting a column whose coverage curve differs.
- **`renewables_share_pct` covers 79 countries; the `*_elec` columns cover ~210.**
  OWID's broad-coverage energy series is the *electricity* mix, not the
  primary-energy mix. For anything where country coverage matters, prefer
  `low_carbon_share_elec_pct` or `carbon_intensity_elec_g_kwh` (gCO2/kWh, which
  also reads directly: coal grid ~800, gas ~400, nuclear/hydro under 50). They
  answer a narrower question — electricity is roughly a third of energy use — so
  the two are not interchangeable in levels, only in intent.
- **Territorial vs. consumption-based emissions.** `co2_mt` is what a country
  burns; `consumption_co2` adds the carbon embodied in imports and subtracts
  exports (~120 countries, one year behind). It exists so "the cut was just
  offshored" can be measured rather than caveated: the UK's territorial fall
  since 2005 is 46% and its consumption fall 36%, so about a fifth of the
  headline is trade moving and the rest isn't. `trade_co2_share` is deliberately
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
  versioned — see *Contracts, ownership and versions* above.
- **Divide by `gdp_constant_usd`, never `gdp_usd`, for anything measured over
  time.** `gdp_usd` (`NY.GDP.MKTP.CD`) is *current* US$, so it moves with
  inflation and the exchange rate: on that basis Japan cut emissions 21% from
  2010–2024 and still scored 10% *worse* on carbon intensity.
  `gdp_constant_usd` (`NY.GDP.MKTP.KD`, constant 2015 US$) is the real-terms
  series. **The same failure is now measurable rather than narrated** — see the
  Currency section: the EU household electricity price rose 35% or 13.5% between
  2021-S1 and 2022-S2 depending only on whether you counted in euros or dollars.
  - **The yen figure was wrong here and in `transform/co2_intensity.py` until
    2026-08-24, and it was wrong in the way a plausible number is.** It said the
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
  `WB_WDI_INDICATORS` in `ingest/pipeline.py` and a `max(case …)` in `stg_wdi.sql`.
  The dict already carries the column name, so those two places restate the
  same mapping — `tests/test_ingest.py` holds them together, including against
  a code pointed at the *wrong* column, which no range test can see.
- **Eurostat is JSON-stat** — a flat `value` dict keyed by a row-major index over
  all dimensions. `eu_elec_prices` filters every dimension but `geo`/`time`
  server-side, then walks that grid (see `pipeline.py`). Its `geo` codes are ISO2
  *except* `EL`=Greece (GR) and `UK`=UK (GB);
  `stg_eu_electricity_prices_semiannual.sql` remaps those and joins `stg_country`
  for ISO3. EU/EEA only, so the mart column is null for the rest of the world.
  (The `length(geo) = 2` filter there drops `EU27_2020` and friends but *not*
  `EA` — two letters. That falls out at the inner join, which no ISO2 matches.)
- **Eurostat prices are semi-annual, and both grains are modelled.**
  `stg_eu_electricity_prices_semiannual` is the cleaning model at
  `(country_iso3, year, half)`; `stg_eu_electricity_prices` averages it to annual
  so it can join the country-year spine. Averaging is what the annual grain costs,
  and the cost is large enough to model around: the mean absolute half-over-half
  change was 19% across countries in 2022 and 13% in 2023 against 3–4% through the
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
