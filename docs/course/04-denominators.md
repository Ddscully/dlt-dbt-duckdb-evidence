# 04 — Denominators, units and coverage

← [03 — Tests that fail on bugs, not on reality](./03-tests.md) · [Course index](./README.md) · next: 05 — State vs build artifacts

**Objectives.** Read a ratio as two independent decisions and say what each one
commits you to. Recognise the three conversions that change a story rather than
restating it — a currency, a price basis, a unit — and know which of them any
test in this repo can see. Answer "what is the latest year?" per column instead
of per table, and know what a `max(year)` cross-section silently deletes.

**Prerequisites.** A built sandbox ([00](./00-setup.md)) and the real warehouse
(`just run`), because two of the exercises need more than 17 countries. Module
[03](./03-tests.md) for why a one-sided bound cannot see a scaling error.

---

## 1. A ratio is two decisions, and only one of them gets reviewed

Every derived number in this warehouse is a numerator over a denominator, and
the reviewing attention goes almost entirely to the numerator. `co2_mt` is
audited, cross-checked against consumption-based emissions, snapshotted for
restatements. The thing it is divided by gets a column name.

Look at what the denominators here actually are:

| Metric | Numerator | Denominator | The decision hiding in it |
|---|---|---|---|
| `co2_per_capita` | tonnes CO2 | population | whose population — resident, registered, present? |
| `analytics.co2_intensity.co2_per_gdp_const_usd` | kg CO2 | **constant 2015 US$** | current or constant, and which base year |
| `fct_emissions_energy.co2_kg_per_gdp_ppp_2011` | kg CO2 | 2011 international-$ (PPP) | market rates or purchasing power |
| `renewables_share_pct` | renewable energy | **primary energy** | energy, or only electricity |
| `low_carbon_share_elec_pct` | low-carbon generation | **electricity** | renewable, or low-carbon |
| `trade_co2_share` | traded CO2 | territorial CO2 | a denominator that can be smaller than its numerator |

The last four are the same trap in four costumes: two columns whose names are
near-synonyms, whose denominators are not, and which are therefore not
interchangeable in levels. France, 2023:

| | `renewables_share_pct` | `low_carbon_share_elec_pct` |
|---|---|---|
| France | **15.9%** | **92.1%** |
| Brazil | 49.0% | 91.0% |
| United States | 11.2% | 40.9% |
| South Africa | 3.8% | 15.9% |

France's nuclear fleet is not renewable and is very much low-carbon, and the
denominator changes from all energy to electricity alone. Across every
country-year where both exist since 2015 they average **14.5% and 40.1%**: a
Pearson *r* of 0.80, which is high enough to look like the same column on a
scatter and is nowhere near high enough to substitute one for the other.

> The rule: **name the denominator in the column name whenever it is a choice.**
> `co2_kg_per_gdp_ppp_2011` is a mouthful and tells you the unit, the basis and
> the base year. It was called `co2_per_gdp` until 2026, which is the whole
> reason `fct_emissions_energy` is the one versioned model here
> ([`docs/DATA_QUALITY.md`](../DATA_QUALITY.md), and module 06).

## 2. Current or constant, and what it costs to get wrong

`gdp_usd` is `NY.GDP.MKTP.CD`: current US dollars, i.e. this year's prices at
this year's exchange rate. `gdp_constant_usd` is `NY.GDP.MKTP.KD`: constant
2015 US dollars. For a single-year cross-section either is defensible. Divide by
the first in anything measured **over time** and you are reporting inflation and
the currency market as if they were the thing you set out to measure.

Japan, 2010 to 2024, every figure from `marts.fct_emissions_energy` and
`marts.fct_fx_rates_periods`:

| | 2010 | 2024 | change |
|---|---|---|---|
| Emissions (`co2_mt`) | 1,211.1 | 961.9 | **−20.6%** |
| Real GDP (`gdp_constant_usd`) | $4.266tn | $4.708tn | +10.4% |
| Current-$ GDP (`gdp_usd`) | $5.812tn | $4.190tn | **−27.9%** |
| kg CO2 per constant $ | 0.2839 | 0.2043 | **−28.0%** |
| kg CO2 per current $ | 0.2084 | 0.2296 | **+10.2%** |

Japan cut a fifth of its emissions and, on the current-dollar basis, got 10%
worse. The current-dollar GDP fall decomposes cleanly, and the currency is most
of it:

```
0.721  =  1.104          ×  0.579              ×  1.128
current-$    real growth     dollar value of       residual
GDP                          the yen               (domestic prices)
```

The yen averaged **87.7 per dollar in 2010 and 151.4 in 2024**: it lost 42% of
its dollar value, and that alone is bigger than every real thing Japan did.

This is not one country's oddity. Take the 193 countries with both GDP series in
both years and compute the 14-year change in carbon intensity twice:

- **127 improved** on constant dollars; **147** improved on current dollars.
- **30 of the 193 flip the sign of their decarbonisation trend** depending only
  on the denominator. Five go from improving to worsening:

| | emissions change | intensity, constant $ | intensity, current $ |
|---|---|---|---|
| Nigeria | +21.2% | **−13.5%** | **+76.3%** |
| Brazil | +9.7% | −7.9% | +10.9% |
| Japan | −20.6% | −28.0% | +10.2% |
| Lesotho | +13.1% | −5.1% | +5.7% |
| Namibia | +24.4% | −10.5% | +4.3% |

Nigeria is the naira, Japan is the yen, and none of it is a fact about carbon.

And the cross-section is not immune either, which is worth knowing because
"current dollars are fine for one year" is the usual shorthand. Rank the
countries within their income group for 2024 on each basis: **166 of 194
(86%) land on a different rank**, the worst moving 26 places. The shorthand
means *internally consistent*, not *the same answer*.

## 3. "The latest year" is a property of a column

`marts.fct_emissions_energy` is built on the country-year spine, so it holds a
row for every country-year **any** source covers, with nulls where a given
source does not reach. `max(year)` on it is therefore whichever publisher runs
furthest ahead, and it is nobody's latest year:

```bash
just course-query "
select year,
       count(co2_mt)                      as co2,
       count(primary_energy_twh)          as energy,
       count(carbon_intensity_elec_g_kwh) as grid,
       count(gdp_constant_usd)            as gdp,
       count(consumption_co2)             as consumption
from marts.fct_emissions_energy where year >= 2022 group by 1 order by 1"
```

Against the real warehouse (`max(year)` = **2025**):

| year | co2_mt | primary_energy | grid intensity | gdp_constant | consumption_co2 |
|---|---|---|---|---|---|
| 2022 | 214 | 210 | 207 | 208 | 120 |
| 2023 | 214 | 210 | 205 | 203 | 120 |
| 2024 | 214 | **79** | 195 | 199 | **0** |
| 2025 | **0** | 0 | 90 | 186 | 0 |

Three separate things are visible in that table and they fail in three different
ways:

- **A cliff.** `primary_energy_twh` goes 210 → 79 in one year. A chart cut to
  "the latest year with energy data" keeps 38% of its countries and reports the
  survivors as if they were the world.
- **A cross-section that is empty.** `where year = (select max(year) …)` returns
  214 rows with `co2_mt` null in every one of them. Not an error, not zero rows:
  a full-height table of nulls.
- **A column that stops entirely.** `consumption_co2` is one year behind, always.

`marts.dim_grid_emission_factors` is the same problem where the number has a
legal consequence. "The most recent published factor" is 2025 for 90 countries,
2024 for 105, 2023 for 10 and 2022 for 2, so `where year = 2025` returns **90
of 207** and silently drops 117 countries, Ukraine's 111 TWh grid among them. The
model ships `is_latest_available` as a **boolean filter, not a year**, precisely
so the correct query cannot be written as a year literal.

The site does not hardcode a year either.
[`reports/sources/warehouse/latest_years.sql`](../../reports/sources/warehouse/latest_years.sql)
computes a latest year **per metric family**, each with its own coverage floor
(`n_co2 >= 200`, `n_energy >= 200`, `n_elec >= 150`, `n_price >= 25`). One
literal per family, derived from the data, in one file.

## 4. Spot or average — the conversion that has no wrong-looking answer

`marts.fct_fx_rates_periods` carries both `avg_units_per_eur` and
`period_end_units_per_eur` for every currency and period, and **the model
refuses to pick between them**, because the right one depends on what is being
converted:

- A **stock** — a balance, an inventory, a closing position — exists at an
  instant, so it converts at that instant's rate.
- A **flow** — revenue, spend, a price paid across a period — accumulates, so it
  converts at the period average.

`period_end_vs_avg_pct` is the column that says what choosing wrong costs:

| currency | year | average | year-end | end vs avg |
|---|---|---|---|---|
| USD | 2003 | 1.1312 | 1.2630 | **+11.7%** |
| USD | 2014 | 1.3285 | 1.2141 | **−8.6%** |
| ISK | 2008 | 146.25 | 290.00 | **+98.3%** |

Two consequences the model builds around, both of which look like nitpicks and
are not:

**Average the published fixings, never the gap-filled table.**
`fct_fx_rates_daily` carries the last fixing forward across weekends, so
averaging it counts every Friday three times and weights the mean toward
whichever weekday sits next to a closure. `fct_fx_rates_periods` reads
`fct_fx_rates_published` for that reason alone.

**`avg_eur_per_unit` is not `1 / avg_units_per_eur`.** The mean of reciprocals is
not the reciprocal of the mean, and the gap grows with volatility: 0.16% for the
dollar in 2014, 0.52% in 2008, and **11.9% for the Icelandic krona in 2008**.
Each direction is the mean of its own series. The period-*end* columns do invert
exactly, because a single point has no averaging in it.

The one place the warehouse actually spends this: Eurostat's household
electricity price is its only euro-denominated measurement.

> Across the 39 countries present in both halves, the average household price
> rose **35%** between 2021-S1 and 2022-S2 **in euros** and **13.5% in
> dollars**, because the euro fell from 1.205 to 1.014 over the same eighteen
> months.

Both numbers are correct. A chart titled "European electricity prices" with no
stated currency is reporting the exchange rate as if it were an energy market.

## 5. Units, and the error no test in this repo can see

`dim_grid_emission_factors` ships the same factor twice:
`emission_factor_g_co2_per_kwh` as published, `emission_factor_t_co2_per_mwh`
because that is the unit meter data arrives in. They differ by exactly 1000
(1306.3 and 1.306 at the maximum), which is the entire reason both columns
exist: the consumer forced to divide by 1000 themselves will eventually forget,
and this number goes into a regulatory filing.

Now look at what would catch it if they did. `fct_example_scope2_emissions`
multiplies twelve sites' MWh by the factor, and its yml gives `scope2_t_co2e`
`{min_value: 0}` and `share_of_group_pct` `{min_value: 0, max_value: 100}`.
Use the g/kWh column where the t/MWh one belongs and:

- every tonnage is 1000× too large: 232,456 tCO2e becomes 232.5 **Mt**, which
  is more than Spain emitted in 2023 (215.5 Mt), from twelve offices and
  factories;
- `min_value: 0` still passes, because the error is a scaling and the sign is
  unchanged;
- `share_of_group_pct` still passes *exactly*, because every share is a ratio of
  two numbers that both moved by the same factor.

**A units error is invisible to one-sided bounds and to every ratio computed
downstream of it.** That is module 03's ceiling argument arriving as a live gap:
the only test that could catch this is an upper bound, and there isn't one.

---

## 🔧 Drill 1 — the denominator that changes the ranking

**Symptom.** `analytics.co2_intensity` ranks countries on carbon efficiency
within their income group. Someone notices that `gdp_constant_usd` is null for a
handful of country-years where `gdp_usd` is populated, and switches the
denominator to recover them. More countries ranked, same column name, same row
count.

**Seed the bug.** In `transform/co2_intensity.py`, `build_co2_intensity` divides
by `gdp_constant_usd` twice: once in the `when` guard and once in the `then`.
Change both to `gdp_usd`:

```bash
sed -i 's/gdp_constant_usd/gdp_usd/g' transform/co2_intensity.py
just course-transform
```

**Observe.** No error, and the shape is untouched: 560 rows, 16 countries, same
as healthy. The column is still called `co2_per_gdp_const_usd`.

```bash
just course-query "
select country_iso3, year, round(co2_per_gdp_const_usd, 4) as intensity, co2_intensity_rank
from analytics.co2_intensity where country_iso3 = 'JPN' and year in (2010, 2024) order by year"
```

**Your task.**

1. Japan's rank goes 5 → 5 when healthy and 4 → 7 under the bug. Which of those
   two is the claim you would defend in a review, and what evidence in the
   sandbox alone could tell them apart?
2. The sandbox has 16 countries. Go to the **real** warehouse and compute the
   14-year change in intensity for 2010 → 2024 on both denominators. How many of
   the ~193 countries change the *sign* of their trend?
3. Name the failure that is not arithmetic at all: something is now wrong that
   no query against `analytics.co2_intensity` can reveal.
4. `gdp_constant_usd` really is null where `gdp_usd` is populated for some
   country-years. The reviewer's motivation was real. What should they have done
   instead?

**Verification.**

```bash
git checkout transform/co2_intensity.py
just course-transform
just course-query "
select count(*) as rows, count(distinct country_iso3) as countries,
       round(min(co2_per_gdp_const_usd), 4) as min_val,
       round(max(co2_per_gdp_const_usd), 4) as max_val
from analytics.co2_intensity"
```

Healthy: `560`, `16`, `0.0638`, `2.3853`. Under the bug the first two are
identical and the maximum is `6.8689`.

<details>
<summary>Reveal</summary>

**1. The healthy one, and the sandbox can tell you why.** Under the bug Japan's
intensity *rises* from 0.2084 to 0.2296 while its emissions fall 20.6%, so the
denominator shrank faster than the numerator did, and the only thing that can
shrink a country's GDP by 28% over fourteen years while its economy grows is the
exchange rate. The check that separates them needs no extra data:

```sql
select year, round(co2_mt, 1) as co2_mt,
       round(gdp_usd / 1e12, 3) as gdp_current_tn,
       round(gdp_constant_usd / 1e12, 3) as gdp_constant_tn
from marts.fct_emissions_energy where country_iso3 = 'JPN' and year in (2010, 2024);
```

Real GDP `4.266 → 4.708`; current-dollar GDP `5.812 → 4.190`. One of those is a
description of Japan and the other is a description of the yen. **When a ratio
moves against its numerator, look at the denominator before you believe the
story**: that is the whole diagnostic, and it fits in one query.

**2. Thirty of 193 flip sign.**

```sql
with p as (
  select country_iso3, country_name, year,
         co2_mt * 1e9 / gdp_constant_usd as const_basis,
         co2_mt * 1e9 / gdp_usd          as cur_basis
  from marts.fct_emissions_energy
  where year in (2010, 2024) and gdp_usd > 0 and gdp_constant_usd > 0 and co2_mt is not null
),
w as (
  select country_iso3, country_name,
         max(case when year = 2010 then const_basis end) as c10,
         max(case when year = 2024 then const_basis end) as c24,
         max(case when year = 2010 then cur_basis   end) as u10,
         max(case when year = 2024 then cur_basis   end) as u24
  from p group by 1, 2
)
select count(*) as countries,
       count(*) filter (c24 < c10) as improved_constant,
       count(*) filter (u24 < u10) as improved_current,
       count(*) filter (c24 < c10 and u24 > u10) as flipped_to_worse,
       count(*) filter (c24 > c10 and u24 < u10) as flipped_to_better
from w where c10 is not null and c24 is not null;
```

`193, 127, 147, 5, 25`. Five countries that decarbonised are reported as having
got dirtier (Nigeria, Brazil, Japan, Lesotho, Namibia) and twenty-five that did
not are reported as having improved. **The bug is not "some numbers moved": it
is that the answer to the question the table was built to ask reverses for one
country in six, and stays plausible for all of them.**

**3. The column is called `co2_per_gdp_const_usd` and no longer is.** Nothing in
the warehouse compares a column's name against the expression that produced it,
and nothing ever will: a name is a comment that ships. Every consumer
downstream (the Evidence page, the ranking, the release Parquet) now reads a
label that states a basis the data does not have, and the description in the
model's docstring says "constant 2015 US$" while the code says otherwise. This
is the same class as module 02's `stg_wdi` pivot: the structure is intact, the
meaning is not, and structural checks are blind by construction.

**4. Left join the gap, do not swap the basis.** `gdp_constant_usd` being null
where `gdp_usd` is populated is a real coverage fact about the World Bank series,
and the honest responses are: rank the countries you can rank and let the rest be
null (which is what the model does: it filters nulls out and says so), or add
the missing country-years as a documented absence. Substituting a *different
measurement* to fill a gap in this one is the fallback-row mistake from the CBAM
seed in another costume: a value that exists nowhere in the source, arrived at by
pairing two rows that were never meant to meet.
</details>

---

## 🔧 Drill 2 — the rate that is right for a balance and wrong for a price

**Symptom.** `fct_eu_electricity_prices_semiannual` converts a euro price to
dollars. A reviewer sees that the model computes with `avg_units_per_eur` while
`period_end_units_per_eur` sits right next to it unused, decides the closing rate
is the more current of the two, and switches it. The build is green and the price
levels barely move.

**Seed the bug.**

```bash
sed -i 's/\* f\.avg_units_per_eur as electricity_price_usd_kwh/* f.period_end_units_per_eur as electricity_price_usd_kwh/' \
  dbt/models/marts/fct_eu_electricity_prices_semiannual.sql
just course-rebuild
```

**Observe.** `PASS=402 WARN=0 ERROR=0 SKIP=0`: byte-identical to healthy, and
every price is still a plausible price. Across all 1,373 rows the mean *signed*
change is **+0.14%** — the errors very nearly cancel, because the closing rate is
above the average about as often as below — while the mean *absolute* change is
2.77% and the worst single row 7.5%. Hold on to that pair; it is the drill.

**Your task.**

1. The model already ships the evidence needed to catch this, on every row.
   Find the one-line query that proves the conversion is not the one the column's
   description claims: without consulting the source SQL.
2. Levels barely move. Compute the half-over-half **change** in the dollar price
   instead, under both conventions. On the real warehouse, how many of the 1,330
   half-over-half changes disagree about whether the price went *up or down*?
3. France, 2022-S2. Give the three different answers to "did French household
   electricity get more expensive?" and say which one you would put on a chart.
4. Write the dbt test that would have failed. Then say why the equivalent test on
   `dim_retail_customer.first_order_gbp <= net_revenue_gbp` was rejected as
   unshippable, and what makes this case different.

**Verification.**

```bash
git checkout dbt/models/marts/fct_eu_electricity_prices_semiannual.sql
just course-rebuild
just course-query "
select period, round(electricity_price_eur_kwh, 4) as eur, round(electricity_price_usd_kwh, 4) as usd
from marts.fct_eu_electricity_prices_semiannual
where country_iso3 = 'FRA' and period like '2022%' order by period"
```

Healthy: `0.2092 / 0.2287` and `0.2204 / 0.2234`. Under the bug: `0.2092 /
0.2173` and `0.2204 / 0.2351`.

<details>
<summary>Reveal</summary>

**1. The row carries the rate it was converted at.**

```sql
select count(*) as rows,
       count(*) filter (
         electricity_price_usd_kwh <> electricity_price_eur_kwh * usd_per_eur_period_avg
       ) as rows_not_converted_at_the_stated_rate
from marts.fct_eu_electricity_prices_semiannual;
```

`1373, 1373` under the bug and `1373, 0` when healthy. This is what
`usd_per_eur_period_avg` is *for*: the model's own comment says "a converted
figure whose rate is not visible is a figure nobody downstream can check", and
this is the check. **Ship the parameter beside the result and the result becomes
auditable from within the row**, with no access to the model, the source, or
anyone who remembers what was intended.

**2. 282 of 1,330 (21.2%) disagree about the direction**, and the worst pair
disagree by 42 percentage points.

```sql
with s as (
  select country_iso3, country_name, period, period_start_date,
         electricity_price_eur_kwh                          as eur,
         electricity_price_usd_kwh                          as usd_avg,
         electricity_price_eur_kwh * usd_per_eur_period_end as usd_end
  from marts.fct_eu_electricity_prices_semiannual
),
ch as (
  select *,
         lag(period_start_date) over w = period_start_date - interval 6 month as ok,
         100.0 * (usd_avg / lag(usd_avg) over w - 1) as d_avg,
         100.0 * (usd_end / lag(usd_end) over w - 1) as d_end
  from s window w as (partition by country_iso3 order by period_start_date)
)
select count(*) as changes,
       count(*) filter (sign(d_avg) <> sign(d_end)) as sign_disagreements
from ch where ok and d_avg is not null;
```

The distortion is nearly constant *within* a period (every country in a half
shares one exchange rate) so it cancels almost perfectly out of a level and not
at all out of a difference. **An error that is common to a period is invisible in
a cross-section and maximal in a time series**, which is exactly backwards from
where people look for it.

**3. Three answers, all arithmetically correct:**

| basis | 2022-S1 → 2022-S2 |
|---|---|
| euros, what households paid | **+5.4%** |
| dollars at the half-year average | **−2.3%** |
| dollars at the closing rate | **+8.2%** |

The euro figure is the one to chart, because the question is about French
electricity and French households pay in euros. The dollar-at-average column
exists for the one job the euro column cannot do (sitting beside dollar GDP)
and the closing-rate figure is an artefact of a rate that moved 5.2% inside the
period being asked to describe six months of purchases. The general form: **the
currency a chart is denominated in is part of its title, not part of its
formatting.**

**4. The test:**

```yaml
- dbt_utils.expression_is_true:
    arguments:
      expression: electricity_price_usd_kwh = electricity_price_eur_kwh * usd_per_eur_period_avg
```

Measured on the healthy warehouse: **0 of 1,373 rows fail on float residue.** It
is safe here and it was not safe for `first_order_gbp <= net_revenue_gbp`, and
the difference is worth having straight, because "never compare floats for
equality" is a rule that would have talked you out of a good test.

`first_order_gbp` and `net_revenue_gbp` are two independent `sum()`s over the
same doubles in different orders. Floating-point addition is not associative and
DuckDB's parallel aggregation fixes no order, so the two disagree in the last
bits on 272 rows: a float-equality test wearing an inequality
(`docs/DATA_PROTECTION.md` measures the same instability at 5,781 vs 5,785
distinct values between builds).

Here there is no aggregation. The test recomputes one IEEE-754 multiplication of
two stored doubles, and a multiplication of the same two operands is bit-exact
every time. **The rule is not "floats are inexact", it is "floats are
order-dependent under aggregation"**: reproduce a scalar expression and you get
the identical bits; re-sum a column and you do not.

The honest caveat: the test pins the *arithmetic*, not the *choice*. Rewrite the
model to convert via `avg_eur_per_unit` and it fails on a correct model. That is
an argument for a tolerance (`abs(a - b) < 1e-12`) rather than against the test.
</details>

---

## 🔍 Investigate 1 — how far does the latest year travel?

> Real warehouse (`data/warehouse.duckdb`), not the sandbox. The whole subject is
> coverage, and 17 countries cannot show it.

Every chart on the Evidence site has to answer "which year am I showing?" and
every honest answer is per column.
[`reports/sources/warehouse/latest_years.sql`](../../reports/sources/warehouse/latest_years.sql)
answers it once, with a coverage floor per metric family. This is where you check
whether those floors are the right ones.

**Questions.**

1. For each of `co2_mt`, `primary_energy_twh`, `carbon_intensity_elec_g_kwh`,
   `gdp_constant_usd` and `consumption_co2`, find the most recent year with at
   least 150 countries. How many distinct answers are there?
2. `latest_years.sql` uses floors of 200, 200, 150, 190 and 100. Take the two
   you would most want to argue with and say what the number is buying.
3. `price_year` counts only complete years (`not price_is_partial_year`). Find
   how many country-years carry that flag and when. Is it a latest-year edge case?
4. Run the naive query — `where year = (select max(year) from
   marts.fct_emissions_energy)` — and describe exactly what a chart built on it
   would render. Then do the same against `marts.dim_grid_emission_factors` with
   `where year = 2025` and compare the two failure modes.

<details>
<summary>Reveal</summary>

**1. Three distinct answers from five columns.**

```sql
select max(year) filter (n_co2 >= 150)         as co2_year,
       max(year) filter (n_energy >= 150)      as energy_year,
       max(year) filter (n_elec >= 150)        as elec_year,
       max(year) filter (n_gdp >= 150)         as gdp_year,
       max(year) filter (n_consumption >= 150) as consumption_year
from (
  select year,
         count(co2_mt) n_co2, count(primary_energy_twh) n_energy,
         count(carbon_intensity_elec_g_kwh) n_elec,
         count(gdp_constant_usd) n_gdp, count(consumption_co2) n_consumption
  from marts.fct_emissions_energy group by year
);
```

`2024, 2023, 2024, 2025, NULL`. Three distinct years from four columns, and the
fifth answer is **NULL**: `consumption_co2` never reaches 150 countries in any
year of the series, because it covers 120 at its very best. That is the answer to
the question: a floor is not a threshold you set once and reuse, and one of these
columns cannot meet a floor the others clear without noticing it is there. A
`max(year) filter (…)` that returns NULL is the honest failure; the same floor
applied as a `where` clause would have returned zero rows and looked like a bug in
the query.

**2. The two worth arguing with are `n_elec >= 150` and `n_consumption >= 100`.**

`n_elec >= 150` is the loosest floor in the file and it is buying the 2024
grid-intensity cross-section at 195 countries rather than falling back to 2023.
Set it at 200 and the elec year becomes 2023; set it at 100 and it becomes 2025
at 90 countries, which is under half the world. 150 is the number that keeps the
newest *defensible* year and rejects the newest year.

`n_consumption >= 100` looks alarmingly low next to the others and is the only
honest choice: consumption-based emissions cover 120 countries at their maximum,
so any floor above 120 makes the column permanently unavailable. **A coverage
floor is calibrated against the column's own ceiling, not against a house
standard**: the same lesson as module 03's bounds, one level up.

**3. 29 country-years across 28 countries, from 2007 to 2025.** It is emphatically
not a latest-year edge case: 23 of them are countries entering the series at its
2007 start, plus one-offs like the UK in 2020 and Iceland in 2025. Half a year of
prices averaged and presented as a year is wrong wherever it happens, and a
filter written as `year < 2025` would catch one of the twenty-nine.

**4. The two failures are not the same failure, and that is the point.**

`fct_emissions_energy` at `max(year)` = 2025 returns **214 rows in which `co2_mt`
is null in every one**. The chart renders: axes, legend, correct country list,
no bars. It looks like a rendering bug, someone reloads, and the actual cause
(that the year is one no CO2 publisher has reached) is nowhere in the picture.

`dim_grid_emission_factors` at `year = 2025` returns **90 of 207 rows, fully
populated**. The chart renders perfectly. It is a real cross-section of a real
year, it is missing 117 countries including Ukraine's 111 TWh grid, and nothing
about it looks wrong.

**The empty one is safe and the populated one is dangerous.** A query that
returns nothing gets investigated within the hour; a query that returns a
plausible subset gets published. This is why the vintage model ships a boolean:
`is_latest_available` cannot be typed as a year literal, so the wrong query is
harder to write than the right one.
</details>

---

## 💬 Design defence

**(a)** `dim_grid_emission_factors` ships the same number in two units, and
`fct_fx_rates_periods` ships every rate in both directions. Both are derivable
from the other in one operation. Defend the duplication, and then say where this
argument stops.

<details>
<summary>Reveal</summary>

The defence is about who does the arithmetic and what happens when they get it
wrong. Both tables are **products**: they ship as standalone Parquet in a public
release, to consumers who cannot be paged and whose spreadsheet nobody will
review. The g/kWh → t/MWh conversion is a division by 1000 and the failure mode
is a filing off by three orders of magnitude; the `units_per_eur` → `eur_per_unit`
conversion is a reciprocal and the failure mode is an inverted exchange rate,
which is off by a factor of 100+ for a yen or a forint. **A conversion the
consumer must perform is a conversion someone will eventually skip**, and one
extra `double` per row is the cheapest insurance in the warehouse.

Where it stops is where the second column stops being a *restatement* and starts
being a *second measurement*. `avg_eur_per_unit` looks like the reciprocal of
`avg_units_per_eur` and is not (the mean of reciprocals differs from the
reciprocal of the mean by 11.9% for the krona in 2008) so shipping both is
mandatory rather than convenient, and each has to be computed from its own
series. The opposite case is `renewables_share_pct` and
`low_carbon_share_elec_pct`: two columns, near-identical names, genuinely
different denominators. Duplicating a unit is free; duplicating a *definition*
means every consumer now has to choose, and most will choose by name.

The rule that separates them: duplicate when the second form is the same fact in
a different unit and the conversion is lossy to get wrong. Do not duplicate when
the second form answers a different question: model that, name it fully, and
make the difference the first thing the description says.
</details>

**(b)** `fct_eu_electricity_prices_semiannual` ships `usd_per_eur_period_end`
and never uses it. Argue for deleting the column. Then argue against.

<details>
<summary>Reveal</summary>

**For deletion:** it is dead weight in a contracted mart with 15 columns, it
ships in the public release, and it is a loaded gun: drill 2 is literally the
bug where someone reaches for it. A column that exists only to be not used is an
invitation, and if the model has made the decision then the decision should not
be re-offered on every row.

**Against, and this is the stronger case:** the column is what makes the decision
*visible* rather than *buried*. With only the average rate shipped, a reader who
wants to know whether spot or average was used has to open the SQL, and a reader
who wants to convert a balance rather than a flow (a receivable in euros, an
inventory at half-end) has no rate at all and will find one somewhere else, at
some other date, with no record of which. Carrying both turns "we chose the
average" from a fact about the code into a fact about the data, checkable by the
one-line query in drill 2's reveal.

The synthesis is that the column's *description* is doing the work, not the
column: "shipped and deliberately **not** used… correct for a balance and wrong
for a price". Delete the description and the argument for deletion wins
immediately. **A column that documents a decision has to say so in the place
people read**, which for a published dataset is the yml, not the SQL comment
above it.
</details>

**(c)** The warehouse models EU electricity prices at *two* grains: the
half-yearly fact and an annual average that joins the country-year spine. The
annual figure is, in the Dutch 2022 case, "a price nobody paid". Defend keeping
it, and name the guard that makes it honest.

<details>
<summary>Reveal</summary>

The annual column exists because the country-year spine is where prices meet
emissions, GDP and population, and a half-year grain cannot join a year grain
without either fabricating two rows of GDP or collapsing to one row of price.
Averaging is the smaller lie of the two, and it is confined to the one model that
needs it.

The cost is real and is stated in the yml rather than discovered: the mean
absolute half-over-half change was **19% across countries in 2022** and 13% in
2023, against 3–4% through the 2010s, and the Netherlands went €0.034/kWh in
2022-S1 to €0.142 in S2 as that year's energy-tax cuts landed in the first half:
an annual average of €0.088 that no Dutch household was ever billed.

The guard is `price_is_partial_year` / `n_half_years`, and it guards a *different*
failure: an average over one half presented as an average over two. That is 29
country-years, and `latest_years.sql` excludes them from `price_year` for exactly
this reason.

What makes the arrangement honest is that **both grains ship and the
documentation says which to use for what**: `fct_eu_electricity_prices_semiannual`
for anything about prices over time, the annual column only for joining prices to
something else on the spine. The failure this design prevents is the one where an
average is the *only* thing that exists, and nobody downstream can tell that a
choice was ever made. Averaging is not the problem; averaging silently is.
</details>

---

## What to carry forward

- A ratio is two decisions. The numerator gets reviewed and the denominator gets
  a column name, so put the basis, the unit and the base year *in* the name.
- Current dollars measure the currency market. Over time, 30 of 193 countries
  reverse the sign of their carbon-intensity trend depending on nothing but the
  denominator, and 86% of a single-year ranking moves too.
- When a ratio moves against its numerator, look at the denominator before you
  believe the story. It is one query.
- "The latest year" is a property of a column. `max(year)` on a spine-built fact
  is whichever publisher runs furthest ahead, and it is nobody's answer.
- An empty result is safe; a plausible subset is dangerous. Ship a boolean like
  `is_latest_available` so that the wrong query is harder to write than the right
  one.
- Stocks convert at the closing rate, flows at the period average, and the model
  should refuse to choose for you. Average the published fixings, never a
  carried-forward series, and never invert an average.
- Ship the parameter beside the result. A rate on the row makes the conversion
  auditable from within the row, by someone who has never seen the model.
- An error that is common to a period cancels out of a level and doubles in a
  difference, which is the opposite of where people look.
- Units errors are invisible to one-sided bounds and to every ratio downstream.
  Twelve invented offices can out-emit Spain with `PASS=402 ERROR=0`.
- "Never compare floats for equality" is really "floats are order-dependent under
  aggregation". A reproduced scalar expression is bit-exact; a re-summed column
  is not.

← [03 — Tests that fail on bugs, not on reality](./03-tests.md) · [Course index](./README.md) · next: 05 — State vs build artifacts
