---
title: Method and limits
description: The CBAM mark-up schedule, the annex correction, and the five limits a practitioner checks first.
sidebar_position: 3
---

[← CBAM Exposure](/cbam)

**Every number in this section is an administrative default, marked up on purpose.
A real importer with supplier data will usually pay less, and every importer's
obligation is further reduced by two deductions this section does not model.**

## The annex and its correction

The values are Annex I of Implementing Regulation (EU) 2025/2621 **as corrected by
[Implementing Regulation (EU) 2026/1740](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32026R1740)**,
which replaced Annexes I and IV in full on 3 August 2026 and applies retroactively
from 1 January. The numbers barely moved (66 of 10,503 comparable rows, mostly
down by a percent or two), but the table changed shape, and most of the original
annex's defects were fixed at source. The first annual declaration, covering 2026
imports, is due in 2027.

## The mark-up is set per product group

```sql markups
select
    product_group,
    count(*)                                            as n_rows,
    avg(markup_2026_pct)                                as markup_pct,
    median(cbam_cost_2026_eur_per_t)                    as median_2026,
    median(cbam_cost_2027_eur_per_t)                    as median_2027,
    median(cbam_cost_2028_eur_per_t)                    as median_2028
from warehouse.cbam_exposure
group by 1
order by median_2026 desc
```

<DataTable data={markups} rowNumbers=false>
    <Column id=product_group title="Product group"/>
    <Column id=n_rows title="Rows" fmt='#,##0'/>
    <Column id=markup_pct title="2026 mark-up" fmt='0.0"%"'/>
    <Column id=median_2026 title="Median €/t, 2026" fmt='€#,##0.00'/>
    <Column id=median_2027 title="2027" fmt='€#,##0.00'/>
    <Column id=median_2028 title="2028" fmt='€#,##0.00'/>
</DataTable>

The defaults carry a mark-up of **10% in 2026, 20% in 2027 and 30% from 2028**, with
fertilisers at 1% in all three years. A flat 10/20/30% would overstate every one of
the 2,457 fertiliser rows by nine points in 2026 and twenty-seven by 2028.

## The price is a parameter

There is no clean free public API for EU ETS spot, so the carbon price is an
assumption, stated on the [overview](/cbam) and applied to the regulation's
tonnage. The range charted there, €60 to €120 a tonne, is roughly where EUAs have
traded since 2022, with room above it.

## What this is not

<Alert status=warning>

**A screening tool, not a filing.** Every number here is an *administrative
default*: an estimate of a country's average, deliberately marked up so that
obtaining verified installation data is the cheaper path. What this ranks is which
sourcing lanes are worth the effort of going to get that data.

</Alert>

- **The figures are gross.** What an importer surrenders is reduced by two
  deductions that nothing here models: one for the EU ETS allowances EU producers
  of the good still receive free (a benchmark per good, times a factor that
  phases out from 2026 to 2034), and one for any carbon price effectively paid
  where the good was made, under Article 9 of Regulation (EU) 2023/956. When this
  was written the benchmarks were provisional and the Article 9 rules not yet
  adopted, so neither can be applied yet with confidence.
- **A CN code alone does not always identify a row.** In the original annex,
  2523 10 00 was both white clinker and grey clinker, whose default values differ
  by more than a factor of two. The correction gives those two 10-digit TARIC
  codes, 2523 10 00 10 and 2523 10 00 90, so that trap is closed, but the annex
  still prints 4- and 6-digit headings above the rows that carry the numbers, and
  classification to the right description remains the importer's problem.
- **The annex's defects are reproduced rather than corrected.** The seed
  transcribes the regulation as it stands. A legal instrument is not this
  project's to tidy up, and the correction is the argument for that: the original
  annex's quirks were fixed by the body that wrote it.
- **The mark-up is asserted, not read off the annex.** The corrected annex
  publishes only direct, indirect and total, so the phase-in comes from a seed
  stating what the articles say. Same numbers, weaker provenance.
- **The grid factor shown elsewhere in this warehouse is not the annex's.** The
  regulation's own electricity emission factors come from IEA data under a
  non-commercial licence, which this project deliberately does not redistribute.
  The indirect part of the annex cannot be reconciled against the OWID-derived
  factors on the [Scope 2](/scope2) page. They sit beside each other; they are not
  the same measurement.

The underlying table is `marts.fct_cbam_exposure`, and the transcribed annex is
the `cbam_default_values` and `cbam_goods` seeds, with the phase-in rates in
`cbam_markup_schedule`. The mart ships in the
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)
both as Parquet and inside the DuckDB file; the seeds are in the DuckDB file's
`main` schema only.
