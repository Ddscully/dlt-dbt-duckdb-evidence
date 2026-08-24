# 02 — Loading twice

← [01 — Grain is the contract](./01-grain.md) · [Course index](./README.md) · next: [03 — Tests that fail on bugs](./03-tests.md)

**Objectives.** Say what a second run of a pipeline does to the rows the first
one landed. Choose between `replace` and `merge`, and pick a merge key that
cannot silently collapse rows. Explain why two lookback windows in the same repo
are five *years* and ten *days*.

**Prerequisites.** A built sandbox ([00](./00-setup.md)), and module
[01](./01-grain.md) — a merge key is a grain claim about the landing table.

---

## 1. The only question that matters about a load

A pipeline that runs once is a script. This one runs on a schedule, and the whole
of ingestion design is the answer to: **what does the second run do to the rows
the first one landed?**

There are three answers, and this repo uses two of them:

| disposition | second run | safe to re-run? |
|---|---|---|
| `append` | adds every row again | **no** — a re-fetched window duplicates |
| `replace` | drops the table, reloads it | yes, but you must fetch everything |
| `merge` | rows matching the key replace their old versions | yes, and you may fetch a window |

`ingest/pipeline.py` splits seven resources across the last two:

```python
FULL_REFRESH_RESOURCES = ("owid_co2", "owid_energy", "wb_country", "eu_elec_prices")
INCREMENTAL_RESOURCES  = ("wb_wdi", "ecb_fx_rates", "retail_invoice_lines")
```

The four on the left are whole-file downloads — there is no window to ask for, so
`replace` costs nothing and buys the strongest guarantee available: **the table is
exactly what the publisher just served.** A country-year the source withdraws
disappears, which is correct and which `merge` cannot do.

## 2. Why that is two loads and not one

```python
for names, kwargs in (
    (FULL_REFRESH_RESOURCES, {"refresh": REFRESH}),   # REFRESH = "drop_resources"
    (INCREMENTAL_RESOURCES, {}),
):
```

`refresh` is a property of a **run**, not of a resource. dlt persists a schema per
resource and only ever *widens* it, so a column that lands with the wrong type
stays wrong forever unless the schema is dropped and re-inferred — hence
`drop_resources` on the replace group.

Apply that same argument to `wb_wdi` and you destroy the thing that makes it
incremental: dropping the resource takes its table **and its watermark** with it,
which is a full reload wearing an incremental costume. So the dispositions load in
two calls. It is `drop_resources` rather than `drop_sources` for a second reason —
Dagster can materialise a subset of the source, and `drop_sources` would wipe the
four tables that were not selected.

## 3. The merge key is a grain claim

```python
WDI_PRIMARY_KEY = ("indicator", "country_code", "year")
FX_PRIMARY_KEY  = ("rate_date", "quote_currency")
```

A merge key says *these columns identify a row*. Get it too wide and re-runs
duplicate; get it too narrow and **rows silently eat each other**, which is
drill 1.

Two details in `WDI_PRIMARY_KEY` are the kind of thing that only shows up once:

- It is `country_code`, the World Bank's own two-letter key — **not**
  `country_iso3`, which every other model in this warehouse joins on. The API
  leaves `countryiso3code` empty on its aggregate series, so five of them would
  share `(indicator, '', year)`. Investigate 1 measures what that costs.
- The key columns are declared `nullable: False` in `WDI_COLUMNS`, so a null key
  **fails the load** instead of escaping the merge predicate. `null = null` is
  never true, so a null-keyed row matches nothing and duplicates on every single
  run — a table that grows forever with no error.

## 4. Watermarks and lookback are two different numbers

A watermark records how far a load got. A lookback window says how far back the
*next* load re-asks anyway. The gap between them is where restatements live.

```python
def wdi_start_year(last_loaded_year):
    if last_loaded_year is None:
        return None                                   # the whole series
    return last_loaded_year - WDI_LOOKBACK_YEARS + 1  # 5 years back from the mark
```

Fetching only what is *newer* than the watermark would freeze stale revisions
into the warehouse forever, because **the World Bank restates published years as
routine practice.** The ECB does not restate a fixing — so the same mechanism is
tuned to ten days there, and the two numbers are not a copy-paste failure:

| | `wb_wdi` | `ecb_fx_rates` |
|---|---|---|
| lookback | `WDI_LOOKBACK_YEARS = 5` | `FX_LOOKBACK_DAYS = 10` |
| watermark | **one per indicator** | **one for the table** |
| why | adding an indicator adds a request never made before | every currency arrives in the *same* request |

The FX watermark can be table-wide precisely because a newly listed currency is
already covered by it. WDI cannot, because a new indicator code has no history
in the destination and a table-wide mark would claim it did.

> There is a second FX lookback one layer down — `fx_incremental_lookback_days`
> (30) in the dbt model — and it must be **no smaller** than `FX_LOOKBACK_DAYS`
> (10). Two constants that must be *equal* are a drift bug waiting to happen; one
> that must only be *no smaller* costs a few redundant rows a run and cannot fail
> in the direction that loses data. Prefer the second shape whenever you can get
> it.

---

## 🔧 Drill 1 — the merge key that ate ten indicators

**Symptom.** Someone simplifies `WDI_PRIMARY_KEY`, reasoning that a row is
identified by its country and year — which is, after all, the grain of every
model downstream. The build is green. Every table has the row count it had
yesterday.

**Seed the bug.**

```bash
sed -i 's|^WDI_PRIMARY_KEY = ("indicator", "country_code", "year")|WDI_PRIMARY_KEY = ("country_code", "year")|' \
  ingest/pipeline.py
just course-sandbox     # a merge key change needs a re-ingest, not just a rebuild
```

**Observe.** `PASS=402 WARN=0 ERROR=0 SKIP=0` — again identical to healthy. Then:

```bash
just course-query 'select count(*) from staging.stg_wdi'
```

**576 rows. Exactly what it was before.**

**Your task.**

1. The staging row count is unchanged and the build is green. Find the damage.
2. Explain why `stg_wdi`'s row count could not possibly have moved, whatever the
   merge key did. (Read `dbt/models/staging/stg_wdi.sql`.)
3. Name the cheapest test that would have failed.

**Verification.**

```bash
git checkout ingest/pipeline.py
just course-sandbox
just course-query "
select count(*) as rows, count(distinct indicator) as indicators from raw.wb_wdi"
```

Healthy: `6336`, `11`.

<details>
<summary>Reveal</summary>

**The measurement.**

| | healthy | bugged |
|---|---|---|
| `raw.wb_wdi` rows | 6,336 | **576** (−91%) |
| distinct indicators landed | **11** | **1** |
| `staging.stg_wdi` rows | 576 | **576** (unchanged) |
| `stg_wdi.gdp_per_capita_usd` non-null | 576 | 576 |
| `stg_wdi.gdp_usd` non-null | 576 | **0** |
| `stg_wdi.life_expectancy` non-null | 560 | **0** |
| `stg_wdi.population` non-null | 576 | **0** |
| `dbt build` | `PASS=402 ERROR=0` | `PASS=402 ERROR=0` |

Ten of the eleven indicators were destroyed at the landing table. The only
survivor is `NY.GDP.PCAP.CD` — the first key in `WB_WDI_INDICATORS`, which won
the collision by arriving first.

**Why the row count could not move.** `stg_wdi` pivots the long table to wide
with `max(case when indicator = … then value end)`, one `case` per column. A
pivot's output grain is `(country_iso3, year)` **regardless of how many indicator
rows feed it** — so destroying 91% of the input changes no row count anywhere
downstream. It empties columns instead.

This is the most dangerous shape in the module: the sanity check everyone
actually runs — *did the row count change?* — is structurally blind to it. Ten
columns went to 100% null and the table is exactly the same height.

**Finding it.** Compare what landed against what was *asked for*, which is the
one thing the warehouse cannot infer for itself:

```sql
select count(distinct indicator) as indicators_landed from raw.wb_wdi;
-- healthy: 11      bugged: 1
```

Or from the shape of the damage — whole columns at zero, which is never how real
sparsity looks:

```sql
select count(*) as rows, count(gdp_usd) as gdp, count(population) as pop
from staging.stg_wdi;
-- healthy: 576, 576, 576      bugged: 576, 0, 0
```

**The cheapest test.** A `not_null` on `stg_wdi.population` would have failed
instantly. More generally: a pivot deserves a test per pivoted column, because
the pivot converts row loss into column emptiness, and *column* emptiness is
something `not_null` can see. Module 01's punchline was that tests cannot see
missing rows — a pivot is the transformation that hands you a second chance.

`WB_WDI_INDICATORS` is also the natural source of truth for a
`relationships`-style assertion: eleven codes go in, eleven must land.
</details>

---

## 🔧 Drill 2 — the truncated read, at two scales

**Symptom.** `raw.retail_invoice_lines` lands `1000000` rows. The workbook has
1,067,371. Nothing errored.

This one really happened here, and what caught it was **a human noticing a round
number** — not a test. The drill stages the same mistake at fixture scale so you
can watch the difference.

**Seed the bug.**

```bash
sed -i 's|    yield from con.sql(retail_sql(months)).to_arrow_reader(RETAIL_BATCH_ROWS)|    yield next(con.sql(retail_sql(months)).arrow(10_000))|' \
  ingest/pipeline.py
just course-sandbox
```

`DuckDBPyRelation.arrow()` returns a streaming `RecordBatchReader`, not a table.
A caller who treats it as one gets **the first batch and no warning**.

**Your task.** This time the build is *not* green — `ERROR=1`. Before reading on:

1. Which test failed, and what is it actually pinning?
2. That test exists because of a decision made in `scripts/record_fixtures.py`.
   Which one?
3. **The important question:** would that same test have caught the real
   1,000,000-row truncation in production? Justify it with a query against the
   real warehouse, not a guess.

**Verification.**

```bash
git checkout ingest/pipeline.py
just course-sandbox
just course-query 'select count(*) from marts.fct_retail_order_line'
```

Healthy: `41089`.

<details>
<summary>Reveal</summary>

**1. What failed.**

```
Failure in test stg_retail_has_exactly_one_positive_cancellation_line
  Got 10000 results, configured to fail if != 0
```

The file contains exactly **one** cancellation line with a positive quantity —
`C496350`, quantity 1, £373.57. The test asserts that oddity is still there. It
is an `expression_is_true` over the whole model, which is why a violation returns
all 10,000 rows rather than one.

**2. Why it exists.** The fixture is **selected by shape, not sampled**. A 4%
random draw keeps the volume and loses all six `A` bad-debt adjustments and this
single positive `C` line — the rows that make the taxonomy in staging worth
having. `RETAIL_FIXTURE_SELECTION` picks each shape explicitly, and tests pin
that they survived.

**3. In production, this test would have passed.** Measure where the pinned row
sits in file order:

```sql
select
  (select count(*) from raw.retail_invoice_lines
     where sheet_name = 'Year 2009-2010' and invoice_ts <= timestamp '2010-02-01 08:24:00')
    as rows_before_it,
  (select count(*) from raw.retail_invoice_lines) as total_rows;
-- 76800, 1067371
```

The row is at position **~76,800 of 1,067,371 — 7% into the file**, in the first
of two sheets. A 1,000,000-row truncation drops the last **67,371** rows, i.e.
the final 6.3%. The pinned shape is nowhere near the boundary, so the test sails
through while 67,371 rows are missing.

**The lesson, and it is the module's most transferable one:** a shape test
protects the shape it names, not the *quantity* of data. Its power depends
entirely on where the missing rows fall relative to what it pins — and at fixture
scale (10,000 of 41,089) the boundary swept up the pinned row, while at
production scale it did not. A check that fires in CI and not in production is
worse than no check, because it is *believed*.

What actually protects this is a count against an independent source of truth:
`tests/test_ingest.py` counts the resource's rows against the workbook's own.
That is the only assertion in the module that a truncation cannot satisfy.

Note the family resemblance to module 00's warning about the 17-country fixture
slice. Same failure, opposite direction: there, a threshold the slice passes and
production breaks; here, a test the slice fails and production passes.
</details>

---

## 🔍 Investigate 1 — the merge key that is not ISO3

> Real warehouse (`data/warehouse.duckdb`), not the sandbox.

Every model in this warehouse joins on `country_iso3`. `WDI_PRIMARY_KEY` uses
`country_code` instead. That looks like an inconsistency worth tidying up.

**Questions.**

1. How many `raw.wb_wdi` rows have a blank or null `country_iso3`? How many
   distinct `country_code`s do they represent?
2. If the merge key were `(indicator, country_iso3, year)`, how many rows would
   survive out of those, and how many would be lost?
3. Would you notice? Which model in this warehouse would change, and by how much?
4. A full WDI reload is how many rows? What fraction does the 5-year window
   actually fetch? Is that ratio an argument for or against a *longer* window?

```bash
uv run python -c "
import duckdb; c = duckdb.connect('data/warehouse.duckdb', read_only=True)
print(c.sql('select * from raw.wb_wdi limit 5'))"
```

<details>
<summary>Reveal</summary>

**1.** 3,630 rows, across **5** distinct `country_code`s — `XD`, `XM`, `XT`, `XY`
and `XN`. These are the World Bank's aggregate series (income groups and
regional rollups), and the API simply leaves `countryiso3code` empty for them.
726 rows each, which is 11 indicators × 66 years.

**2.** All five collapse onto `(indicator, '', year)`. **726 rows survive, 2,904
are lost** — one aggregate wins each key and the other four are merged away.

**3. You would almost certainly not notice**, and that is the point. Nothing
downstream reads those rows: `stg_wdi` joins to `stg_country`, which is
authoritative for what a country is and carries none of the `X…` aggregates —
module 01's spine, doing its job. So the loss is invisible in every mart, and it
would sit in `raw` waiting for the first person to ask an income-group question
directly of the landing table.

That is the argument for keying on the publisher's own identifier rather than on
the one *your* models happen to prefer. The merge key belongs to the **source**;
it is a statement about what the API considers a distinct row. Re-keying it to
your join column silently imposes your model's worldview on the landing table,
which is exactly the layer that is supposed to be free of it.

**4.** `raw.wb_wdi` is **192,390 rows**. Years 2021 onward — the 5-year window —
are **14,575**, or **7.6%**. So the window turns a ~190k-row pull into a ~15k-row
one, a 13× saving on every scheduled run.

Read that ratio carefully, because the naive conclusion is backwards. It is
*cheap* to widen: going from 5 years to 10 would still fetch well under a fifth
of the series. The window is not 5 because 10 would be expensive — it is 5
because that is the horizon over which the World Bank's revisions actually move,
and a wider window buys re-fetched rows that were never going to change. The cost
argument and the correctness argument point the same way here, which is luck. An
older restatement is handled explicitly instead, with `just ingest-wdi-full` or
`just backfill-wdi 1997`.
</details>

---

## 💬 Design defence

**(a)** dlt keys its pipeline state on the **pipeline name**, not on the
destination — so `build_pipeline()` appends `_fixtures` to the name when
`INGEST_FIXTURES=1`. Explain the exact failure that prevents, and why deleting
the warehouse is not a sufficient guard.

<details>
<summary>Reveal</summary>

State lives in `~/.local/share/dlt/pipelines/<name>/`, outside the warehouse
entirely. Without the suffix, a fixture run's WDI watermark is handed to the next
**real** run — which then asks for a five-year window on the assumption that
sixty years of history it never loaded are already there. You get a warehouse
holding 2021-2025 and a watermark asserting 1960-2025 is complete.

Deleting the warehouse does not guard it in general, though it appears to: dlt
resets state when the destination is *empty*, so the failure hides during
development and appears the moment someone runs fixtures against a warehouse that
already has real data in it — a laptop, never CI.

This is a **cache-key bug**, and the third instance of the same family in this
repo. The others: the retail workbook cache keyed on the directory rather than
the archive's sha256, so a re-record looked like a no-op; and Evidence's schema
cache keyed on the source rather than the query text. A cache whose key omits
something the value depends on will always fail this way — quietly, and only in
the configuration nobody tests.
</details>

**(b)** `replace` gives a guarantee `merge` cannot: the table is exactly what the
publisher just served. Given that, defend using `merge` for `wb_wdi` at all —
and state precisely what you gave up.

<details>
<summary>Reveal</summary>

You use `merge` when the fetch can be **narrowed** and the full fetch is
expensive enough to matter: 192,390 rows every run against 14,575. Merge is what
makes the partial fetch *safe* — re-fetched rows replace their previous versions
rather than appending a second copy, so a run that asks for five years leaves
1960 onward intact.

What you give up is deletion. **A country-year the World Bank withdraws stays in
`raw.wb_wdi` until a full reload**, because merge has no way to express "this key
is gone" — nothing arrives to overwrite it. `replace` expresses it for free by
dropping the table.

So the honest description is not "merge is the incremental one" but *"merge
trades the ability to observe deletions for the ability to fetch a window"*. That
trade is right for WDI (withdrawals are rare, restatements are routine) and wrong
for the four whole-file sources, where the window does not exist and the trade
would be pure loss. `just ingest-wdi-full` is the escape hatch that buys the
guarantee back on demand.
</details>

**(c)** `ecb_fx_rates` merges, and its API takes a date range — the same two
properties that earn `wb_wdi` a Dagster partition. It is deliberately **not**
partitioned. What is the actual rule, then?

<details>
<summary>Reveal</summary>

The rule is not "the API takes a range" and never was "the disposition is merge".
It is whether **a partition is a re-runnable unit of *work* that maps cleanly
onto a slice of the destination**, and whether that unit is big enough to be
worth having.

The ECB's entire 27-year series is one three-second request. Partitioning it
daily would create roughly 7,000 Dagster partitions to stand in for a single
request — all the bookkeeping and none of the benefit.

The third resource is what proves the rule, because it breaks both of the naive
tests: `retail_invoice_lines` has **no request to narrow at all** — the source is
one static 45 MB workbook — and it is still partitioned by month. What narrows
there is the *load*: reading and converting a month is real work, the cached
download means twenty-five partitions are still one fetch, and `invoice_month` is
derived from the same timestamp the partition key uses, so re-running one month
replaces exactly that month.

Hence `PARTITIONED_RESOURCES` is its own constant rather than being derived from
`INCREMENTAL_RESOURCES`. It was derived once, and adding a second merge resource
would have silently given it yearly partitions.
</details>

---

## What to carry forward

- Design the second run, not the first. `append` is almost never the answer.
- A merge key is a grain claim about the **landing** table, and it belongs to the
  publisher — key on their identifier, not on your join column.
- Declare key columns non-nullable. A null key matches nothing and duplicates
  forever, with no error.
- A **pivot converts row loss into column emptiness**, which is the one form of
  data loss `not_null` can actually see. Use it.
- A watermark and a lookback window are different numbers, and the window is set
  by how the publisher **restates**, not by what is cheap.
- Two constants that must be equal will drift. Two where one must merely be *no
  smaller* cannot fail in the direction that loses data.
- A shape test protects the shape, not the quantity. Row counts need an
  independent source of truth.

← [01 — Grain is the contract](./01-grain.md) · [Course index](./README.md) · next: [03 — Tests that fail on bugs](./03-tests.md)
