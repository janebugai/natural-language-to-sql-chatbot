select
    review_id,
    product_id,
    customer_id,
    rating,
    review_date
from {{ ref('stg_reviews') }}
