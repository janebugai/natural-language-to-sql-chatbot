select
    cast(return_id as integer)     as return_id,
    cast(order_item_id as integer) as order_item_id,
    cast(return_date as date)      as return_date,
    cast(reason as varchar)        as reason,
    cast(refund_amount as double)  as refund_amount
from {{ ref('raw_returns') }}
