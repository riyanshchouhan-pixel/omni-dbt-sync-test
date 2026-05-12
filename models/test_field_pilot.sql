{{ config(materialized='view') }}

select 1 as test_id, 'Amsterdam' as city_name, 100.50 as test_revenue
union all
select 2 as test_id, 'Paris' as city_name, 250.75 as test_revenue
union all
select 3 as test_id, 'Tokyo' as city_name, 500.00 as test_revenue
