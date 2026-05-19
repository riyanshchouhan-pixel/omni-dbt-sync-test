import json
import re
import os
import time
import requests
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
CODA_TOKEN      = os.environ.get("CODA_API_KEY")
CODA_BASE       = "https://coda.io/apis/v1"
FOLDER_ID       = "fl-EAb0FEKf-m"
DOC_TITLE       = "Data Dictionary — dbt + Omni"

OMNI_MODEL_ID   = "fa15e44a-4bb9-41e8-8279-187d01d9b562"
OMNI_KEY        = os.environ.get("OMNI_API_KEY")
OMNI_BASE_URL   = f"https://headout.omniapp.co/api/v1/models/{OMNI_MODEL_ID}"
MANIFEST_PATH   = "target/manifest.json"

MODEL_TO_FILE_KEY = {
    "fct_reviews":        "headout-dev.dbt_riyansh/fct_reviews.view",
    "test_field_pilot":   "headout-dev.dbt_riyansh/test_field_pilot.view",
    "test_cities_revenue":"headout-dev.dbt_riyansh/test_cities_revenue.view",
}

DBT_SECTION_MARKER = "#The info below was pulled from your dbt repository"
SKIP_KEYS = {'dimensions', 'measures', 'catalog', 'schema', 'table_name', 'dbt', 'version'}


# ── Coda helpers ──────────────────────────────────────────────────────────────
def coda_headers():
    return {
        "Authorization": f"Bearer {CODA_TOKEN}",
        "Content-Type": "application/json"
    }


def get_or_create_doc():
    """Find existing doc by title in folder, or create a new one."""
    resp = requests.get(f"{CODA_BASE}/docs", headers=coda_headers(),
                        params={"folderId": FOLDER_ID})
    resp.raise_for_status()
    for doc in resp.json().get("items", []):
        if doc["name"] == DOC_TITLE:
            print(f"Found existing doc: {doc['id']}")
            return doc["id"]

    resp = requests.post(f"{CODA_BASE}/docs", headers=coda_headers(), json={
        "title": DOC_TITLE,
        "folderId": FOLDER_ID
    })
    resp.raise_for_status()
    doc_id = resp.json()["id"]
    print(f"Created new doc: {doc_id}")
    print("Waiting for doc to initialize...")
    time.sleep(5)
    return doc_id


def list_pages(doc_id):
    for attempt in range(10):
        resp = requests.get(f"{CODA_BASE}/docs/{doc_id}/pages", headers=coda_headers())
        if resp.status_code == 409:
            print(f"  Doc still initializing, waiting... ({attempt + 1}/10)")
            time.sleep(3)
            continue
        resp.raise_for_status()
        return {p["name"]: p["id"] for p in resp.json().get("items", [])}
    raise Exception("Doc failed to initialize after 30 seconds")


def create_page(doc_id, name, parent_id=None):
    payload = {"name": name}
    if parent_id:
        payload["parentPageId"] = parent_id
    resp = requests.post(f"{CODA_BASE}/docs/{doc_id}/pages",
                         headers=coda_headers(), json=payload)
    resp.raise_for_status()
    page_id = resp.json()["id"]
    time.sleep(4)  # wait for page to be ready
    return page_id


def update_page_content(doc_id, page_id, markdown):
    resp = requests.put(
        f"{CODA_BASE}/docs/{doc_id}/pages/{page_id}",
        headers=coda_headers(),
        json={
            "contentUpdate": {
                "insertionMode": "replace",
                "canvasContent": {
                    "format": "markdown",
                    "content": markdown
                }
            }
        }
    )
    if not resp.ok:
        print(f"  ⚠️  Content update failed {resp.status_code}: {resp.text[:300]}")
    return resp.ok


# ── Data helpers ───────────────────────────────────────────────────────────────
def load_manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def parse_dbt_models(manifest):
    """Returns { model_name: { description, columns: { col: desc } } }"""
    result = {}
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") == "model":
            name = node["name"]
            result[name] = {
                "description": node.get("description", "").strip(),
                "columns": {}
            }
            for col_name, col_data in node.get("columns", {}).items():
                desc = col_data.get("description", "").strip()
                result[name]["columns"][col_name] = desc
    return result


def get_omni_yaml():
    resp = requests.get(f"{OMNI_BASE_URL}/yaml",
                        headers={"Authorization": f"Bearer {OMNI_KEY}"},
                        timeout=30)
    resp.raise_for_status()
    return resp.json().get("files", {})


def parse_omni_fields(yaml_content):
    """Returns { field_key: { description, ai_context, sql } }"""
    result = {}
    lines = yaml_content.split('\n')
    current_field = None
    in_dbt_section = False

    for line in lines:
        if DBT_SECTION_MARKER in line:
            in_dbt_section = True
        if in_dbt_section:
            continue

        field_match = re.match(r'^  (\w+):\s*(\{\})?$', line)
        if field_match:
            key = field_match.group(1)
            if key not in SKIP_KEYS:
                current_field = key
                result[current_field] = {'description': None, 'ai_context': None, 'sql': None}

        if current_field:
            sql_match = re.match(r"^    sql:\s*[\"']?(.+?)[\"']?\s*$", line)
            if sql_match:
                result[current_field]['sql'] = sql_match.group(1).strip("\"'")

            desc_match = re.match(r'^    description:\s*(.+)$', line)
            if desc_match:
                result[current_field]['description'] = desc_match.group(1).strip().strip('"')

            ai_match = re.match(r'^    ai_context:\s*(.+)$', line)
            if ai_match:
                result[current_field]['ai_context'] = ai_match.group(1).strip().strip('"')

    return result


# ── Build markdown for a model page ───────────────────────────────────────────
def build_page_markdown(model_name, dbt_model, omni_fields):
    dbt_columns = dbt_model["columns"]
    model_desc  = dbt_model["description"] or "_No description in dbt._"

    lines = []
    lines.append(f"# {model_name}")
    lines.append(f"_{model_desc}_")
    lines.append("")
    lines.append(f"_Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Table header
    lines.append("| dbt Column | Omni Field | Description | ai_context | Source |")
    lines.append("|---|---|---|---|---|")

    seen_omni_keys = set()

    # ── dbt columns first
    for col_name, dbt_desc in dbt_columns.items():
        # Find matching Omni field
        matched_omni_key = None
        for omni_key, field_data in omni_fields.items():
            if '[' in omni_key:
                continue
            sql_val = field_data.get('sql')
            if sql_val and sql_val == col_name:
                matched_omni_key = omni_key
                break
            elif not sql_val and omni_key == col_name:
                matched_omni_key = omni_key
                break

        omni_desc = ""
        omni_ai   = ""
        omni_field_display = "_not in Omni_"
        source = "🔵 dbt only"

        if matched_omni_key:
            seen_omni_keys.add(matched_omni_key)
            field_data = omni_fields[matched_omni_key]
            omni_desc  = field_data.get('description') or ""
            omni_ai    = field_data.get('ai_context') or ""
            omni_field_display = matched_omni_key if matched_omni_key != col_name else col_name

            if omni_desc == dbt_desc:
                source = "✅ In sync"
            else:
                source = "⚠️ Override"

        description = omni_desc or dbt_desc or "_no description_"
        lines.append(f"| {col_name} | {omni_field_display} | {description} | {omni_ai or '_none_'} | {source} |")

    # ── Omni-only fields (not matched to any dbt column)
    for omni_key, field_data in omni_fields.items():
        if omni_key in seen_omni_keys:
            continue
        if '[' in omni_key:
            continue
        sql_val  = field_data.get('sql') or ""
        omni_desc = field_data.get('description') or "_no description_"
        omni_ai   = field_data.get('ai_context') or "_none_"
        # Skip if sql is a computed expression (contains spaces, parens, $)
        if any(c in sql_val for c in ['(', '$', ' ']):
            source = "🔧 Computed"
        else:
            source = "🆕 Omni only"
        lines.append(f"| _n/a_ | {omni_key} | {omni_desc} | {omni_ai} | {source} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Legend:**")
    lines.append("- ✅ In sync — dbt and Omni descriptions match")
    lines.append("- ⚠️ Override — Omni description differs from dbt")
    lines.append("- 🔵 dbt only — column exists in dbt but not yet in Omni")
    lines.append("- 🆕 Omni only — field created in Omni, not in dbt")
    lines.append("- 🔧 Computed — derived field in Omni (e.g. UPPER, joins)")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not CODA_TOKEN:
        raise ValueError("CODA_API_KEY not set")
    if not OMNI_KEY:
        raise ValueError("OMNI_API_KEY not set")

    print("Loading manifest.json...")
    manifest   = load_manifest()
    dbt_models = parse_dbt_models(manifest)

    print("Fetching Omni YAML...")
    omni_files = get_omni_yaml()

    print("Getting/creating Coda doc...")
    doc_id     = get_or_create_doc()
    pages      = list_pages(doc_id)
    print(f"Existing pages: {list(pages.keys())}")

    for model_name, file_key in MODEL_TO_FILE_KEY.items():
        print(f"\nProcessing {model_name}...")

        dbt_model  = dbt_models.get(model_name, {"description": "", "columns": {}})
        yaml_content = omni_files.get(file_key)
        if not yaml_content:
            print(f"  Skipping — Omni file not found for {file_key}")
            continue

        omni_fields = parse_omni_fields(yaml_content)
        markdown    = build_page_markdown(model_name, dbt_model, omni_fields)

        # Get or create page
        if model_name in pages:
            page_id = pages[model_name]
            print(f"  Updating existing page: {page_id}")
        else:
            page_id = create_page(doc_id, model_name)
            print(f"  Created new page: {page_id}")

        ok = update_page_content(doc_id, page_id, markdown)
        if ok:
            print(f"  ✅ Page updated")

    print(f"\n✅ Done. View your doc: https://coda.io/d/{doc_id}")


if __name__ == "__main__":
    main()
