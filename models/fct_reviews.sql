{{ config(materialized='view') }}

select
    1                           as review_id,
    101                         as experience_id,
    201                         as booking_id,
    301                         as customer_id,
    'Amsterdam'                 as city_name,
    'Netherlands'               as country_name,
    5                           as rating,
    'Absolutely loved it!'      as review_text,
    TRUE                        as is_verified,
    DATE('2024-01-15')          as review_date,
    TIMESTAMP('2024-01-15 10:30:00') as reviewed_at,
    89.99                       as experience_price,
    1                           as review_count

union all select 2, 102, 202, 302, 'Paris', 'France', 4, 'Great experience, would recommend.', TRUE, DATE('2024-02-20'), TIMESTAMP('2024-02-20 14:15:00'), 120.00, 1
union all select 3, 103, 203, 303, 'Tokyo', 'Japan', 5, 'Best trip of my life!', TRUE, DATE('2024-03-05'), TIMESTAMP('2024-03-05 09:00:00'), 200.00, 1
union all select 4, 101, 204, 304, 'Amsterdam', 'Netherlands', 3, 'Good but could be better.', FALSE, DATE('2024-03-10'), TIMESTAMP('2024-03-10 16:45:00'), 89.99, 1
union all select 5, 104, 205, 305, 'New York', 'USA', 2, 'Disappointing, not as described.', TRUE, DATE('2024-04-01'), TIMESTAMP('2024-04-01 11:00:00'), 150.00, 1
union all select 6, 102, 206, 301, 'Paris', 'France', 5, 'Magnifique! Will be back.', TRUE, DATE('2024-04-18'), TIMESTAMP('2024-04-18 13:30:00'), 120.00, 1
union all select 7, 105, 207, 306, 'Barcelona', 'Spain', 4, 'Very well organized tour.', TRUE, DATE('2024-05-02'), TIMESTAMP('2024-05-02 10:00:00'), 95.00, 1
union all select 8, 103, 208, 307, 'Tokyo', 'Japan', 1, 'Cancelled last minute, very unhappy.', TRUE, DATE('2024-05-15'), TIMESTAMP('2024-05-15 08:00:00'), 200.00, 1
