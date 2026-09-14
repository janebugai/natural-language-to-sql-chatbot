-- Adds margin_pct: the actual margin this product is realizing, so it can be
-- compared against categories.target_margin without the caller having to
-- redo the arithmetic themselves.
select
    product_id,
    category_id,
    product_name,
    brand,
    unit_price,
    cost,
    case when unit_price = 0 then null
         else round((unit_price - cost) / unit_price, 4)
    end as margin_pct,
    launch_date,
    is_active
from {{ ref('stg_products') }}
