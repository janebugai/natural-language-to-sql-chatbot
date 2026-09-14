select
    cast(product_id as integer)   as product_id,
    cast(category_id as integer)  as category_id,
    cast(product_name as varchar) as product_name,
    cast(brand as varchar)        as brand,
    cast(unit_price as double)    as unit_price,
    cast(cost as double)          as cost,
    cast(launch_date as date)     as launch_date,
    cast(is_active as integer)    as is_active
from {{ ref('raw_products') }}
