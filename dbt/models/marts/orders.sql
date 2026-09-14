select
    order_id,
    customer_id,
    order_date,
    status,
    channel,
    payment_method,
    shipping_cost,
    tax,
    promo_code
from {{ ref('stg_orders') }}
