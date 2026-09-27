---
title: 3. Decoupling
description: Which large emitters grew their inflation-adjusted economy while cutting emissions since 2005.
sidebar_position: 3
---

[← All nine findings](/findings)

```sql latest_years
select * from warehouse.latest_years
```

```sql decoupling
with base_year as (
    select country_iso3, co2_mt, gdp_constant_usd
    from warehouse.emissions_energy
    where year = 2005
),

end_year as (
    select country_iso3, country_name, region, co2_mt, gdp_constant_usd
    from warehouse.emissions_energy
    where year = (select gdp_year from ${latest_years})
)

select
    e.country_name,
    e.region,
    100 * (e.co2_mt / b.co2_mt - 1)                     as co2_change,
    100 * (e.gdp_constant_usd / b.gdp_constant_usd - 1) as real_gdp_change,
    e.co2_mt,
    case
        when e.gdp_constant_usd > b.gdp_constant_usd and e.co2_mt < b.co2_mt
            then 'Cut emissions while growing'
        else 'Did not'
    end as decoupled
from end_year e
inner join base_year b on e.country_iso3 = b.country_iso3
where b.gdp_constant_usd is not null
  and e.gdp_constant_usd is not null
  and b.co2_mt > 100
  and e.co2_mt is not null
```

```sql decoupling_examples
-- Cuts negated so the sentence below can say "cutting 20%" rather than "-20%".
select
    count(*)                                                            as n_countries,
    count(*) filter (where decoupled = 'Cut emissions while growing')   as n_decoupled,
    max(real_gdp_change) filter (where country_name = 'United States')  as us_growth,
    -max(co2_change) filter (where country_name = 'United States')      as us_cut,
    max(real_gdp_change) filter (where country_name = 'United Kingdom') as uk_growth,
    -max(co2_change) filter (where country_name = 'United Kingdom')     as uk_cut
from ${decoupling}
```

Of the <Value data={decoupling_examples} column=n_countries/> countries that emitted more than 100 Mt in 2005, <Value data={decoupling_examples} column=n_decoupled/> have since grown their real GDP while cutting emissions.

<ScatterPlot
    data={decoupling}
    x=real_gdp_change
    y=co2_change
    size=co2_mt
    series=decoupled
    seriesColors={{
        'Cut emissions while growing': ['#2a78d6', '#3987e5'],
        'Did not': ['#eb6834', '#d95926']
    }}
    xFmt="0"
    yFmt="0"
    title="Growth against emissions since 2005"
    subtitle="Countries emitting over 100 Mt in 2005. Bubble size is latest-year CO₂."
    xAxisTitle="Real GDP change (%)"
    yAxisTitle="CO₂ change (%)"
    tooltipTitle=country_name
>
    <ReferenceLine y=0 label="No change in emissions" labelPosition=aboveEnd/>
</ScatterPlot>

Anything below the line grew its economy while cutting emissions. The US grew <Value data={decoupling_examples} column=us_growth fmt='0"%"'/> in real terms while cutting emissions <Value data={decoupling_examples} column=us_cut fmt='0"%"'/> over the same years, and the UK grew <Value data={decoupling_examples} column=uk_growth fmt='0"%"'/> and cut <Value data={decoupling_examples} column=uk_cut fmt='0"%"'/> of its emissions.

The standing objection is that production moved offshore, so the cut is an
accounting artifact of where the factory sits. That is testable, and
[finding 4](/findings/offshoring) tests it.

<Alert status=info>

**So what.** This is the national-scale evidence that "grow and cut" is
achievable, and it is the same choice a company makes when it sets a target. An
absolute reduction target is credible alongside a growth plan, but only where
the intensity improvement outruns the growth, and [finding 7](/findings/intensity)
shows that isn't automatic.

**Who acts:** whoever signs the target, which in practice is the CFO rather than
the sustainability team. **Cost of getting it wrong:** committing publicly to an
absolute cut the growth plan makes arithmetically impossible, and restating it
two years later.

</Alert>

<Details title="How this is measured">

GDP is in constant 2015 US dollars (`gdp_constant_usd`), not current dollars.
Current dollars move with inflation and exchange rates, which is enough to flip
the sign of a country's apparent progress. The sample is the countries emitting
more than 100 Mt in 2005, large enough for a percentage change to mean something.

</Details>
