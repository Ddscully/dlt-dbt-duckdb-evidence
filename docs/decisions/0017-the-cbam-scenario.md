# 0017. The CBAM scenario re-prices the mart's certificates for one good, gross of the free-allocation deduction, and says so

Status: accepted 2026-09-28 (#117)

## Context

`run_scenario` (`agent/scenario.py`) answers "what does CBAM cost on this
good at this carbon price?" from `marts.fct_cbam_exposure`, whose certificate
columns fix the tonnage by law and whose euro columns price it at one assumed
EU ETS price, the dbt var `eu_ets_price_eur_per_t` (EUR 75). What was measured
before building, against the warehouse:

- **The table has 260 goods**, named by CN code and a description that runs to
  426 characters. As a schema enum their keys are 12.8 KB, about three times
  the three earlier tools' schemas together, for a model with a small context.
- **A description is not a key**: two aluminium goods are both "Bars and rods".
  A CN code is never shared by two goods.
- **The annex lists countries per product group**, 67 for aluminium and 52 for
  iron and steel, so a country listed for one good can be absent for another.
  Four countries in `dim_country` are absent everywhere: Germany, an EU origin,
  and Iceland, Norway and Switzerland.
- **Ties at the cheapest source are ordinary**: 37 of unwrought aluminium's 67
  listed sources share its lowest value.
- **The doubles carry noise** (`3.5999999999999996`). Re-priced at EUR 75 and
  rounded half up, 840 of the 34,995 (good, country, year) costs are a penny
  above the mart's euro column: each is exactly half a penny, which the double
  holds just below.

And one finding from outside the warehouse, checked against secondary sources
only (ICAP; a CBAM guide), since EUR-Lex does not serve to the fetcher: **an
importer surrenders fewer certificates than the default values imply**, in
step with the EU ETS allowances EU producers still receive free — a benchmark
per good times a factor that phases out from 2026 to 2034. ICAP reports the
benchmarks as provisional, to be "reviewed and updated until 1 January 2027".
Nothing in the warehouse models that deduction, so the mart's figures are
gross of it.

## Decision

- **Gross figures, labelled.** Every result carries a note, quoted verbatim by
  the loop and sent as a second block by the MCP server, that the figures come
  before the free-allocation deduction and are not what an importer will owe,
  that they are the regulation's defaults rather than a supplier's verified
  emissions, and that the price is an assumption.
- **One good per call, named in words**: an exact `good_key`, a CN code or its
  prefix, or words matched against the description and the product group.
  Several matches return a list to choose from, not an error.
- **A result shows the good's cheapest, median and dearest listed source**,
  naming a country and how many share its value, **the fallback row**, and any
  countries asked for, each marked as carrying its own value or the fallback's.
  An optional tonnage costs a shipment, since the model may not multiply.
- **A country the annex does not list for the good is named and never
  priced**, with the rule: the fallback applies to an unlisted country only if
  CBAM covers it, and the exemptions are not known here. A name that is no
  country at all is refused, with the nearest spellings the warehouse uses.
- **`Decimal` arithmetic**: certificates rounded to five places, euros to the
  penny half up, once, after every multiplication.

## Rejected

- **Modelling the deduction now.** The benchmarks are provisional until 2027,
  and could not be confirmed from a primary source — the same ground on which
  Annex IV was not transcribed ([0009](0009-cbam-annex-transcribed-faithfully.md)).
- **Not building the tool until then.** The gross figure is what the mart and
  the CBAM page already publish; stated as gross, it is an honest screening
  number.
- **A `good_key` enum in the schema**, at 12.8 KB; and **a separate
  `find_good` tool**, one more choice for a small model to get wrong.
- **A product-group summary**: a median across goods is not a cost anyone pays.
- **The full table of listed sources**, up to 101 rows for one good.
- **Pricing an unlisted country at the fallback**, which would put a CBAM cost
  on goods from Germany, Norway and Switzerland.
- **Refusing the whole call over one unlisted country**, as planned: the model
  would have to call again only to drop it.

## Consequences

The mart's own wording — `certificates_2026_t_co2e_per_t` as "certificates
surrendered per tonne", the model as what a tonne "costs at the EU border" —
states the gross figure as the net one, and is left for a change of its own.
When the benchmarks are final, the deduction can be a seed like the mark-up
schedule ([0010](0010-cbam-markup-schedule-is-a-seed.md)), and the note would
change from "gross" to what the net figure assumes.
