# dbt ↔ Omni Sync Test Report

**Project:** omni-dbt-sync-test
**Tester:** Riyansh Chouhan
**Repo:** https://github.com/riyanshchouhan-pixel/omni-dbt-sync-test
**Omni Model:** Test (headout.omniapp.co)
**BigQuery Project:** headout-dev
**Dataset:** dbt_riyansh
**dbt Model:** test_field_pilot

---

## Test Environment Setup
| Component | Details |
|-----------|---------|
| Omni Model | Test |
| BigQuery Connection | headout-dev |
| dbt Dataset | dbt_riyansh |
| GitHub Repo | riyanshchouhan-pixel/omni-dbt-sync-test |
| Git Branch | main |

---

## Test Results

### Test 1 — Add a new column in dbt → does it appear in Omni?
- **Date:** 13 May 2026
- **Change:** Added `country_name` column to `test_field_pilot.sql`
- **Action:** Ran `dbt run` + Schema Refresh in Omni
- **Result:** ✅ PASSED
- **Notes:** `country_name: {}` appeared as a new dimension in `test_field_pilot.view` in Omni after schema refresh. Trigger: Schema Refresh (not dbt metadata sync).

### Test 2 — Change field description in dbt schema.yml → does it appear in Omni?
- **Date:** 13 May 2026
- **Change:** Updated `city_name` description to `"Updated city description - Test 2 sync check"` in `schema.yml`
- **Action:** `git push` + Schema Refresh in Omni (with dbt environment configured)
- **Result:** ✅ PASSED
- **Notes:** Description appeared in `test_field_pilot.view` with comment `#This description was pulled from dbt.`. Also synced: all other field descriptions, full dbt SQL code, dbt metadata block. Bottom bar now shows `dbt env: dev (Health ●)`. Trigger: Schema Refresh with dbt environment set up.

---

### Test 3 — Change field label (meta.label) in dbt → does it appear in Omni?
- **Date:** 13 May 2026
- **Change:** Updated `city_name` label to `"City Name [TEST 3 LABEL CHANGE]"` in `schema.yml`
- **Action:** `git push` + Schema Refresh in Omni
- **Result:** ❌ FAILED
- **Notes:** Field appeared as "City Name" (Omni's auto-format of `city_name`) in workbook field picker — NOT "City Name [TEST 3 LABEL CHANGE]". `meta.label` in dbt is NOT synced to Omni's label. Labels must be defined directly in Omni's view YAML.

---

### Test 4 — Add a new dbt model → does it appear in Omni?
- **Date:** 13 May 2026
- **Change:** Created `test_cities_revenue.sql` with 4 columns (`city_name`, `country_name`, `revenue`, `region`)
- **Action:** `dbt run` + `git push` + Schema Refresh in Omni
- **Result:** ✅ PASSED
- **Notes:** `test_cities_revenue.view` appeared in Omni under `headout-dev.dbt_riyansh` with all 4 dimensions, auto-generated count measure, and full dbt SQL code pulled in. Trigger: Schema Refresh.

---

### Test 5 — Delete a column in dbt → does it disappear from Omni?
- **Date:** 13 May 2026
- **Change:** Removed `region` column from `test_cities_revenue.sql`
- **Action:** `dbt run` + `git push` + Schema Refresh in Omni
- **Result:** ✅ PASSED
- **Notes:** `region` dimension completely disappeared from `test_cities_revenue.view`. dbt SQL code in the view also updated to reflect the removal. Trigger: Schema Refresh.

### Test 6 — Change aggregate type (meta.aggregate_type) in dbt → does it appear in Omni?
- **Date:** 13 May 2026
- **Change:** Updated `test_revenue` meta field to `aggregate_type: avg` in `schema.yml` (previously `sum`)
- **Action:** `git push` + Schema Refresh in Omni
- **Result:** ❌ FAILED
- **Notes:** The `measures` section in `test_field_pilot.view` only shows the auto-generated `count` measure. `test_revenue` did NOT appear as a measure with `aggregate_type: avg`. `meta.aggregate_type` in dbt is NOT synced to Omni as a measure aggregate type. Aggregate types / custom measures must be defined directly in Omni's view YAML. Same pattern as Test 3 — `meta.*` fields do not sync.

---

### Test 7 — Add a dbt metric → does it appear as a measure in Omni?
- **Date:** 13 May 2026
- **Change:** Added `semantic_models` block with `total_revenue` measure and `create_metric: true` to `schema.yml`
- **Action:** `git push` + Schema Refresh in Omni
- **Result:** ⚠️ BLOCKED
- **Notes:** dbt MetricFlow (dbt 1.6+) requires a **time spine model** to parse semantic models. Without it, `dbt parse` fails with: *"The semantic layer requires a time spine model with granularity DAY or smaller in the project, but none was found."* Omni dbt sync fails as a result. dbt metrics are not testable in this setup without additional MetricFlow infrastructure. Old dbt metrics syntax (pre-1.6) is also unsupported in dbt 1.11.x.

---

## Omni Override Tests (Omni IDE → Schema Refresh)

### Test 9 — Add a measure in Omni IDE → does it survive Schema Refresh?
- **Date:** 13 May 2026
- **Change:** Added `total_revenue` measure (`aggregate_type: sum`, `sql: ${test_revenue}`) directly in `test_field_pilot.view` in Omni IDE
- **Action:** Merged branch to main → Schema Refresh
- **Result:** ✅ PASSED
- **Notes:** `total_revenue` measure survived the Schema Refresh. Omni only updates dbt-derived parts (dimensions, descriptions) during refresh — custom measures added in Omni IDE are preserved. Safe to add custom measures in Omni without fear of them being wiped.

---

### Test 10 — Change a field label in Omni IDE → does it survive Schema Refresh?
- **Date:** 13 May 2026
- **Change:** Added `label: "City Name [OMNI OVERRIDE TEST 10]"` to `city_name` dimension in Omni IDE
- **Action:** Save to branch → Merge → Schema Refresh
- **Result:** ✅ PASSED
- **Notes:** Label survived Schema Refresh. Omni does not overwrite custom labels set in the view YAML. Note: `label:` is a valid Omni YAML property for dimensions. Earlier errors were caused by combining it with `hidden: true`, not by `label` itself.

---

### Test 11 — Add a custom dimension in Omni IDE → does it survive Schema Refresh?
- **Date:** 13 May 2026
- **Change:** Added `city_upper:` dimension with `sql: "UPPER(${city_name})"` to `test_field_pilot.view` in Omni IDE
- **Action:** Save to branch → Merge → Schema Refresh
- **Result:** ✅ PASSED
- **Notes:** `city_upper` custom computed dimension survived Schema Refresh completely. Custom dimensions added in Omni IDE are not wiped by dbt schema changes. Note: `type: string` is not a valid Omni dimension property — only `sql:` is needed.

---

### Test 12 — Hide a field in Omni IDE → does it survive Schema Refresh?
- **Date:** 13 May 2026
- **Change:** Added `hidden: true` to `test_revenue` dimension in Omni IDE view YAML
- **Action:** Save to branch → Merge → Schema Refresh
- **Result:** ✅ PASSED
- **Notes:** `hidden: true` survived Schema Refresh. `hidden` IS a valid Omni YAML property for dimensions when set directly in the view file. Important distinction: using "Hide" from the Explore UI (Modeling → Hide) does NOT write to the view YAML — it stores the state in Omni's internal DB, not git. Only hiding via the view YAML is git-managed and persistent.

---

### Test 13 — Change a field description in Omni IDE → does dbt overwrite it on Schema Refresh?
- **Date:** 13 May 2026
- **Change:** Changed `city_name` description to `"OMNI OVERRIDE - Test 13 custom description"` in Omni IDE (overriding dbt's `"Updated city description - Test 2 sync check"`)
- **Action:** Save to branch → Merge → Schema Refresh
- **Result:** ✅ PASSED (Omni wins)
- **Notes:** Omni's custom description survived — dbt did NOT overwrite it. The `#This description was pulled from dbt.` comment is absent from `city_name`, confirming Omni's version took precedence. Key insight: **once you override a description in Omni's view YAML, dbt's description will not overwrite it on subsequent Schema Refreshes.** Fields without a custom Omni description will continue to pull from dbt.
- **Key observation:** `city_name` on line 17 has NO `#This description was pulled from dbt.` comment, unlike `test_revenue` (line 23) which still has it. This comment acts as a marker — its absence on `city_name` confirms that once you override a description in Omni, it takes priority and dbt's version won't overwrite it.

---

## Pending Tests

| # | Test | Direction | Status |
|---|------|-----------|--------|
| 8 | Change materialization (view → table) | dbt → Omni | 🔲 Pending |

