-- Adds net_amount: unit_price * quantity - discount. This is the figure the
-- "revenue" business metric sums over (see app/metadata/ecommerce.yaml).
select
    order_item_id,
    order_id,
    product_id,
    quantity,
    unit_price,
    discount,
    round(unit_price * quantity - discount, 2) as net_amount
from {{ ref('stg_order_items') }}
