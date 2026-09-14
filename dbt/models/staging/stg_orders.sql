select
    cast(order_id as integer)       as order_id,
    cast(customer_id as integer)    as customer_id,
    cast(order_date as date)        as order_date,
    cast(status as varchar)         as status,
    cast(channel as varchar)        as channel,
    cast(payment_method as varchar) as payment_method,
    cast(shipping_cost as double)   as shipping_cost,
    cast(tax as double)             as tax,
    cast(promo_code as varchar)     as promo_code
from {{ ref('raw_orders') }}
