{{ config(materialized='view') }}

select
  1                                    as id,
  'Amsterdam'                          as city_name,
  100.50                               as revenue,
  42                                   as item_count,
  DATE('2024-01-01')                   as created_date,
  TIMESTAMP('2024-01-01 10:00:00')     as created_at,
  TRUE                                 as is_active,
  CAST(100.50 / 3 AS FLOAT64)          as avg_revenue

union all

select
  2, 'Paris', 250.75, 18,
  DATE('2024-02-15'),
  TIMESTAMP('2024-02-15 14:30:00'),
  FALSE,
  CAST(250.75 / 5 AS FLOAT64)

union all

select
  3, 'Tokyo', 500.00, 35,
  DATE('2024-03-20'),
  TIMESTAMP('2024-03-20 09:15:00'),
  TRUE,
  CAST(500.00 / 7 AS FLOAT64)
