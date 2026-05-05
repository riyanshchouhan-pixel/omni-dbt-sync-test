-- Minimal test model. Selects a few columns from an existing
-- table so the model is "valid" without us needing to run dbt.
select
    combined_entity_id,
    report_date,
    sum_revenue_predicted,
    sum_insider_commission
from `headout-analytics.analytics_reporting.combined_entity_stats`
where report_date = current_date()
