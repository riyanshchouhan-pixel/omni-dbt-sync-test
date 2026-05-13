{{ config(materialized='view') }}

select 
    'Amsterdam' as city_name,
    'Netherlands' as country_name,
    100.50 as revenue
union all
select 'Paris', 'France', 250.75
union all
select 'Tokyo', 'Japan', 500.00
