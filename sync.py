import json
import re
import os
import requests

# Config
OMNI_MODEL_ID = "fa15e44a-4bb9-41e8-8279-187d01d9b562"
MANIFEST_PATH = "target/manifest.json"
OMNI_KEY = os.environ.get("OMNI_API_KEY")
OMNI_BASE_URL = f"https://headout.omniapp.co/api/v1/models/{OMNI_MODEL_ID}"

# Map dbt model name → Omni file key
# Add more entries here as you onboard additional models
MODEL_TO_FILE_KEY = {
    "fct_reviews": "headout-dev.dbt_riyansh/fct_reviews.view",
    # "test_field_pilot": "headout-dev.dbt_riyansh/test_field_pilot.view",
    # "test_cities_revenue": "headout-dev.dbt_riyansh/test_cities_revenue.view",
}

DBT_SECTION_MARKER = "#The info below was pulled from your dbt repository"


def load_manifest(path):
    with open(path) as f:
        return json.load(f)


def parse_dbt_descriptions(manifest):
    """Returns { model_name: { column_name: description } }"""
    result = {}
    for node_key, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") == "model":
            model_name = node["name"]
            result[model_name] = {}
            for col_name, col_data in node.get("columns", {}).items():
                desc = col_data.get("description", "").strip()
                if desc:
                    result[model_name][col_name] = desc
    return result


def get_omni_yaml():
    """Fetch all YAML files from Omni. Returns { file_key: yaml_string }"""
    headers = {"Authorization": f"Bearer {OMNI_KEY}"}
    resp = requests.get(f"{OMNI_BASE_URL}/yaml", headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("files", {})


def parse_omni_fields(yaml_content):
    """
    Returns { omni_field_key: { description, ai_context, sql } }
    sql = underlying column name if field is renamed in Omni, else None
    """
    result = {}
    lines = yaml_content.split('\n')
    current_field = None
    in_dbt_section = False

    SKIP_KEYS = {'dimensions', 'measures', 'catalog', 'schema', 'table_name', 'dbt', 'version'}

    for line in lines:
        if DBT_SECTION_MARKER in line:
            in_dbt_section = True
        if in_dbt_section:
            continue

        # Field name at exactly 2-space indent (with or without {} empty spec)
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
                result[current_field]['description'] = desc_match.group(1).strip()

            ai_match = re.match(r'^    ai_context:\s*(.+)$', line)
            if ai_match:
                result[current_field]['ai_context'] = ai_match.group(1).strip()

    return result


def update_field_in_yaml(yaml_content, field_name, new_description):
    """
    Update a field's description and ai_context in the raw YAML string.
    Preserves all other field properties (format, sql, aggregate_type, etc.)
    Strips any old dbt comment markers since Omni drops them anyway.
    """
    lines = yaml_content.split('\n')
    result = []
    i = 0
    in_dbt_section = False

    while i < len(lines):
        line = lines[i]

        if DBT_SECTION_MARKER in line:
            in_dbt_section = True

        if not in_dbt_section and re.match(rf'^  {re.escape(field_name)}:$', line):
            result.append(line)  # field name line
            i += 1

            # Collect preserved properties (format, sql, aggregate_type, etc.)
            # Skip old dbt comment markers, description, ai_context
            preserved = []
            while i < len(lines):
                curr = lines[i]
                # Stop at a 0-indent section header (measures:, dimensions:, dbt:, etc.)
                if re.match(r'^\w', curr) and curr.strip() != '':
                    break
                # Stop at next 2-indent field (sibling field)
                if re.match(r'^  \w', curr) and not re.match(r'^    ', curr) and curr.strip() != '':
                    break
                # Skip old dbt marker comment (best-effort, in case it's still there)
                if curr.strip().startswith('#') and 'dbt' in curr.lower():
                    i += 1
                    continue
                if re.match(r'^    description:', curr):
                    i += 1
                    continue
                if re.match(r'^    ai_context:', curr):
                    i += 1
                    continue
                preserved.append(curr)
                i += 1

            # Write preserved props first, then description + ai_context
            # Quote the value to handle special characters (em dash, colons, etc.)
            safe_desc = new_description.replace('"', '\\"')
            for prop in preserved:
                result.append(prop)
            result.append(f'    description: "{safe_desc}"')
            result.append(f'    ai_context: "{safe_desc}"')
        else:
            result.append(line)
            i += 1

    return '\n'.join(result)


def post_yaml_to_omni(file_key, updated_yaml, commit_message):
    """Push updated YAML directly to Omni's internal state."""
    headers = {
        "Authorization": f"Bearer {OMNI_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "fileName": file_key,
        "yaml": updated_yaml,
        "commitMessage": commit_message
    }
    resp = requests.post(f"{OMNI_BASE_URL}/yaml", headers=headers, json=payload)
    if not resp.ok:
        print(f"❌ POST /yaml failed {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
    return resp.json()


def main():
    if not OMNI_KEY:
        raise ValueError("OMNI_API_KEY environment variable not set")

    print("Loading manifest.json...")
    manifest = load_manifest(MANIFEST_PATH)
    dbt_models = parse_dbt_descriptions(manifest)
    print(f"Found {len(dbt_models)} dbt models\n")

    print("Fetching current YAML from Omni...")
    omni_files = get_omni_yaml()
    print(f"Got {len(omni_files)} files from Omni\n")

    # Track changes per file
    files_to_update = {}  # file_key → { yaml_content, changes[] }

    for model_name, dbt_columns in dbt_models.items():
        file_key = MODEL_TO_FILE_KEY.get(model_name)
        if not file_key:
            continue  # model not mapped to an Omni file

        yaml_content = omni_files.get(file_key)
        if not yaml_content:
            print(f"Skipping {model_name} — file key '{file_key}' not found in Omni")
            continue

        omni_fields = parse_omni_fields(yaml_content)

        for col_name, dbt_desc in dbt_columns.items():
            # Match dbt column → Omni field
            # Priority: sql: value match first, then direct key match
            matched_omni_key = None
            for omni_key, field_data in omni_fields.items():
                if '[' in omni_key:
                    continue  # skip time hierarchy fields like review_date[week]
                sql_val = field_data.get('sql')
                if sql_val and sql_val == col_name:
                    matched_omni_key = omni_key
                    break
                elif not sql_val and omni_key == col_name:
                    matched_omni_key = omni_key
                    break

            if not matched_omni_key:
                continue  # field not present in Omni, skip

            field_data = omni_fields[matched_omni_key]
            omni_desc = field_data.get('description')
            omni_ai = field_data.get('ai_context')

            # Skip only if both description and ai_context already match dbt
            if omni_desc == dbt_desc and omni_ai == dbt_desc:
                continue

            renamed_note = f" (Omni key: '{matched_omni_key}')" if matched_omni_key != col_name else ""
            print(f"DIFF: {model_name}.{col_name}{renamed_note}")
            if omni_desc != dbt_desc:
                print(f"  description  Omni: {omni_desc}")
                print(f"  description  dbt:  {dbt_desc}")
            if omni_ai != dbt_desc:
                print(f"  ai_context   Omni: {omni_ai}")
                print(f"  ai_context   dbt:  {dbt_desc}")

            if file_key not in files_to_update:
                files_to_update[file_key] = {
                    'yaml': yaml_content,
                    'changes': []
                }
            files_to_update[file_key]['changes'].append({
                'omni_field': matched_omni_key,
                'dbt_field': col_name,
                'dbt_desc': dbt_desc,
                'model': model_name,
            })

    if not files_to_update:
        print("\n✅ No differences found. Everything in sync.")
        return

    total_changes = sum(len(v['changes']) for v in files_to_update.values())
    print(f"\n{total_changes} difference(s) found across {len(files_to_update)} file(s).")
    print("Applying updates directly to Omni via API...\n")

    for file_key, file_data in files_to_update.items():
        updated_yaml = file_data['yaml']
        changes = file_data['changes']

        # Apply all field updates sequentially to the same YAML string
        for change in changes:
            updated_yaml = update_field_in_yaml(
                updated_yaml,
                change['omni_field'],
                change['dbt_desc']
            )

        # Build a descriptive commit message
        field_list = ", ".join(
            f"{c['model']}.{c['dbt_field']}" for c in changes
        )
        commit_msg = f"sync: restore dbt descriptions for {field_list}"

        result = post_yaml_to_omni(file_key, updated_yaml, commit_msg)
        if result.get('success'):
            print(f"✅ Updated {file_key}")
            for change in changes:
                renamed = f" → Omni field '{change['omni_field']}'" if change['omni_field'] != change['dbt_field'] else ""
                print(f"   • {change['model']}.{change['dbt_field']}{renamed}: \"{change['dbt_desc']}\"")
        else:
            print(f"❌ Failed to update {file_key}: {result}")

    print(f"\n✅ Done. {total_changes} field(s) synced to Omni.")


if __name__ == "__main__":
    main()
