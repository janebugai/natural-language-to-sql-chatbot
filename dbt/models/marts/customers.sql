-- Adds lifetime_order_count / lifetime_revenue: pre-aggregated from orders +
-- order_items so "our best customers" doesn't require the caller to write a
-- join+aggregate themselves. These are already totals — summing
-- order_items again on top of them would double count.
with order_agg as (
    select
        o.customer_id,
        cast(count(distinct o.order_id) as integer) as lifetime_order_count,
        coalesce(sum(oi.unit_price * oi.quantity - oi.discount), 0) as lifetime_revenue
    from {{ ref('stg_orders') }} o
    left join {{ ref('stg_order_items') }} oi on oi.order_id = o.order_id
    group by o.customer_id
)
select
    c.customer_id,
    c.name,
    c.email,
    c.city,
    c.country,
    c.acquisition_channel,
    c.birth_date,
    c.is_business,
    c.signup_date,
    coalesce(oa.lifetime_order_count, 0)      as lifetime_order_count,
    round(coalesce(oa.lifetime_revenue, 0), 2) as lifetime_revenue
from {{ ref('stg_customers') }} c
left join order_agg oa on oa.customer_id = c.customer_id
