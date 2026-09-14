-- Adds delivery_days: shipped_date -> delivered_date, null while in transit
-- or not yet shipped, so "average delivery time" doesn't need a date_diff
-- written out by the LLM every time.
select
    shipment_id,
    order_id,
    carrier,
    shipped_date,
    delivered_date,
    case when shipped_date is not null and delivered_date is not null
         then cast(date_diff('day', shipped_date, delivered_date) as integer)
    end as delivery_days,
    status
from {{ ref('stg_shipments') }}
