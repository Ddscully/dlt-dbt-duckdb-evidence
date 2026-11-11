-- Customers ranked by spend, then cumulative share of revenue at each percentile
-- of the base. `ceil` rather than `round` so bucket X means "the top X%" exactly.
--
-- A source query rather than a block on the page because the retail overview
-- and its customers page both draw it, and a page query cannot read another
-- page's. It also runs the window functions once at build time rather than in
-- every visitor's browser.
with ranked as (
    select
        monetary_gbp,
        row_number() over (order by monetary_gbp desc)  as rn,
        count(*) over ()                                as n_customers,
        sum(monetary_gbp) over (
            order by monetary_gbp desc rows unbounded preceding
        )                                               as cumulative_gbp,
        sum(monetary_gbp) over ()                       as total_gbp
    from analytics.retail_rfm
    where monetary_gbp > 0
),

curve as (
    select
        ceil(100.0 * rn / n_customers)                  as pct_of_customers,
        max(100.0 * cumulative_gbp / total_gbp)         as pct_of_revenue
    from ranked
    group by 1
)

select 0 as pct_of_customers, 0.0 as pct_of_revenue
union all
select * from curve
order by 1
