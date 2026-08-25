# 01 — Grain is the contract

← [00 — Setup](./00-setup.md) · [Course index](./README.md) · next: [02 — Loading twice](./02-loading-twice.md)

**Objectives.** Say what one row of a table means, in one sentence, without
hedging. Recognise a fact that hangs off a source instead of a spine, and know
what it costs. Defend a grain that breaks the house convention.

**Prerequisites.** A built sandbox ([00](./00-setup.md)).

---

## 1. One row per what?

The grain of a table is the answer to "what does one row mean", and it is a
*contract*: everything downstream — every join, every average, every count — is
correct only if that sentence is true.

The dominant grain in this warehouse is **`(country_iso3, year)`**. Every
country-shaped staging model and the wide fact carry it, and `dbt_utils`'
`unique_combination_of_columns` asserts it rather than trusting it.

Three tables break it, and the three exceptions are the whole lesson:

| Table | Grain | Why |
|---|---|---|
| `fct_eu_electricity_prices_semiannual` | `(country_iso3, year, half)` | Eurostat *publishes* half-years. Averaging to a year is a real loss — the Netherlands went €0.034/kWh in 2022-S1 to €0.142 in S2, and the annual €0.088 is a price nobody paid |
| `fct_cbam_exposure` | `(sourcing country, good)` — **no year** | a regulatory schedule is not a time series. It changes when an implementing regulation says so, not annually |
| `fct_retail_order_line` | `(invoice, line_number)` | the warehouse's finest grain, and the first below a country |

> The convention is that `(country_iso3, year)` is the **dominant** grain, not a
> house rule. Reaching for `dim_country_year` when the thing you are modelling
> is not a country-year is how you get a fact with a fabricated dimension bolted
> onto it. CLAUDE.md's sentence used to say "every model" and stopped being true
> twice.

## 2. The spine

`marts.dim_country_year` is `stg_country` × every year any source covers:

```sql
-- dbt/models/marts/dim_country_year.sql, abridged
bounds as (select min(year) as first_year, max(year) as last_year from (…)),
years  as (select unnest(range(first_year, last_year + 1)) as year from bounds)
select c.country_iso3, cast(y.year as integer) as year, c.country_name, …
from country as c
cross join years as y
```

Two decisions in nine lines. The **bounds are read from the data**, not
hardcoded: OWID CO2 reaches back to 1750 and the World Bank publishes the
current year, and both ends move. And the **dimension is authoritative for what
a country is**: a code `stg_country` does not carry cannot reach the mart, which
is how the World Bank's aggregates (`WLD`, `EUU`) and Antarctica stay out.

`fct_emissions_energy` then hangs off it, not off any source:

```sql
from spine as s
inner join observed as o on s.country_iso3 = o.country_iso3 and s.year = o.year
left  join co2      as c on s.country_iso3 = c.country_iso3 and s.year = c.year
left  join energy   as e on …
left  join wdi      as w on …
left  join eu_prices as p on …
```

`observed` is the union of country-years *any* source reports. Read the shape
carefully, because both halves are load-bearing:

- The **inner join to `observed`** is what stops the fact carrying an all-null
  row for (Kosovo, 1750) and ~20,000 of its friends. The spine is a full cross
  join; the fact is not.
- The **left joins to each source** are what let a country-year that only *one*
  source reports still reach the mart. In the real warehouse, 14 countries —
  Puerto Rico, Kosovo, Monaco, Gibraltar, the Channel Islands — have **no OWID
  emissions in any year**, and 13 of those have World Bank data. Off a source,
  they do not exist. Off the spine, they are rows with nulls in the emissions
  columns.

Nulls are therefore the *normal case* here, not a defect. A chart query filters
for the columns it needs.

---

## 🔧 Drill 1 — the fact that quietly lost two thirds of its countries

**Symptom.** A colleague simplifies the join block in
`dbt/models/marts/fct_emissions_energy_v2.sql`, reasoning that the wide fact is
about emissions, so a row with no emissions is not worth carrying. The build is
green. Nobody notices for a month, until the EU electricity dashboard is missing
most of Europe.

**Seed the bug.**

```bash
sed -i 's|^left join co2 as c on|inner join co2 as c on|' \
  dbt/models/marts/fct_emissions_energy_v2.sql
just course-rebuild
```

**Observe.** The build finishes on `PASS=402 WARN=0 ERROR=0 SKIP=0`: the same
verdict, to the row, as the healthy build. All 369 data tests pass. Both grain
contracts still hold, because the grain *is* still unique; the model simply has
fewer rows in it.

**Your task.** Without reading the reveal:

1. Find one query that demonstrates the fact is wrong, using only tables in the
   warehouse: no access to the old build, no `git diff`. This is the real
   constraint: in production you find these by internal inconsistency, because
   the previous version has already been overwritten.
2. Explain why no test caught it, and name the test you would add.

**Verification.** When you think it is fixed:

```bash
git checkout dbt/models/marts/fct_emissions_energy_v2.sql
just course-rebuild
just course-query "
select
  (select count(*) from marts.fct_emissions_energy)                    as mart_rows,
  (select count(distinct country_iso3) from marts.fct_emissions_energy) as countries,
  (select count(*) from marts.fct_emissions_energy
     where electricity_price_eur_kwh is not null)                       as price_rows"
```

Healthy: `4096`, `52`, `701`.

<details>
<summary>Reveal</summary>

**The measurement.**

| | healthy | bugged |
|---|---|---|
| mart rows | 4,096 | 3,487 (−15%) |
| distinct countries | **52** | **17** (−67%) |
| rows carrying an EU price | 701 | 104 (−85%) |
| `dbt build` | `PASS=402 ERROR=0` | `PASS=402 ERROR=0` |

Row count fell 15% and country count fell 67%, which is the tell: the loss is not
spread evenly, it is *whole countries*. The 17 survivors are exactly the CO2
fixture slice.

**The query that finds it.** Two marts in the same build disagree about which
countries exist:

```sql
select
  (select count(distinct country_iso3) from marts.fct_emissions_energy)              as wide_fact,
  (select count(distinct country_iso3) from marts.fct_eu_electricity_prices_semiannual) as price_mart;
-- healthy: 52, 41      bugged: 17, 41
```

`fct_eu_electricity_prices_semiannual` is built off Eurostat directly and still
has all 41 countries. The wide fact claims 17. Both were built by the same
`dbt build`, from the same sources, seconds apart. One of them is lying.

The general form, and the one worth keeping, is: **left-join the spine to the
fact and count the gaps.** That is what `dim_country_year` is *for*.

```sql
select count(*) as country_years_the_fact_should_carry
from marts.dim_country_year        as s
join staging.stg_eu_electricity_prices as p
  on s.country_iso3 = p.country_iso3 and s.year = p.year
left join marts.fct_emissions_energy   as f
  on s.country_iso3 = f.country_iso3 and s.year = f.year
where f.country_iso3 is null;
-- healthy: 0      bugged: 597
```

**Why nothing caught it.** Every test in the project is an assertion about the
rows that are *present*: `not_null`, `accepted_range`,
`unique_combination_of_columns`, `relationships`. Deleting rows makes all four
*more* likely to pass. There is no test whose failure mode is absence, because
absence has no row to store in `dbt_test__audit`.

**The test to add** is a `relationships`-shaped assertion in the other
direction: every `(country_iso3, year)` in `stg_eu_electricity_prices` must
appear in `fct_emissions_energy`. `dbt_utils.equal_rowcount` is the cheap cousin
of this and is already used on `fct_fx_rates_published`, precisely because that
model is a faithful copy of a view and so a row count *can* be compared.

The deeper lesson: an `inner join` is a filter wearing a join's clothes. In a
mart built on a spine, every join to a source should be `left` and the only
`inner` should be the one to `observed`, which is deliberate and commented.
</details>

---

## 🔍 Investigate 1 — "the latest year" is not a year

> Run this against the **real** warehouse (`data/warehouse.duckdb`), not the
> sandbox. Coverage is the entire point and the sandbox has 17 countries.

You are asked for a cross-section: grid carbon intensity by country, latest
available year. The obvious query:

```sql
select country_iso3, carbon_intensity_elec_g_kwh
from marts.fct_emissions_energy
where year = (select max(year) from marts.fct_emissions_energy);
```

**Questions.**

1. How many countries does that return? How many would 2023 return?
2. `max(year)` on this mart, which source is it actually reporting?
3. `primary_energy_twh` and `low_carbon_share_elec_pct` are both "energy"
   columns from the same publisher, in the same model. Do they have the same
   latest year? The same coverage at that year?
4. What should the query have been?

```bash
uv run python -c "
import duckdb; c = duckdb.connect('data/warehouse.duckdb', read_only=True)
print(c.sql('''
select column_name from information_schema.columns
where table_schema=\"marts\" and table_name=\"fct_emissions_energy\"'''))"
```

<details>
<summary>Reveal</summary>

| column | latest year with data | countries at that year | countries at 2023 |
|---|---|---|---|
| `co2_mt` | 2024 | 214 | 214 |
| `consumption_co2` | 2023 | 120 | 120 |
| `primary_energy_twh` | 2024 | **79** | 210 |
| `renewables_share_pct` | 2024 | 79 | 79 |
| `carbon_intensity_elec_g_kwh` | 2025 | **90** | 205 |
| `low_carbon_share_elec_pct` | 2025 | 90 | 205 |
| `gdp_constant_usd` | 2025 | 186 | 203 |
| `electricity_price_eur_kwh` | 2025 | 39 | 39 |
| `life_expectancy` | 2024 | 217 | 217 |

1. **90 countries, against 205 at 2023.** The "latest year" cross-section drops
   115 countries (56% of the sample) and every one of them silently. The
   result is not wrong-looking; it is a perfectly plausible 90-row table.
2. `max(year)` is **2025**, which is Eurostat's electricity price and the World
   Bank, both running ahead of OWID. Cutting an *energy* chart to the mart's
   `max(year)` is cutting it to a year defined by a completely different source.
3. No. `primary_energy_twh` stops in 2024 and `low_carbon_share_elec_pct`
   reaches 2025, and their coverage differs by a factor of 2.6 at 2023 (79
   against 205), because OWID's broad-coverage energy series is the
   **electricity** mix, not the primary-energy mix. They answer different
   questions and are not interchangeable in levels.
4. Latest year *per metric family*, each with its own coverage floor. That is
   exactly what `reports/sources/warehouse/latest_years.sql` computes, and why
   no Evidence page hardcodes a year literal. Add a family there before charting
   a column whose coverage curve you have not looked at.

The same shape recurs across the warehouse with different clothes on:
`dim_grid_emission_factors.is_latest_available` is a **filter, not a year**,
because the most recent published factor resolves to 2025 for 90 countries, 2024
for 105, 2023 for 10 and 2022 for 2, so `where year = 2025` drops more than half
the world from a number with a legal consequence attached.
</details>

---

## 💬 Design defence

Write an answer before opening each reveal.

**(a)** Every country-shaped model here is built on `dim_country_year`. **`dim_grid_emission_factors` deliberately is not.** Defend that.

<details>
<summary>Reveal</summary>

Because a country-year with no published emission factor is an **absence, not a
reference value**, and this table is a product: a reporter multiplies its number
by metered kWh for a CSRD or CDP filing. On the spine, every country would get a
row for every year, and a row with a null factor in a reference table invites
someone to carry the previous year forward or read the null as zero.

`dim_country_year` is where absences are meant to be rows: that is its job, and
you left-join to it when you want to *see* the gaps. A product table is the one
place you do not want that. Five OWID territories (Guadeloupe, Martinique,
Réunion, French Guiana, the Falklands) consequently carry no factor at all, and
the Scope 2 page states the count rather than leaving it unexplained.
</details>

**(b)** `fct_cbam_exposure` has **no year in its grain**, in a warehouse whose
dominant grain is `(country, year)`. It would be trivial to add one. Why is that
wrong?

<details>
<summary>Reveal</summary>

Because the source is a legal instrument, not a measurement. Annex I of the
implementing regulation is a **schedule**: it states a default value per
(country, good) and it changes when an amending regulation says so: IR
2026/1740 replaced it in full in July 2026, retroactive to 1 January.

Adding a year would mean inventing one, and an invented dimension is worse than
a missing one because it looks queryable. Someone would group by it, plot a
trend, and describe regulatory drafting as if it were an emissions trend. The
mark-up *schedule* (10/20/30% by year, 1% for fertilisers) is the part that is
genuinely time-varying, and it lives in its own seed
(`dbt/seeds/cbam_markup_schedule.csv`) where it can be reviewed as data.

Note the shape of the argument: the grain is not chosen for convenience or
consistency, it is read off what the source actually asserts.
</details>

**(c)** The spine is 62,928 rows and the fact it feeds is 43,138: the fact is
**smaller** than its own dimension. Why is that not a bug, and what would it mean
if the fact were ever *larger*?

<details>
<summary>Reveal</summary>

Smaller is expected: the spine is a full cross join of 228 countries × 276 years
(1750–2025), and the fact inner-joins it to `observed`, so the ~19,790 country-years
no source reports at all — (Kosovo, 1750) and its friends — never become rows.
The gap is the answer to "what don't we have", which is why the spine is
materialised rather than inlined.

Larger would mean the grain contract had broken: a join to a source fanned out,
producing two rows for one `(country_iso3, year)`. That failure *is* caught, and
loudly: `unique_combination_of_columns` on the mart fails the build. Note the
asymmetry with Drill 1: **fan-out fails loudly, row loss fails silently**, and
the tests in this project are much better at the first than the second.
</details>

---

## What to carry forward

- Write the grain down as a sentence, then make a test assert it.
- A fact hangs off a **dimension**, not off whichever source felt primary.
- In a spine-built model, `inner join` is a filter. There should be exactly one,
  and it should be commented.
- Your tests probably cannot see missing rows. Absence has no row to store.
- "The latest year" is a property of a **column**, not of a table.

← [00 — Setup](./00-setup.md) · [Course index](./README.md) · next: [02 — Loading twice](./02-loading-twice.md)
