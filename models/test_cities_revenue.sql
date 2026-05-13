{{ config(materialized='view') }}

select 
    'Amsterdam' as city_name,
    'Netherlands' as country_name,
    100.50 as revenue,
    'Europe' as region
union all
select 'Paris', 'France', 250.75, 'Europe'
union all
select 'Tokyo', 'Japan', 500.00, 'Asia'
