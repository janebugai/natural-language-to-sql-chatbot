select
    category_id,
    name,
    department,
    target_margin
from {{ ref('stg_categories') }}
