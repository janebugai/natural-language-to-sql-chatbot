select
    cast(category_id as integer)  as category_id,
    cast(name as varchar)         as name,
    cast(department as varchar)   as department,
    cast(target_margin as double) as target_margin
from {{ ref('raw_categories') }}
