import base64
import json
import os
import re
import yaml
import requests
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

MANIFEST_PATH = os.environ.get("MANIFEST_PATH", "target/manifest.json")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")

GITHUB_REPO        = "headout/omni-analytics"
GITHUB_BASE_BRANCH = "main"
GITHUB_API         = "https://api.github.com"

# Maps dbt model name → path of its Omni view YAML inside headout/omni-analytics
MODEL_TO_GITHUB_PATH = {
    "dbt_omni_sync": "omni/Headout Analytics/analytics_reporting/dbt_omni_sync.view.yaml",
}

# Everything below this marker in an Omni view file is auto-managed — never edit it
DBT_SECTION_MARKER = "#The info below was pulled from your dbt repository"

# Top-level YAML keys that are not field names
SKIP_KEYS = {"dimensions", "measures", "catalog", "schema", "table_name", "dbt", "version"}


# ── GitHub API helpers ────────────────────────────────────────────────────────

# Auth headers for all GitHub API calls
def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# Fetch a file from headout/omni-analytics; returns (yaml_str, file_sha)
def get_file_from_github(file_path, ref=None):
    encoded_path = requests.utils.quote(file_path, safe="/")
    url    = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{encoded_path}"
    params = {"ref": ref} if ref else {}
    resp   = requests.get(url, headers=_gh_headers(), params=params, timeout=30)
    resp.raise_for_status()
    data    = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


# Get HEAD commit SHA on main — used as the base when creating a new branch
def get_main_sha():
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/git/ref/heads/{GITHUB_BASE_BRANCH}"
    resp = requests.get(url, headers=_gh_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()["object"]["sha"]


# Create a new branch pointing at the given SHA
def create_branch(branch_name, sha):
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/git/refs"
    resp = requests.post(url, headers=_gh_headers(), json={
        "ref": f"refs/heads/{branch_name}",
        "sha": sha,
    }, timeout=30)
    resp.raise_for_status()
    print(f"✅ Created branch: {branch_name}")


# Push an updated file to a branch (file_sha must match the current SHA on main)
def push_file_to_branch(file_path, content_str, file_sha, branch_name, commit_message):
    encoded_path    = requests.utils.quote(file_path, safe="/")
    url             = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{encoded_path}"
    encoded_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
    resp = requests.put(url, headers=_gh_headers(), json={
        "message": commit_message,
        "content": encoded_content,
        "sha":     file_sha,
        "branch":  branch_name,
    }, timeout=30)
    resp.raise_for_status()
    print(f"✅ Pushed updated YAML to branch: {branch_name}")


# Open a PR from branch_name → main; returns the PR URL
def create_pull_request(branch_name, title, body):
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/pulls"
    resp = requests.post(url, headers=_gh_headers(), json={
        "title": title,
        "body":  body,
        "head":  branch_name,
        "base":  GITHUB_BASE_BRANCH,
    }, timeout=30)
    resp.raise_for_status()
    pr = resp.json()
    print(f"✅ PR created: {pr['html_url']}")
    return pr["html_url"]


# ── Open PR deduplication ─────────────────────────────────────────────────────

# Returns a set of (file_path, omni_field) pairs already covered by open dbt-sync PRs
def get_already_synced_fields(files_to_check, dbt_descriptions_by_file):
    already_synced = set()

    # List all open PRs with dbt-sync/ branches
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/pulls"
    resp = requests.get(url, headers=_gh_headers(), params={"state": "open", "per_page": 50}, timeout=30)
    resp.raise_for_status()
    open_prs = resp.json()

    sync_prs = [pr for pr in open_prs if pr["head"]["ref"].startswith("dbt-sync/")]
    if not sync_prs:
        return already_synced

    print(f"  Found {len(sync_prs)} open dbt-sync PR(s) — checking for duplicate changes...")

    for pr in sync_prs:
        branch = pr["head"]["ref"]
        pr_url = pr["html_url"]
        print(f"  Checking {pr_url} (branch: {branch})")

        for file_path, dbt_descs_by_omni_field in dbt_descriptions_by_file.items():
            try:
                yaml_on_branch, _ = get_file_from_github(file_path, ref=branch)
            except Exception:
                continue  # file might not exist on that branch yet

            fields_on_branch = parse_omni_fields(yaml_on_branch)

            for omni_field, expected_desc in dbt_descs_by_omni_field.items():
                fd = fields_on_branch.get(omni_field)
                if not fd:
                    continue
                # If both description and ai_context already match dbt on this branch,
                # the open PR already covers this change — skip it
                if fd.get("description") == expected_desc and fd.get("ai_context") == expected_desc:
                    print(f"    ↩ Skipping {omni_field} — already in open PR {pr_url}")
                    already_synced.add((file_path, omni_field))

    return already_synced


# ── dbt manifest parsing ──────────────────────────────────────────────────────

# Load dbt manifest.json from disk
def load_manifest(path):
    with open(path) as f:
        return json.load(f)


# Extract { model_name: { col_name: description } } from the manifest
def parse_dbt_models(manifest):
    result = {}
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") == "model":
            name = node["name"]
            cols = {
                col: data.get("description", "").strip()
                for col, data in node.get("columns", {}).items()
                if data.get("description", "").strip()
            }
            result[name] = cols
    return result


# ── Omni YAML parsing ─────────────────────────────────────────────────────────

# Parse an Omni view YAML and return { field_key: { description, ai_context, sql } }
def parse_omni_fields(yaml_content):
    # Strip dbt-managed section before parsing
    if DBT_SECTION_MARKER in yaml_content:
        yaml_content = yaml_content[:yaml_content.index(DBT_SECTION_MARKER)]

    data   = yaml.safe_load(yaml_content) or {}
    result = {}

    for section in ("dimensions", "measures"):
        for field_key, field_data in (data.get(section) or {}).items():
            if not isinstance(field_data, dict):
                continue
            if field_key in SKIP_KEYS or "[" in field_key:
                continue
            result[field_key] = {
                "description": field_data.get("description"),
                "ai_context":  field_data.get("ai_context"),
                "sql":         field_data.get("sql"),
            }

    return result


# Find the Omni field matching a dbt column — tries sql: value first, then direct name match
def match_omni_field(col_name, omni_fields):
    for omni_key, field_data in omni_fields.items():
        if "[" in omni_key:
            continue
        sql_val = field_data.get("sql")
        if (sql_val and sql_val == col_name) or (not sql_val and omni_key == col_name):
            return omni_key
    return None


# ── YAML update ───────────────────────────────────────────────────────────────

# Update description and ai_context for one field in the raw YAML string
# update_description=False preserves the existing description line when the value hasn't changed
def update_field_in_yaml(yaml_content, field_name, new_description, update_description=True):
    lines          = yaml_content.split("\n")
    result         = []
    i              = 0
    in_dbt_section = False

    while i < len(lines):
        line = lines[i]

        if DBT_SECTION_MARKER in line:
            in_dbt_section = True

        if not in_dbt_section and re.match(rf"^  {re.escape(field_name)}:$", line):
            result.append(line)
            i += 1

            preserved = []
            while i < len(lines):
                curr = lines[i]

                if re.match(r"^\w", curr) and curr.strip():
                    break
                if re.match(r"^  \w", curr) and not re.match(r"^    ", curr) and curr.strip():
                    break
                if curr.strip().startswith("#") and "dbt" in curr.lower():
                    i += 1
                    continue

                if re.match(r"^    description:", curr):
                    if update_description:
                        i += 1
                        # Skip continuation lines (wrapped multi-line value)
                        while i < len(lines) and re.match(r"^      ", lines[i]):
                            i += 1
                    else:
                        preserved.append(curr)
                        i += 1
                        while i < len(lines) and re.match(r"^      ", lines[i]):
                            preserved.append(lines[i])
                            i += 1
                    continue

                if re.match(r"^    ai_context:", curr):
                    # Always replace ai_context with the fresh value below
                    i += 1
                    while i < len(lines) and re.match(r"^      ", lines[i]):
                        i += 1
                    continue

                preserved.append(curr)
                i += 1

            safe_desc = new_description.replace('"', '\\"')
            result.extend(preserved)
            if update_description:
                result.append(f'    description: "{safe_desc}"')
            result.append(f'    ai_context: "{safe_desc}"')

        else:
            result.append(line)
            i += 1

    return "\n".join(result)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not GITHUB_TOKEN:
        raise ValueError(
            "GITHUB_TOKEN is not set.\n"
            "Run with: GITHUB_TOKEN=$(gh auth token) python sync.py"
        )

    print("Loading manifest.json...")
    manifest   = load_manifest(MANIFEST_PATH)
    dbt_models = parse_dbt_models(manifest)
    print(f"Found {len(dbt_models)} dbt models in manifest\n")

    files_to_update = {}

    for model_name, dbt_columns in dbt_models.items():
        github_path = MODEL_TO_GITHUB_PATH.get(model_name)
        if not github_path:
            continue

        print(f"Fetching {github_path} from GitHub...")
        yaml_content, file_sha = get_file_from_github(github_path)
        omni_fields = parse_omni_fields(yaml_content)
        print(f"  Parsed {len(omni_fields)} fields from Omni view\n")

        for col_name, dbt_desc in dbt_columns.items():
            matched = match_omni_field(col_name, omni_fields)
            if not matched:
                continue

            fd = omni_fields[matched]

            if fd.get("description") == dbt_desc and fd.get("ai_context") == dbt_desc:
                continue

            renamed = f" (Omni key: '{matched}')" if matched != col_name else ""
            print(f"  DIFF: {model_name}.{col_name}{renamed}")
            if fd.get("description") != dbt_desc:
                print(f"    description  now: {fd.get('description')}")
                print(f"    description  dbt: {dbt_desc}")
            if fd.get("ai_context") != dbt_desc:
                print(f"    ai_context   now: {fd.get('ai_context')}")
                print(f"    ai_context   dbt: {dbt_desc}")

            if github_path not in files_to_update:
                files_to_update[github_path] = {
                    "yaml":    yaml_content,
                    "sha":     file_sha,
                    "changes": [],
                }
            files_to_update[github_path]["changes"].append({
                "omni_field":         matched,
                "dbt_field":          col_name,
                "dbt_desc":           dbt_desc,
                "model":              model_name,
                "update_description": fd.get("description") != dbt_desc,
            })

    if not files_to_update:
        print("\n✅ No differences found. Everything in sync.")
        return

    # Build a lookup of { file_path: { omni_field: expected_dbt_desc } } for dedup check
    dbt_descs_by_file = {
        file_path: {c["omni_field"]: c["dbt_desc"] for c in file_data["changes"]}
        for file_path, file_data in files_to_update.items()
    }

    print("\nChecking open dbt-sync PRs for duplicate changes...")
    already_synced = get_already_synced_fields(files_to_update, dbt_descs_by_file)

    # Remove fields already covered by an open PR
    for file_path in list(files_to_update.keys()):
        files_to_update[file_path]["changes"] = [
            c for c in files_to_update[file_path]["changes"]
            if (file_path, c["omni_field"]) not in already_synced
        ]
        if not files_to_update[file_path]["changes"]:
            del files_to_update[file_path]

    if not files_to_update:
        print("\n✅ All differences are already covered by open PR(s). Nothing new to raise.")
        return

    total = sum(len(v["changes"]) for v in files_to_update.values())
    print(f"\n{total} new difference(s) to sync. Creating GitHub branch and PR...\n")

    timestamp   = datetime.now().strftime("%Y-%m-%d-%H-%M")
    branch_name = f"dbt-sync/{timestamp}"
    main_sha    = get_main_sha()
    create_branch(branch_name, main_sha)

    pr_body_lines = [
        "## dbt → Omni description sync",
        "",
        "This PR was auto-generated by the dbt-omni sync script.",
        "It adds `ai_context` to Omni view fields to match dbt `schema.yml`.",
        "",
        "### Changes",
        "",
    ]

    for file_path, file_data in files_to_update.items():
        updated_yaml = file_data["yaml"]
        changes      = file_data["changes"]

        for change in changes:
            updated_yaml = update_field_in_yaml(
                updated_yaml,
                change["omni_field"],
                change["dbt_desc"],
                update_description=change["update_description"],
            )

        field_list = ", ".join(f"{c['model']}.{c['dbt_field']}" for c in changes)
        commit_msg = f"sync: dbt descriptions for {field_list}"

        push_file_to_branch(
            file_path      = file_path,
            content_str    = updated_yaml,
            file_sha       = file_data["sha"],
            branch_name    = branch_name,
            commit_message = commit_msg,
        )

        pr_body_lines.append(f"**`{file_path}`** — {len(changes)} field(s):")
        for c in changes:
            renamed = f" → `{c['omni_field']}`" if c["omni_field"] != c["dbt_field"] else ""
            pr_body_lines.append(f"- `{c['model']}.{c['dbt_field']}`{renamed}: set ai_context")
        pr_body_lines.append("")

    pr_url = create_pull_request(
        branch_name = branch_name,
        title       = f"sync: dbt descriptions → Omni ({total} field(s))",
        body        = "\n".join(pr_body_lines),
    )

    print(f"\n✅ Done. {total} field(s) queued for sync.")
    print(f"   Review and merge the PR: {pr_url}")


if __name__ == "__main__":
    main()
