"""
dbt → Omni Sync Script (GitHub PR workflow)
============================================

WHAT THIS DOES:
  Reads column descriptions from the dbt manifest.json, compares them against
  the Omni view YAML files stored in headout/omni-analytics on GitHub, and
  raises a Pull Request with any differences.

  Specifically it syncs two Omni fields per column:
    - description  : the human-readable field description shown in Omni
    - ai_context   : the description used by Omni's AI query helper

  Both are set to the same value as the dbt column description.

WHY GITHUB API (not Omni API):
  The Headout Analytics Omni model has "Pull requests are required for model
  changes" enabled at the org level. This blocks direct POST /yaml calls with
  a 400 error. So instead we write directly to the GitHub repo that Omni is
  connected to (headout/omni-analytics), create a branch, and open a PR.
  When the PR is merged, Omni picks up the changes automatically.

USAGE:
  GITHUB_TOKEN=ghp_xxx \\
  MANIFEST_PATH=/path/to/manifest.json \\
  python sync.py

  On local machine you can use the gh CLI token:
  GITHUB_TOKEN=$(gh auth token) MANIFEST_PATH=... python sync.py

ENVIRONMENT VARIABLES:
  GITHUB_TOKEN   GitHub PAT with Contents + Pull requests read/write
                 on headout/omni-analytics
  MANIFEST_PATH  Path to dbt manifest.json (default: target/manifest.json)

TO ADD A NEW MODEL:
  1. Add an entry to MODEL_TO_GITHUB_PATH below
  2. Make sure the dbt model has column descriptions in schema.yml
  3. Run the script — it will find diffs and raise a PR automatically
"""

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

# The GitHub repo where Omni view YAML files are stored
GITHUB_REPO        = "headout/omni-analytics"
GITHUB_BASE_BRANCH = "main"
GITHUB_API         = "https://api.github.com"

# Maps dbt model name → full file path inside headout/omni-analytics
# Add new models here as you onboard them to the sync pipeline
MODEL_TO_GITHUB_PATH = {
    "dbt_omni_sync": "omni/Headout Analytics/analytics_reporting/dbt_omni_sync.view.yaml",
}

# Omni appends a dbt metadata block at the bottom of every view file.
# We stop parsing/editing when we hit this marker so we never touch
# the auto-managed dbt section.
DBT_SECTION_MARKER = "#The info below was pulled from your dbt repository"

# Top-level YAML keys that are not field names — skip these when parsing fields
SKIP_KEYS = {"dimensions", "measures", "catalog", "schema", "table_name", "dbt", "version"}


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _gh_headers():
    """Return auth headers for every GitHub API request."""
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_file_from_github(file_path):
    """
    Fetch a file from headout/omni-analytics on the main branch.
    Returns (yaml_str, file_sha).

    file_sha is needed later when pushing updates — GitHub requires it
    to confirm you're editing the latest version of the file.
    """
    encoded_path = requests.utils.quote(file_path, safe="/")
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{encoded_path}"
    resp = requests.get(url, headers=_gh_headers(), timeout=30)
    resp.raise_for_status()
    data    = resp.json()
    # GitHub returns file content as base64-encoded string
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def get_main_sha():
    """
    Get the current HEAD commit SHA on main.
    Used as the base when creating a new branch so the branch starts
    from the latest state of main.
    """
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/git/ref/heads/{GITHUB_BASE_BRANCH}"
    resp = requests.get(url, headers=_gh_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()["object"]["sha"]


def create_branch(branch_name, sha):
    """
    Create a new branch on headout/omni-analytics pointing at the given SHA.
    Branch name format: dbt-sync/YYYY-MM-DD-HH-MM (one branch per DAG run).
    """
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/git/refs"
    resp = requests.post(url, headers=_gh_headers(), json={
        "ref": f"refs/heads/{branch_name}",
        "sha": sha,
    }, timeout=30)
    resp.raise_for_status()
    print(f"✅ Created branch: {branch_name}")


def push_file_to_branch(file_path, content_str, file_sha, branch_name, commit_message):
    """
    Push an updated file to a branch via the GitHub Contents API.

    content_str  the new file content as a plain string
    file_sha     SHA of the current file on main (required by GitHub API)
    """
    encoded_path    = requests.utils.quote(file_path, safe="/")
    url             = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{encoded_path}"
    # GitHub Contents API requires base64-encoded content
    encoded_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
    resp = requests.put(url, headers=_gh_headers(), json={
        "message": commit_message,
        "content": encoded_content,
        "sha":     file_sha,    # tells GitHub which version we're updating
        "branch":  branch_name,
    }, timeout=30)
    resp.raise_for_status()
    print(f"✅ Pushed updated YAML to branch: {branch_name}")


def create_pull_request(branch_name, title, body):
    """
    Open a PR from branch_name → main on headout/omni-analytics.
    Returns the PR URL so it can be logged or stored in XCom.
    """
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


# ── dbt manifest parsing ──────────────────────────────────────────────────────

def load_manifest(path):
    """Load dbt manifest.json from the given file path."""
    with open(path) as f:
        return json.load(f)


def parse_dbt_models(manifest):
    """
    Extract column descriptions from the dbt manifest.

    Returns:
        { model_name: { col_name: description } }
        Only includes columns that have a non-empty description.

    The manifest is produced by `dbt compile` or `dbt run` and contains
    metadata about every model including column-level documentation
    written in schema.yml.
    """
    result = {}
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") == "model":
            name = node["name"]
            cols = {
                col: data.get("description", "").strip()
                for col, data in node.get("columns", {}).items()
                if data.get("description", "").strip()  # skip undocumented columns
            }
            result[name] = cols
    return result


# ── Omni YAML parsing ─────────────────────────────────────────────────────────

def parse_omni_fields(yaml_content):
    """
    Parse an Omni view YAML file and extract all fields with their metadata.

    Returns:
        { field_key: { description, ai_context, sql } }

    Notes:
        - Everything below DBT_SECTION_MARKER is auto-managed by Omni and
          is stripped before parsing so we only read user-managed fields.
        - sql: is the underlying column name if the Omni field was renamed
          (e.g. Omni field "booking_ref" with sql: "booking_id"). Used for
          matching dbt columns to Omni fields when names differ.
        - Time hierarchy fields like "booking_date[week]" are skipped since
          they are auto-generated by Omni and not editable.
    """
    # Strip the dbt-managed section at the bottom before parsing
    if DBT_SECTION_MARKER in yaml_content:
        yaml_content = yaml_content[:yaml_content.index(DBT_SECTION_MARKER)]

    data   = yaml.safe_load(yaml_content) or {}
    result = {}

    # Parse both dimensions and measures sections
    for section in ("dimensions", "measures"):
        for field_key, field_data in (data.get(section) or {}).items():
            if not isinstance(field_data, dict):
                continue
            # Skip section headers and time hierarchy auto-fields
            if field_key in SKIP_KEYS or "[" in field_key:
                continue
            result[field_key] = {
                "description": field_data.get("description"),
                "ai_context":  field_data.get("ai_context"),
                "sql":         field_data.get("sql"),
            }

    return result


def match_omni_field(col_name, omni_fields):
    """
    Find the Omni field that corresponds to a given dbt column name.

    Matching priority:
      1. sql: value match — Omni field was renamed but sql: points to the
         original column (e.g. dbt col "booking_id" → Omni field "booking_ref"
         with sql: "booking_id")
      2. Direct key match — Omni field name equals dbt column name

    Returns the Omni field key, or None if no match found.
    Fields with "[" in the name (time hierarchies) are always skipped.
    """
    for omni_key, field_data in omni_fields.items():
        if "[" in omni_key:
            continue  # skip auto-generated time hierarchy fields
        sql_val = field_data.get("sql")
        if (sql_val and sql_val == col_name) or (not sql_val and omni_key == col_name):
            return omni_key
    return None


# ── YAML update ───────────────────────────────────────────────────────────────

def update_field_in_yaml(yaml_content, field_name, new_description, update_description=True):
    """
    Insert or update description and ai_context for a single field in the
    raw YAML string, preserving all other field properties exactly as-is.

    Args:
        yaml_content        The full YAML file as a string
        field_name          The Omni field key to update (e.g. "booking_id")
        new_description     The dbt description to write
        update_description  If True, rewrite the description line (value changed).
                            If False, preserve existing description and only
                            add/update ai_context. This avoids touching lines
                            that haven't changed (e.g. no quote reformatting).

    Why raw string manipulation instead of yaml.safe_load + yaml.dump?
        yaml.dump does not preserve comments, field ordering, or formatting.
        Since Omni's view files have custom formatting and a dbt section with
        comments at the bottom, we manipulate the raw string directly to keep
        the diff minimal and reviewable.

    Multi-line description handling:
        YAML allows long values to wrap across lines:
            description: Some long text that wraps
              onto the next line here.
        The continuation line (6+ spaces) is detected and handled correctly
        whether we're preserving or replacing the description.
    """
    lines          = yaml_content.split("\n")
    result         = []
    i              = 0
    in_dbt_section = False

    while i < len(lines):
        line = lines[i]

        # Once we hit the dbt section marker, stop editing — everything below
        # is auto-managed by Omni's dbt integration
        if DBT_SECTION_MARKER in line:
            in_dbt_section = True

        # Match field name at exactly 2-space indent (e.g. "  booking_id:")
        if not in_dbt_section and re.match(rf"^  {re.escape(field_name)}:$", line):
            result.append(line)
            i += 1

            # Collect all existing properties for this field
            preserved = []
            while i < len(lines):
                curr = lines[i]

                # Stop when we reach the next top-level section (0-indent)
                if re.match(r"^\w", curr) and curr.strip():
                    break
                # Stop when we reach the next sibling field (2-indent, not 4+)
                if re.match(r"^  \w", curr) and not re.match(r"^    ", curr) and curr.strip():
                    break
                # Skip any leftover dbt comment markers
                if curr.strip().startswith("#") and "dbt" in curr.lower():
                    i += 1
                    continue

                if re.match(r"^    description:", curr):
                    if update_description:
                        # Value changed — skip this line and write a fresh one below
                        i += 1
                        # Also skip any continuation lines (6+ spaces = wrapped value)
                        while i < len(lines) and re.match(r"^      ", lines[i]):
                            i += 1
                    else:
                        # Value unchanged — preserve the original line exactly
                        preserved.append(curr)
                        i += 1
                        # Also preserve continuation lines as-is
                        while i < len(lines) and re.match(r"^      ", lines[i]):
                            preserved.append(lines[i])
                            i += 1
                    continue

                if re.match(r"^    ai_context:", curr):
                    # Always skip old ai_context — we write a fresh one below
                    i += 1
                    while i < len(lines) and re.match(r"^      ", lines[i]):
                        i += 1
                    continue

                # Everything else (format, sql, aggregate_type, etc.) is preserved
                preserved.append(curr)
                i += 1

            # Write out: preserved properties, then description (if changed), then ai_context
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
            "Run with: GITHUB_TOKEN=$(gh auth token) python sync.py\n"
            "Or export GITHUB_TOKEN=ghp_xxx before running."
        )

    # Step 1: Load dbt manifest and extract column descriptions
    print("Loading manifest.json...")
    manifest   = load_manifest(MANIFEST_PATH)
    dbt_models = parse_dbt_models(manifest)
    print(f"Found {len(dbt_models)} dbt models in manifest\n")

    # Step 2: For each mapped model, fetch its Omni view from GitHub and compare
    files_to_update = {}  # file_path → { yaml_str, file_sha, changes[] }

    for model_name, dbt_columns in dbt_models.items():
        github_path = MODEL_TO_GITHUB_PATH.get(model_name)
        if not github_path:
            continue  # model not mapped to an Omni view, skip

        print(f"Fetching {github_path} from GitHub...")
        yaml_content, file_sha = get_file_from_github(github_path)
        omni_fields = parse_omni_fields(yaml_content)
        print(f"  Parsed {len(omni_fields)} fields from Omni view\n")

        for col_name, dbt_desc in dbt_columns.items():
            # Find the matching Omni field (by sql: value or direct name match)
            matched = match_omni_field(col_name, omni_fields)
            if not matched:
                continue  # column not in Omni yet, nothing to sync

            fd = omni_fields[matched]

            # Skip if both description AND ai_context already match dbt
            if fd.get("description") == dbt_desc and fd.get("ai_context") == dbt_desc:
                continue

            # Log what's different
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
                # Only rewrite description if the value actually changed
                # (avoids unnecessary reformatting of unchanged lines)
                "update_description": fd.get("description") != dbt_desc,
            })

    # Step 3: If no diffs found, we're done
    if not files_to_update:
        print("\n✅ No differences found. Everything in sync.")
        return

    total = sum(len(v["changes"]) for v in files_to_update.values())
    print(f"\n{total} difference(s) found across {len(files_to_update)} file(s).")
    print("Creating GitHub branch and PR...\n")

    # Step 4: Create a single branch for this run (covers all changed files)
    timestamp   = datetime.now().strftime("%Y-%m-%d-%H-%M")
    branch_name = f"dbt-sync/{timestamp}"
    main_sha    = get_main_sha()
    create_branch(branch_name, main_sha)

    # Build PR description listing all changed fields
    pr_body_lines = [
        "## dbt → Omni description sync",
        "",
        "This PR was auto-generated by the dbt-omni sync script.",
        "It adds `ai_context` to Omni view fields to match dbt `schema.yml`.",
        "",
        "### Changes",
        "",
    ]

    # Step 5: Apply all changes to the YAML and push each file to the branch
    for file_path, file_data in files_to_update.items():
        updated_yaml = file_data["yaml"]
        changes      = file_data["changes"]

        # Apply all field updates sequentially to the same YAML string
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

        # Add this file's changes to the PR body
        pr_body_lines.append(f"**`{file_path}`** — {len(changes)} field(s):")
        for c in changes:
            renamed = f" → `{c['omni_field']}`" if c["omni_field"] != c["dbt_field"] else ""
            pr_body_lines.append(f"- `{c['model']}.{c['dbt_field']}`{renamed}: set ai_context")
        pr_body_lines.append("")

    # Step 6: Open a PR for review
    pr_url = create_pull_request(
        branch_name = branch_name,
        title       = f"sync: dbt descriptions → Omni ({total} field(s))",
        body        = "\n".join(pr_body_lines),
    )

    print(f"\n✅ Done. {total} field(s) queued for sync.")
    print(f"   Review and merge the PR: {pr_url}")


if __name__ == "__main__":
    main()
