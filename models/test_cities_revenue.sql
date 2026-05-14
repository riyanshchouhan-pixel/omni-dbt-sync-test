{{ config(materialized='view') }}

select
    'Amsterdam' as city_name,
    100.50 as revenue
union all
select 'Paris', 250.75
union all
select 'Tokyo', 500.00
