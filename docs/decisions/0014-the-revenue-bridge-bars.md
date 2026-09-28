# 0014. The revenue bridge prices continuing SKUs, and gives churn, returns and non-product lines bars of their own

Status: accepted 2026-09-28

## Context

`agent/bridge.py` explains a change in net revenue as bars that sum to it
exactly. Net revenue is the warehouse's one definition, `sum(line_amount_gbp)
filter (where is_revenue_line)` in `_retail.yml`: product sales net of product
cancellations. Measured over 2010 against 2011, 1 Jan–9 Dec (warehouse built
2026-09-28):

- **The catalogue churns.** 650 SKUs sold in 2011 were not sold in 2010, and
  took £2.35m of sales, a quarter of the year; 895 sold in 2010 were not sold
  in 2011 (£0.67m). A SKU sold in one year only has no price change to measure.
- **Net units per SKU are not always positive.** 49 SKUs have zero or negative
  net units in one of the two years, so a net price per SKU is undefined for
  them. Returns doubled, from −£235k to −£461k.
- **What net revenue leaves out moves in opposite directions.** Bank and
  Amazon fees rose by about £90k while postage income rose by about as much; the
  net non-product change is +£62k.

## Decision

- **Price, volume and mix are measured on the sale lines of continuing SKUs**
  (sold in both years), with mix at SKU level: volume at year a's average price,
  mix as year b's units re-weighted at year a's SKU prices, price as the rest.
- **New SKUs and discontinued SKUs are two bars**, each the whole revenue of the
  SKUs sold in one year only.
- **Returns are one bar**, the change in product cancellations.
- **Outside GBP, FX is a bar**: the GBP effects convert at year a's effective
  rate, and FX is the remainder.
- **Non-product lines are a final step** from net revenue to everything
  invoiced, with their largest item types named, so the bridge reconciles to
  the invoices and says what it set aside.
- Net revenue keeps its one definition. Discounts are the one excluded item an
  accountant would normally deduct from revenue; at −£13.5k over the whole
  extract they change no conclusion.

## Rejected

- **Mix at country level.** The question the bridge answers is which products
  moved; country mix would be a second, coarser decomposition of the same
  change, and can be added beside this one rather than instead of it.
- **Folding new and discontinued SKUs into mix**, as zero units at the other
  year's price. The mix bar would then be mostly churn (+£2.35m and −£0.67m
  against a mix effect of +£0.51m), and a reader could not tell a shift toward
  dearer products from a new range.
- **Netting returns into each SKU's price and volume.** It leaves 49 SKUs with
  no defined price and needs a fallback rule for each; a returns bar keeps the
  price effect about prices.
- **Bridging net revenue alone.** It would hide fee and postage movements of
  about £90k each, and the bridge would no longer reconcile to anything a
  finance team can tie to the invoices.
- **A second revenue definition that deducts discounts**, for the reason above:
  two definitions of net revenue in one warehouse is a larger cost than
  £13.5k.

## Consequences

The volume bar counts continuing SKUs' units only, so it is not the change in
total units; the answer states the SKU counts behind each bar. When the
catalogue churns this much, the two churn bars are the biggest in the bridge,
and that is the finding, not a defect of the method. Price and mix are
order-dependent, as in any sequential bridge: measuring mix at year b's prices
would move value between the two.
