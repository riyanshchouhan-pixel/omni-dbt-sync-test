{{ config(materialized='view') }}

select
    combined_entity_id,
    report_date,
    sum_revenue_predicted,
    sum_insider_commission
from `segment-data.dbt_audit_reporting.combined_entity_stats`
where report_date >= current_date() - 7


