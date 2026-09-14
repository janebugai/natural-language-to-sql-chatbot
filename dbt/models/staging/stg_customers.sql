select
    cast(customer_id as integer)         as customer_id,
    cast(name as varchar)                as name,
    cast(email as varchar)                as email,
    cast(city as varchar)                as city,
    cast(country as varchar)              as country,
    cast(acquisition_channel as varchar)  as acquisition_channel,
    cast(birth_date as date)              as birth_date,
    cast(is_business as integer)          as is_business,
    cast(signup_date as date)             as signup_date
from {{ ref('raw_customers') }}
