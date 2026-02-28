"""Validate and maintain EXMODZ mod packages and related metadata.

This script verifies each mod under mods/ against its EXMODZ package, compares
modified JSON files against the latest local baseline under .icarus-data/, and
can optionally launch Notepad++ Compare for manual review.

After successful validation, write mode updates:
- EXMOD metadata fields inside each .EXMODZ archive
- Week Compatibility in each mod README
- Date Updated in each mod README when that mod changed in this run
- Top-level modinfo.json entries for EXMODZ distribution
"""

from __future__ import annotations

import argparse
import copy
import difflib
from datetime import datetime
import json
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
import re


ROOT_DIR = Path(__file__).resolve().parent.parent
MODS_DIR = ROOT_DIR / "mods"
MODS_EXMODZ_DIR = ROOT_DIR / "mods-exmodz"
DATA_ROOT_DIR = ROOT_DIR / ".icarus-data"
METADATA_FILE = ROOT_DIR / "metadata.json"
MODINFO_FILE = ROOT_DIR / "modinfo.json"

GITHUB_OWNER = "TheLysdexicOne"
GITHUB_REPO = "Icarus-Mods"
GITHUB_BRANCH = "main"

REQUIRED_README_FIELDS = (
    "name",
    "mod_slug",
    "version",
    "description",
    "image url",
    "readme url",
    "week compatibility",
)


@dataclass
class ModContext:
    slug: str
    folder: Path
    readme_path: Path
    exmodz_path: Path
    mod_json_files: list[Path]
    readme: dict[str, str]


@dataclass
class ArchiveContext:
    exmod_entry_name: str
    exmod_payload: dict


def parse_version(version_str: str) -> list[int]:
    """Parse sequence strings like '221.10' for numeric sorting."""
    parts: list[int] = []
    for part in version_str.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return parts


def load_json_file(file_path: Path) -> tuple[object | None, str | None]:
    """Load JSON from disk and return (data, error)."""
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            return json.load(handle), None
    except FileNotFoundError:
        return None, f"File not found: {file_path}"
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON in {file_path}: {exc}"
    except OSError as exc:
        return None, f"Could not read {file_path}: {exc}"


def save_json_file(file_path: Path, payload: object) -> tuple[bool, str | None]:
    """Save JSON with stable formatting."""
    try:
        with file_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return True, None
    except OSError as exc:
        return False, f"Could not write {file_path}: {exc}"


def resolve_baseline_folder() -> tuple[Path | None, str | None, str | None]:
    """Resolve baseline data folder from metadata then numeric fallback."""
    if not DATA_ROOT_DIR.exists():
        return None, None, f"Baseline root not found: {DATA_ROOT_DIR}"

    metadata, error = load_json_file(METADATA_FILE)
    if error:
        return None, None, error

    latest_sequence: str | None = None
    if isinstance(metadata, dict):
        latest_candidate = metadata.get("latest_data_folder")
        if isinstance(latest_candidate, str) and latest_candidate.strip():
            latest_sequence = latest_candidate.strip()

    if latest_sequence:
        latest_data_path = DATA_ROOT_DIR / latest_sequence / "data"
        if latest_data_path.exists():
            return latest_data_path, latest_sequence, None

    sequences: list[str] = []
    for child in DATA_ROOT_DIR.iterdir():
        if child.is_dir() and child.name.replace(".", "").isdigit() and (child / "data").exists():
            sequences.append(child.name)

    if not sequences:
        return None, None, "No valid baseline sequence folders were found in .icarus-data/"

    sequences.sort(key=parse_version, reverse=True)
    selected_sequence = sequences[0]
    return DATA_ROOT_DIR / selected_sequence / "data", selected_sequence, None


def discover_mods() -> list[str]:
    """Return sorted mod slugs from mods/ where data and README exist."""
    if not MODS_DIR.exists():
        return []

    mod_slugs: list[str] = []
    for child in MODS_DIR.iterdir():
        if not child.is_dir():
            continue
        if (child / "data").exists() and (child / "README.md").exists():
            mod_slugs.append(child.name)
    mod_slugs.sort()
    return mod_slugs


def parse_readme_fields(readme_path: Path) -> tuple[dict[str, str] | None, str | None]:
    """Parse '**Key**: value' pairs from mod README.md."""
    try:
        content = readme_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"Could not read {readme_path}: {exc}"

    result: dict[str, str] = {}
    pattern = re.compile(r"^\*\*(?P<key>[^*]+)\*\*:[ \t]*(?P<value>.*)$", re.MULTILINE)

    for match in pattern.finditer(content):
        key = match.group("key").strip().lower()
        value = match.group("value").strip()
        if value.startswith("`") and value.endswith("`") and len(value) >= 2:
            value = value[1:-1].strip()
        result[key] = value

    missing = [field for field in REQUIRED_README_FIELDS if field not in result]
    if missing:
        return None, f"Missing README field(s) in {readme_path}: {', '.join(missing)}"

    return result, None


def find_mod_json_files(mod_slug: str) -> list[Path]:
    """Find all mod JSON files excluding _exmod sidecars."""
    data_dir = MODS_DIR / mod_slug / "data"
    files = [path for path in data_dir.rglob("*.json") if not path.name.endswith("_exmod.json")]
    files.sort()
    return files


def to_current_file(relative_path: str) -> str:
    """Convert mod relative path to EXMOD CurrentFile format."""
    if "/" not in relative_path:
        return relative_path
    category, filename = relative_path.split("/", 1)
    return f"{category}-{filename}"


def from_current_file(current_file: str) -> str | None:
    """Convert EXMOD CurrentFile format to mod relative path."""
    if current_file == "EndOfMod":
        return None
    if "-" not in current_file:
        return None
    category, filename = current_file.split("-", 1)
    if not category or not filename:
        return None
    return f"{category}/{filename}"


def read_exmod_archive(exmodz_path: Path) -> tuple[ArchiveContext | None, str | None]:
    """Read EXMOD JSON payload from EXMODZ archive."""
    if not exmodz_path.exists():
        return None, f"Missing EXMODZ archive: {exmodz_path}"

    try:
        with zipfile.ZipFile(exmodz_path, "r") as archive:
            candidates = [
                name
                for name in archive.namelist()
                if name.lower().startswith("extracted mods/") and name.lower().endswith(".exmod")
            ]
            if not candidates:
                return None, f"No EXMOD entry found in archive: {exmodz_path}"

            entry_name = sorted(candidates)[0]
            try:
                payload = json.loads(archive.read(entry_name).decode("utf-8"))
            except json.JSONDecodeError as exc:
                return None, f"Invalid EXMOD JSON in {exmodz_path}: {exc}"

            if not isinstance(payload, dict):
                return None, f"Unexpected EXMOD structure in {exmodz_path}: top-level is not object"

            return ArchiveContext(exmod_entry_name=entry_name, exmod_payload=payload), None
    except zipfile.BadZipFile as exc:
        return None, f"Invalid EXMODZ zip archive {exmodz_path}: {exc}"
    except OSError as exc:
        return None, f"Could not read {exmodz_path}: {exc}"


def validate_exmod_payload(payload: dict, expected_slug: str) -> list[str]:
    """Validate basic EXMOD schema and required fields."""
    issues: list[str] = []
    required = ["name", "author", "version", "fileName", "Rows"]
    for key in required:
        if key not in payload:
            issues.append(f"Missing EXMOD key '{key}'")

    rows = payload.get("Rows")
    if not isinstance(rows, list):
        issues.append("EXMOD Rows is not a list")
    else:
        if not rows:
            issues.append("EXMOD Rows is empty")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                issues.append(f"EXMOD Rows[{index}] is not an object")
                continue
            if "CurrentFile" not in row:
                issues.append(f"EXMOD Rows[{index}] missing CurrentFile")
                continue
            current_file = row.get("CurrentFile")
            if not isinstance(current_file, str):
                issues.append(f"EXMOD Rows[{index}].CurrentFile is not a string")

    file_name = payload.get("fileName")
    if isinstance(file_name, str) and file_name != expected_slug:
        issues.append(f"EXMOD fileName '{file_name}' does not match mod slug '{expected_slug}'")

    return issues


def compare_rows_to_mod_files(
    rows: list[dict], relative_mod_files: list[str]
) -> tuple[list[str], list[str]]:
    """Return (missing_in_exmod, extra_in_exmod) using normalized relative paths."""
    exmod_paths: set[str] = set()
    for row in rows:
        current_file = row.get("CurrentFile")
        if not isinstance(current_file, str):
            continue
        normalized = from_current_file(current_file)
        if normalized:
            exmod_paths.add(normalized)

    mod_paths = set(relative_mod_files)
    missing = sorted(mod_paths - exmod_paths)
    extra = sorted(exmod_paths - mod_paths)
    return missing, extra


def format_json_for_diff(payload: object) -> list[str]:
    """Render JSON object into line list suitable for unified diff."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).splitlines()


def is_rows_list(value: object) -> bool:
    """Check if list is DataTable Rows-like: list of dicts with Name key."""
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict) or "Name" not in item:
            return False
    return True


def merge_rows(mod_rows: list, baseline_rows: list) -> list:
    """Apply mod row patches onto baseline rows by Name."""
    patched_rows = copy.deepcopy(baseline_rows)
    index_by_name: dict[str, int] = {}

    for index, row in enumerate(patched_rows):
        if isinstance(row, dict):
            row_name = row.get("Name")
            if isinstance(row_name, str) and row_name not in index_by_name:
                index_by_name[row_name] = index

    for mod_row in mod_rows:
        if not isinstance(mod_row, dict):
            continue

        row_name = mod_row.get("Name")
        if not isinstance(row_name, str):
            continue

        baseline_index = index_by_name.get(row_name)
        if baseline_index is None:
            patched_rows.append(copy.deepcopy(mod_row))
            index_by_name[row_name] = len(patched_rows) - 1
            continue

        baseline_row = patched_rows[baseline_index]
        if not isinstance(baseline_row, dict):
            patched_rows[baseline_index] = copy.deepcopy(mod_row)
            continue

        patched_rows[baseline_index] = apply_mod_to_baseline(mod_row, baseline_row)

    return patched_rows


def apply_mod_to_baseline(
    mod_value: object, baseline_value: object, key: str | None = None
) -> object:
    """Apply a minimized mod payload onto baseline content."""
    if isinstance(mod_value, dict) and isinstance(baseline_value, dict):
        patched = copy.deepcopy(baseline_value)
        for nested_key, nested_mod_value in mod_value.items():
            if nested_key in baseline_value:
                patched[nested_key] = apply_mod_to_baseline(
                    nested_mod_value,
                    baseline_value[nested_key],
                    key=nested_key,
                )
            else:
                patched[nested_key] = copy.deepcopy(nested_mod_value)
        return patched

    if isinstance(mod_value, list) and isinstance(baseline_value, list):
        if key == "Rows" and is_rows_list(mod_value) and is_rows_list(baseline_value):
            return merge_rows(mod_value, baseline_value)
        return copy.deepcopy(mod_value)

    return copy.deepcopy(mod_value)


def build_section_compare_payloads(
    baseline_data: object, patched_data: object
) -> tuple[object, object]:
    """Build compare payloads focused on changed sections.

    For Rows lists, include full row dictionaries for changed Name entries.
    For other keys, include full values for changed keys only.
    """
    baseline_section, patched_section, _ = _build_section_compare_recursive(
        baseline_data,
        patched_data,
        key=None,
    )
    return baseline_section, patched_section


def _build_section_compare_recursive(
    baseline_value: object,
    patched_value: object,
    key: str | None,
) -> tuple[object, object, bool]:
    if baseline_value == patched_value:
        return {}, {}, False

    if (
        key == "Rows"
        and isinstance(baseline_value, list)
        and isinstance(patched_value, list)
        and is_rows_list(baseline_value)
        and is_rows_list(patched_value)
    ):
        baseline_rows, patched_rows, changed = _build_rows_section_compare(
            baseline_value,
            patched_value,
        )
        if not changed:
            return {}, {}, False
        return baseline_rows, patched_rows, True

    if isinstance(baseline_value, dict) and isinstance(patched_value, dict):
        baseline_result: dict[str, object] = {}
        patched_result: dict[str, object] = {}
        changed_any = False

        keys = sorted(set(baseline_value.keys()) | set(patched_value.keys()))
        for nested_key in keys:
            if nested_key in baseline_value and nested_key in patched_value:
                nested_baseline, nested_patched, nested_changed = _build_section_compare_recursive(
                    baseline_value[nested_key],
                    patched_value[nested_key],
                    key=nested_key,
                )
                if nested_changed:
                    baseline_result[nested_key] = nested_baseline
                    patched_result[nested_key] = nested_patched
                    changed_any = True
            elif nested_key in baseline_value:
                baseline_result[nested_key] = baseline_value[nested_key]
                changed_any = True
            else:
                patched_result[nested_key] = patched_value[nested_key]
                changed_any = True

        return baseline_result, patched_result, changed_any

    if isinstance(baseline_value, list) and isinstance(patched_value, list):
        return copy.deepcopy(baseline_value), copy.deepcopy(patched_value), True

    return copy.deepcopy(baseline_value), copy.deepcopy(patched_value), True


def _build_rows_section_compare(
    baseline_rows: list[dict],
    patched_rows: list[dict],
) -> tuple[list[dict], list[dict], bool]:
    baseline_index: dict[str, dict] = {
        row["Name"]: row
        for row in baseline_rows
        if isinstance(row, dict) and isinstance(row.get("Name"), str)
    }
    patched_index: dict[str, dict] = {
        row["Name"]: row
        for row in patched_rows
        if isinstance(row, dict) and isinstance(row.get("Name"), str)
    }

    changed_names: list[str] = []
    for row in patched_rows:
        if not isinstance(row, dict):
            continue
        row_name = row.get("Name")
        if not isinstance(row_name, str):
            continue
        baseline_row = baseline_index.get(row_name)
        if baseline_row != row:
            changed_names.append(row_name)

    baseline_result: list[dict] = []
    patched_result: list[dict] = []

    for row_name in changed_names:
        baseline_row = baseline_index.get(row_name)
        patched_row = patched_index.get(row_name)
        if baseline_row is not None:
            baseline_result.append(copy.deepcopy(baseline_row))
        if patched_row is not None:
            patched_result.append(copy.deepcopy(patched_row))

    return baseline_result, patched_result, bool(changed_names)


def write_temp_json_file(payload: object) -> tuple[Path | None, str | None]:
    """Write payload to a temp JSON file and return path."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            return Path(handle.name), None
    except OSError as exc:
        return None, f"Failed to create temp patched JSON file: {exc}"


def summarize_diff(
    mod_data: object, baseline_data: object, label: str, max_lines: int = 24
) -> list[str]:
    """Return trimmed unified diff lines for console summary."""
    diff = list(
        difflib.unified_diff(
            format_json_for_diff(baseline_data),
            format_json_for_diff(mod_data),
            fromfile=f"baseline/{label}",
            tofile=f"mod/{label}",
            lineterm="",
        )
    )
    if len(diff) <= max_lines:
        return diff
    trimmed = diff[:max_lines]
    trimmed.append(f"... ({len(diff) - max_lines} more line(s) omitted)")
    return trimmed


def run_compare_command(baseline_file: Path, mod_file: Path) -> tuple[bool, str | None]:
    """Launch Notepad++ compare plugin command for manual review."""
    command = [
        "notepad++",
        "-pluginMessage=compare",
        str(mod_file),
        str(baseline_file),
    ]
    try:
        subprocess.Popen(command)
        return True, None
    except FileNotFoundError:
        return False, "notepad++ command was not found in PATH"
    except OSError as exc:
        return False, f"Failed to launch Notepad++ compare: {exc}"


def wait_for_user_confirmation(relative_path: str) -> bool | None:
    """Pause flow for manual validation.

    Returns:
        True to continue, False to quit, None to relaunch compare.
    """
    while True:
        response = (
            input(
                f"Review compare for {relative_path}. [y]es continue / [r]eopen compare / [q]uit: "
            )
            .strip()
            .lower()
        )
        if response == "y":
            return True
        if response == "r":
            return None
        if response == "q":
            return False
        print("Please enter 'y', 'r', or 'q'.")


def update_exmod_metadata(
    payload: dict, readme_fields: dict[str, str], author: str, week: str
) -> bool:
    """Apply canonical metadata updates onto EXMOD payload; return changed flag."""
    desired = {
        "name": readme_fields["name"],
        "author": author,
        "version": readme_fields["version"],
        "fileName": readme_fields["mod_slug"],
        "imageURL": readme_fields["image url"],
        "readmeURL": readme_fields["readme url"],
        "description": readme_fields["description"],
        "week": week,
    }

    changed = False
    for key, value in desired.items():
        current = payload.get(key)
        if current != value:
            payload[key] = value
            changed = True
    return changed


def rewrite_exmodz_entry(
    exmodz_path: Path, entry_name: str, payload: dict
) -> tuple[bool, str | None]:
    """Rewrite a single EXMOD JSON entry in-place while preserving other zip entries."""
    new_bytes = json.dumps(payload, indent=4, ensure_ascii=False).encode("utf-8")

    try:
        with zipfile.ZipFile(exmodz_path, "r") as source:
            infos = source.infolist()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".EXMODZ") as temp:
                temp_path = Path(temp.name)

            with zipfile.ZipFile(temp_path, "w") as target:
                for info in infos:
                    original_data = source.read(info.filename)
                    data = new_bytes if info.filename == entry_name else original_data
                    target.writestr(info, data)

        temp_path.replace(exmodz_path)
        return True, None
    except OSError as exc:
        return False, f"Could not rewrite {exmodz_path}: {exc}"
    except zipfile.BadZipFile as exc:
        return False, f"Invalid EXMODZ zip archive {exmodz_path}: {exc}"


def update_readme_field(
    readme_path: Path,
    field_label: str,
    value: str,
    *,
    append_if_missing: bool,
    append_with_backticks: bool,
) -> tuple[bool, bool, str | None]:
    """Update a README metadata field line.

    Field format is expected to be: **Field Label**: value
    Backticks are preserved when the existing value is wrapped.

    Returns:
        (success, changed, error)
    """
    try:
        content = readme_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, False, f"Could not read {readme_path}: {exc}"

    escaped_label = re.escape(field_label)
    pattern = re.compile(rf"^(\*\*{escaped_label}\*\*:\s*)(`?)([^`\n]*?)(`?)\s*$", re.MULTILINE)

    match = pattern.search(content)
    if not match:
        if not append_if_missing:
            return False, False, f"{field_label} field not found in {readme_path}"

        appended_value = f"`{value}`" if append_with_backticks else value
        prefix = "" if not content or content.endswith("\n") else "\n"
        new_content = f"{content}{prefix}**{field_label}**: {appended_value}\n"
        changed = new_content != content
        if not changed:
            return True, False, None
        try:
            readme_path.write_text(new_content, encoding="utf-8")
            return True, True, None
        except OSError as exc:
            return False, False, f"Could not write {readme_path}: {exc}"

    prefix, left_tick, _, right_tick = match.groups()
    if not left_tick and not right_tick:
        replacement = f"{prefix}{value}"
    else:
        replacement = f"{prefix}`{value}`"

    new_content = pattern.sub(replacement, content, count=1)
    changed = new_content != content
    if not changed:
        return True, False, None

    try:
        readme_path.write_text(new_content, encoding="utf-8")
        return True, True, None
    except OSError as exc:
        return False, False, f"Could not write {readme_path}: {exc}"


def update_readme_week(readme_path: Path, week: str) -> tuple[bool, bool, str | None]:
    """Update '**Week Compatibility**' value in README."""
    return update_readme_field(
        readme_path,
        "Week Compatibility",
        week,
        append_if_missing=False,
        append_with_backticks=False,
    )


def format_readme_date_updated() -> str:
    """Format Date Updated using project README convention."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S EST")


def build_modinfo_entry(slug: str, readme_fields: dict[str, str], author: str) -> dict:
    """Build one modinfo.json entry for EXMODZ distribution."""
    exmodz_url = (
        f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
        f"{GITHUB_BRANCH}/mods-exmodz/{slug}.EXMODZ"
    )

    return {
        "name": readme_fields["name"],
        "author": author,
        "version": readme_fields["version"],
        "compatibility": "All",
        "description": readme_fields["description"],
        "files": {
            "exmodz": exmodz_url,
        },
        "imageURL": readme_fields["image url"],
        "readmeURL": readme_fields["readme url"],
    }


def build_mod_context(mod_slug: str) -> tuple[ModContext | None, list[str]]:
    """Build context for one mod and return (context, issues)."""
    issues: list[str] = []
    folder = MODS_DIR / mod_slug
    readme_path = folder / "README.md"
    exmodz_path = MODS_EXMODZ_DIR / f"{mod_slug}.EXMODZ"
    mod_json_files = find_mod_json_files(mod_slug)

    if not mod_json_files:
        issues.append(f"No JSON files found under {folder / 'data'}")

    readme_fields, readme_error = parse_readme_fields(readme_path)
    if readme_error:
        issues.append(readme_error)
        return None, issues

    assert readme_fields is not None
    readme_slug = readme_fields.get("mod_slug", "")
    if readme_slug != mod_slug:
        issues.append(f"README mod_slug '{readme_slug}' does not match folder '{mod_slug}'")

    context = ModContext(
        slug=mod_slug,
        folder=folder,
        readme_path=readme_path,
        exmodz_path=exmodz_path,
        mod_json_files=mod_json_files,
        readme=readme_fields,
    )
    return context, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and maintain Icarus EXMODZ packages")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply updates to EXMODZ, README week/date values, and modinfo.json",
    )
    parser.add_argument(
        "--with-compare",
        action="store_true",
        help="Launch Notepad++ compare for each changed file versus baseline",
    )
    parser.add_argument(
        "--pause",
        action="store_true",
        help="When compare is launched, wait for manual confirmation before continuing",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("Icarus Mod Maintenance")
    print("=" * 70)
    print(f"Mode: {'WRITE' if args.write else 'DRY RUN'}")

    metadata_data, metadata_error = load_json_file(METADATA_FILE)
    if metadata_error:
        print(f"Error: {metadata_error}", file=sys.stderr)
        return 1
    if not isinstance(metadata_data, dict):
        print(f"Error: Unexpected metadata format in {METADATA_FILE}", file=sys.stderr)
        return 1

    author = str(metadata_data.get("author", "")).strip() or "Unknown"
    week = str(metadata_data.get("week", "")).strip()
    if not week:
        print("Error: metadata.json week is missing", file=sys.stderr)
        return 1

    baseline_data_path, baseline_sequence, baseline_error = resolve_baseline_folder()
    if baseline_error:
        print(f"Error: {baseline_error}", file=sys.stderr)
        return 1
    assert baseline_data_path is not None
    assert baseline_sequence is not None

    print(f"Baseline: .icarus-data/{baseline_sequence}/data/")

    mod_slugs = discover_mods()
    if not mod_slugs:
        print("Error: No mods discovered under mods/", file=sys.stderr)
        return 1

    print(f"Discovered mods: {len(mod_slugs)}")

    contexts: list[ModContext] = []
    errors: list[str] = []
    warnings: list[str] = []

    validated_archives: dict[str, ArchiveContext] = {}
    skipped_wip_mods = 0

    changed_file_count = 0
    unchanged_file_count = 0

    print("\n[1/3] Validating mods and EXMODZ packages...")
    for slug in mod_slugs:
        context, context_issues = build_mod_context(slug)
        if context is None:
            errors.extend([f"{slug}: {issue}" for issue in context_issues])
            continue

        for issue in context_issues:
            warnings.append(f"{slug}: {issue}")

        contexts.append(context)

        if not context.exmodz_path.exists():
            warnings.append(
                f"{slug}: Missing EXMODZ archive ({context.exmodz_path.name}) - treated as work-in-progress, skipping"
            )
            skipped_wip_mods += 1
            continue

        archive_context, archive_error = read_exmod_archive(context.exmodz_path)
        if archive_error:
            errors.append(f"{slug}: {archive_error}")
            continue
        assert archive_context is not None

        exmod_issues = validate_exmod_payload(archive_context.exmod_payload, slug)
        if exmod_issues:
            errors.extend([f"{slug}: {issue}" for issue in exmod_issues])
            continue

        rows = archive_context.exmod_payload.get("Rows")
        assert isinstance(rows, list)

        relative_paths = [
            path.relative_to(context.folder / "data").as_posix() for path in context.mod_json_files
        ]
        missing_in_exmod, extra_in_exmod = compare_rows_to_mod_files(rows, relative_paths)

        if missing_in_exmod:
            errors.append(
                f"{slug}: EXMOD missing row(s) for mod file(s): {', '.join(missing_in_exmod)}"
            )
        if extra_in_exmod:
            errors.append(
                f"{slug}: EXMOD row(s) reference file(s) not in mod data: {', '.join(extra_in_exmod)}"
            )

        mod_data_root = context.folder / "data"
        for mod_file in context.mod_json_files:
            relative_path = mod_file.relative_to(mod_data_root).as_posix()
            baseline_file = baseline_data_path / relative_path

            if not baseline_file.exists():
                errors.append(f"{slug}: Missing baseline file: {relative_path}")
                continue

            mod_data, mod_error = load_json_file(mod_file)
            baseline_data, baseline_file_error = load_json_file(baseline_file)
            if mod_error:
                errors.append(f"{slug}: {mod_error}")
                continue
            if baseline_file_error:
                errors.append(f"{slug}: {baseline_file_error}")
                continue

            if mod_data == baseline_data:
                warnings.append(f"{slug}: No diff detected vs baseline for {relative_path}")
                unchanged_file_count += 1
                continue

            patched_baseline = apply_mod_to_baseline(mod_data, baseline_data)
            if patched_baseline == baseline_data:
                warnings.append(
                    f"{slug}: Mod payload produced no effective change when applied to baseline for {relative_path}"
                )
                unchanged_file_count += 1
                continue

            baseline_section, patched_section = build_section_compare_payloads(
                baseline_data,
                patched_baseline,
            )
            if baseline_section == patched_section:
                warnings.append(
                    f"{slug}: No section-level change could be built for {relative_path}"
                )
                unchanged_file_count += 1
                continue

            changed_file_count += 1
            print(f"  - {slug}: changed -> {relative_path}")
            for line in summarize_diff(
                patched_section,
                baseline_section,
                f"{slug}/{relative_path}",
            ):
                print(f"      {line}")

            if args.with_compare:
                baseline_file_for_compare, baseline_file_error = write_temp_json_file(
                    baseline_section
                )
                if baseline_file_error:
                    warnings.append(f"{slug}: {baseline_file_error}")
                    continue

                patched_file, patched_file_error = write_temp_json_file(patched_section)
                if patched_file_error:
                    warnings.append(f"{slug}: {patched_file_error}")
                    continue

                assert baseline_file_for_compare is not None
                assert patched_file is not None
                while True:
                    ok, compare_error = run_compare_command(baseline_file_for_compare, patched_file)
                    if not ok:
                        warnings.append(f"{slug}: {compare_error}")
                        break

                    if not args.pause:
                        break

                    should_continue = wait_for_user_confirmation(f"{slug}/{relative_path}")
                    if should_continue is True:
                        break
                    if should_continue is False:
                        print("User cancelled during manual compare review.")
                        return 1

        validated_archives[slug] = archive_context

    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  - {warning}")

    if errors:
        print("\nValidation errors:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print("\nNo write operations were performed due to validation failures.", file=sys.stderr)
        return 1

    print(
        f"\nValidation complete: {len(contexts)} mod(s), "
        f"{changed_file_count} changed file(s), {unchanged_file_count} unchanged file(s), "
        f"{skipped_wip_mods} work-in-progress mod(s) skipped"
    )

    if not args.write:
        print("\nDry run complete. Re-run with --write to apply updates.")
        return 0

    print("\n[2/3] Applying EXMODZ + README updates...")
    exmod_updates = 0
    readme_week_updates = 0
    readme_date_updates = 0

    for context in contexts:
        archive_context = validated_archives.get(context.slug)
        if archive_context is None:
            continue

        payload = archive_context.exmod_payload
        exmod_changed = update_exmod_metadata(payload, context.readme, author, week)
        if exmod_changed:
            success, rewrite_error = rewrite_exmodz_entry(
                context.exmodz_path,
                archive_context.exmod_entry_name,
                payload,
            )
            if not success:
                print(f"Error: {rewrite_error}", file=sys.stderr)
                return 1
            exmod_updates += 1

        success, changed, readme_error = update_readme_week(context.readme_path, week)
        if not success:
            print(f"Error: {readme_error}", file=sys.stderr)
            return 1
        if changed:
            readme_week_updates += 1

        mod_changed_this_run = exmod_changed or changed
        if mod_changed_this_run:
            date_updated_value = format_readme_date_updated()
            success, date_changed, readme_error = update_readme_field(
                context.readme_path,
                "Date Updated",
                date_updated_value,
                append_if_missing=True,
                append_with_backticks=True,
            )
            if not success:
                print(f"Error: {readme_error}", file=sys.stderr)
                return 1
            if date_changed:
                readme_date_updates += 1

    print(f"Updated EXMODZ archives: {exmod_updates}")
    print(f"Updated README week fields: {readme_week_updates}")
    print(f"Updated README Date Updated fields: {readme_date_updates}")

    print("\n[3/3] Regenerating modinfo.json...")
    entries: list[dict] = []
    exmodz_files = sorted(MODS_EXMODZ_DIR.glob("*.EXMODZ"))
    context_by_slug = {context.slug: context for context in contexts}

    for exmodz_file in exmodz_files:
        slug = exmodz_file.stem
        context = context_by_slug.get(slug)
        if context is None:
            warnings.append(
                f"No mod README/data context found for EXMODZ archive '{exmodz_file.name}', skipping"
            )
            continue
        entries.append(build_modinfo_entry(slug, context.readme, author))

    modinfo_payload = {
        "mods": entries,
    }

    success, modinfo_error = save_json_file(MODINFO_FILE, modinfo_payload)
    if not success:
        print(f"Error: {modinfo_error}", file=sys.stderr)
        return 1

    print(f"modinfo.json entries: {len(entries)}")
    if warnings:
        print("\nPost-write warnings:")
        for warning in warnings:
            print(f"  - {warning}")

    print("\nMaintenance update completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
