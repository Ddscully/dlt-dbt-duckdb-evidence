# 0024. `query_metric` takes its groupings, filter values and coverage from the semantic layer, and a null is filled only where the column cannot be null

Status: accepted 2026-11-18

## Context

`query_metric` (`agent/metrics.py`) held retail's choices as Python constants:
the groupings (`GROUP_BY`), the filters, a filter-value query joined to
`fct_retail_order_line`, a `years` filter at `day` grain, and a coverage read
from the fact's first and last row. Any other table would have been a code
change, and the semantic layer's guard
(`tests/test_semantic_layer.py`) knew nothing of the ways a country-year table
differs from invoice lines.

A country-year semantic model over `fct_emissions_energy` and
`dim_country_income_history`, parsed in a scratch copy of `dbt/` and compiled by
the agent's own engine against the warehouse, measured what a second table
needs:

- **`fill_nulls_with: 0`**, which every retail metric has, turned 2025 and 2026
  into 0.0 Mt of CO2: the rows exist because WDI population reaches 2026, and
  their `co2_mt` is null because nobody has reported it.
- **An unmatched ratio**, `co2_mt / population`, read 4.6037 t per person for
  2023 against 4.5727 over the countries with both: Taiwan reports 267 Mt of CO2
  and has no WDI population.
- **MetricFlow joins any two metrics on the date**: CO2 with retail revenue, by
  year, gave 277 rows and revenue 0.00 in 1750.
- **A yearly table has no `day`**: the `years` filter at `day` grain was refused.
- **The row span is not the column's**: the table's rows run to 2026, `co2_mt`
  to 2024.
- The existing guard rejected `population` summed with a
  `non_additive_dimension` on the year, which is the correct way to sum it, and
  passed both the zero-filled metric and the unmatched ratio.

And on retail, which had to come through unchanged: 49 fixed tool calls
replayed before and after the change differ only in the new `top` field and in
two refusals' wording.

## Decision

- **Groupings are declared in the yml.** A dimension is offered with `config:
  meta: {offered_as: <name>}`; the time grains offered are those no finer than
  the table's own. A request is checked against what MetricFlow's
  `list_dimensions` says its metrics reach.
- **Filter values come from the table's rows**, through MetricFlow's
  dimension-values query asked with every metric that reads the table.
- **Coverage is where each measure's column has a value**, widened to whole
  periods of the table's grain, and the `years` filter names that grain.
- **One table per request**; metrics from two are refused with a request to ask
  separately. So a ratio or derived metric reads one table too: its inputs
  cannot be asked for separately, and the guard refuses one that spans two.
- **`top` ranks one breakdown by place** in Python, by the first metric, after
  the overcount of a distinct count is judged on every row.
- **More guard rules**: a `semi_additive` column is summed only with a
  `non_additive_dimension` on the time dimension; `fill_nulls_with` reads only a
  column a `not_null` test holds; an input of a ratio or derived metric over a
  nullable column carries a filter; a table with measures is timed by its
  `defaults.agg_time_dimension` alone, at a day, month, quarter or year, the
  periods coverage is widened to.

## Rejected

- **A Python map of groupings**, as `GROUP_BY` was. It is a second place a
  dimension's meaning lives, beside the yml that defines it, and every new table
  would be a code change.
- **Filter values from the requested metric's own rows** — MetricFlow's
  dimension-values query with that metric alone. `returns_gbp` reaches 31 of the
  40 countries with sales, so "returns in Bermuda", correctly 0.00, would be
  refused.
- **`fill_nulls_with: 0` on every simple metric**, the rule until now. Right for
  a sale, where no rows is zero; wrong for a country-year column, where a null is
  a year nobody reported.
- **MetricFlow's `order_by` and `limit` for `top`.** The tool could no longer
  say how many groups it left out, nor judge a distinct count's overcount on all
  of them: the largest groups can sum to the total while all of them exceed it.
- **Letting MetricFlow join two tables' metrics.** The join is on the date
  alone, and a table with no rows that year reads as zero.

## Consequences

- More of MetricFlow's undocumented engine is in use (`list_dimensions`,
  `explain_get_dimension_values`, beside `explain`). The compile guard compiles
  every metric alone under each grouping it offers, and the filter pytest cases
  run the dimension-values query, so an upgrade that breaks either fails there.
- A filtered request costs about 0.14 s, not 0.02 s: the value list is a
  MetricFlow query over the table.
- The `fill_nulls_with` rule is a proxy. A column a `not_null` test holds can
  still be absent for a group a filter empties, which for a sale is zero and for
  a periodic table is not; a country-year metric should carry no
  `fill_nulls_with` at all.
- [0015](0015-metrics-in-the-semantic-layer.md)'s "Retail only, for now" still
  stands: this changes how a second table would be read, not whether there is
  one.
