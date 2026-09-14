select
    cast(review_id as integer)   as review_id,
    cast(product_id as integer)  as product_id,
    cast(customer_id as integer) as customer_id,
    cast(rating as integer)      as rating,
    cast(review_date as date)    as review_date
from {{ ref('raw_reviews') }}
