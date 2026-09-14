select
    return_id,
    order_item_id,
    return_date,
    reason,
    refund_amount
from {{ ref('stg_returns') }}
