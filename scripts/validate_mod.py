"""Validate and minimize mod JSON files against extracted game baseline.

This script lets you select a mod under mods/, compares each JSON file in that mod
against the latest baseline data in .icarus-data/, creates minimized sidecar files
for review, and only replaces the source file after explicit user confirmation.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
MODS_DIR = ROOT_DIR / "mods"
DATA_ROOT_DIR = ROOT_DIR / ".icarus-data"
METADATA_FILE = ROOT_DIR / "metadata.json"
PRESERVED_TOP_LEVEL_KEYS = ("RowStruct", "Defaults", "GenerateEnum")
NO_CHANGE = object()


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
    """Load JSON from disk.

    Returns:
        (data, error_message)
    """
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
    """Write JSON with stable formatting."""
    try:
        with file_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return True, None
    except OSError as exc:
        return False, f"Could not write {file_path}: {exc}"


def is_rows_list(value: object) -> bool:
    """Check if a list is a DataTable Rows-style list of dicts keyed by Name."""
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict) or "Name" not in item:
            return False
    return True


def diff_rows(mod_rows: list, base_rows: list) -> object:
    """Compute minimized row-level diff keyed by Name."""
    if not is_rows_list(mod_rows) or not is_rows_list(base_rows):
        if mod_rows == base_rows:
            return NO_CHANGE
        return mod_rows

    base_index: dict[str, dict] = {}
    for row in base_rows:
        row_name = row.get("Name")
        if not isinstance(row_name, str) or row_name in base_index:
            if mod_rows == base_rows:
                return NO_CHANGE
            return mod_rows
        base_index[row_name] = row

    minimized_rows: list[dict] = []
    for row in mod_rows:
        row_name = row.get("Name")
        if not isinstance(row_name, str):
            minimized_rows.append(row)
            continue

        base_row = base_index.get(row_name)
        if base_row is None:
            minimized_rows.append(row)
            continue

        row_diff = diff_dict(row, base_row)
        if row_diff is NO_CHANGE:
            continue

        if not isinstance(row_diff, dict):
            minimized_rows.append(row)
            continue

        row_payload = {"Name": row_name}
        for key, value in row_diff.items():
            if key == "Name":
                continue
            row_payload[key] = value
        if len(row_payload) > 1:
            minimized_rows.append(row_payload)

    if not minimized_rows:
        return NO_CHANGE

    return minimized_rows


def diff_value(mod_value: object, base_value: object, key: str | None = None) -> object:
    """Return minimized diff payload or NO_CHANGE sentinel."""
    if isinstance(mod_value, dict) and isinstance(base_value, dict):
        return diff_dict(mod_value, base_value)

    if isinstance(mod_value, list) and isinstance(base_value, list):
        if key == "Rows":
            return diff_rows(mod_value, base_value)
        if mod_value == base_value:
            return NO_CHANGE
        return mod_value

    if mod_value == base_value:
        return NO_CHANGE

    return mod_value


def diff_dict(mod_obj: dict, base_obj: dict) -> object:
    """Compute minimized dictionary diff."""
    diff_payload: dict = {}

    for key, mod_value in mod_obj.items():
        if key not in base_obj:
            diff_payload[key] = mod_value
            continue

        result = diff_value(mod_value, base_obj[key], key=key)
        if result is not NO_CHANGE:
            diff_payload[key] = result

    if not diff_payload:
        return NO_CHANGE

    return diff_payload


def build_minimized_payload(mod_data: object, baseline_data: object) -> tuple[object | None, bool]:
    """Build final minimized output payload and report changed status."""
    diff_payload = diff_value(mod_data, baseline_data)
    if diff_payload is NO_CHANGE:
        return None, False

    if isinstance(mod_data, dict):
        final_payload: dict = {}

        for key in PRESERVED_TOP_LEVEL_KEYS:
            if key in mod_data:
                final_payload[key] = mod_data[key]

        if isinstance(diff_payload, dict):
            for key, value in diff_payload.items():
                final_payload[key] = value
        else:
            return mod_data, True

        return final_payload, True

    return diff_payload, True


def discover_mods() -> list[str]:
    """Return sorted mod slugs under mods/."""
    if not MODS_DIR.exists():
        return []

    mod_names = [
        child.name for child in MODS_DIR.iterdir() if child.is_dir() and (child / "data").exists()
    ]
    mod_names.sort()
    return mod_names


def select_mod(mod_names: list[str]) -> str | None:
    """Prompt user to choose one mod."""
    if not mod_names:
        print("Error: No mods found under mods/", file=sys.stderr)
        return None

    print("Available mods:")
    for index, mod_name in enumerate(mod_names, start=1):
        print(f"  {index}. {mod_name}")

    while True:
        choice = input("Select a mod by number (or 'q' to quit): ").strip().lower()
        if choice == "q":
            return None
        if choice.isdigit():
            selected_index = int(choice) - 1
            if 0 <= selected_index < len(mod_names):
                return mod_names[selected_index]
        print("Invalid selection. Please enter a valid number.")


def resolve_baseline_folder() -> tuple[Path | None, str | None, str | None]:
    """Resolve baseline data folder using metadata first and sorted fallback.

    Returns:
        (baseline_data_path, baseline_sequence, error_message)
    """
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
        if child.is_dir() and child.name.replace(".", "").isdigit():
            if (child / "data").exists():
                sequences.append(child.name)

    if not sequences:
        return None, None, "No valid baseline sequence folders were found in .icarus-data/"

    sequences.sort(key=parse_version, reverse=True)
    selected_sequence = sequences[0]
    return DATA_ROOT_DIR / selected_sequence / "data", selected_sequence, None


def find_mod_json_files(mod_slug: str) -> list[Path]:
    """Find all mod JSON files under mods/<slug>/data excluding sidecars."""
    data_dir = MODS_DIR / mod_slug / "data"
    files = [path for path in data_dir.rglob("*.json") if not path.name.endswith("_exmod.json")]
    files.sort()
    return files


def open_for_review(file_path: Path) -> None:
    """Open a file with the OS default associated app."""
    try:
        os.startfile(file_path)  # type: ignore[attr-defined]
    except Exception as exc:
        print(f"Warning: Could not open {file_path}: {exc}", file=sys.stderr)


def ask_review_decision(relative_path: str) -> str:
    """Prompt user to accept or reject minimized output."""
    while True:
        decision = (
            input(f"Approve minimized output for {relative_path}? [y]es/[n]o/[q]uit: ")
            .strip()
            .lower()
        )
        if decision in {"y", "n", "q"}:
            return decision
        print("Please enter 'y', 'n', or 'q'.")


def validate_reviewed_sidecar(sidecar_file: Path, baseline_data: object) -> tuple[bool, str | None]:
    """Validate manually edited sidecar before replacement.

    Ensures JSON is valid and still represents changed content versus baseline.
    """
    reviewed_data, reviewed_error = load_json_file(sidecar_file)
    if reviewed_error:
        return False, reviewed_error

    _, has_changes = build_minimized_payload(reviewed_data, baseline_data)
    if not has_changes:
        return (
            False,
            "Reviewed sidecar has no changes versus baseline. "
            "It must contain at least one intended mod change.",
        )

    return True, None


def replace_original_with_sidecar(
    original_file: Path, sidecar_file: Path
) -> tuple[bool, str | None]:
    """Replace original JSON content with sidecar content, then delete sidecar."""
    try:
        sidecar_content = sidecar_file.read_text(encoding="utf-8")
        temp_file = original_file.with_name(f"{original_file.name}.tmp")
        temp_file.write_text(sidecar_content, encoding="utf-8")
        temp_file.replace(original_file)
        sidecar_file.unlink(missing_ok=True)
        return True, None
    except OSError as exc:
        return False, f"Failed to replace {original_file} from sidecar: {exc}"


def update_metadata_mod_index(
    mod_slug: str, baseline_sequence: str, files: list[str]
) -> tuple[bool, str | None]:
    """Update metadata.json with per-mod indexed files."""
    metadata, error = load_json_file(METADATA_FILE)
    if error:
        return False, error

    if not isinstance(metadata, dict):
        return False, f"Unexpected metadata format in {METADATA_FILE}"

    mod_file_index = metadata.get("mod_file_index")
    if not isinstance(mod_file_index, dict):
        mod_file_index = {}

    mod_file_index[mod_slug] = {
        "updated_at": datetime.now().isoformat(),
        "baseline_folder": baseline_sequence,
        "files": files,
    }
    metadata["mod_file_index"] = mod_file_index

    temp_file = METADATA_FILE.with_suffix(".json.tmp")
    success, write_error = save_json_file(temp_file, metadata)
    if not success:
        return False, write_error

    try:
        temp_file.replace(METADATA_FILE)
    except OSError as exc:
        return False, f"Failed to update metadata.json atomically: {exc}"

    return True, None


def main() -> int:
    """Main execution flow."""
    print("=" * 60)
    print("Icarus Mod JSON Validator")
    print("=" * 60)

    print("\n[1/6] Discovering mods...")
    mod_names = discover_mods()
    selected_mod = select_mod(mod_names)
    if selected_mod is None:
        print("No mod selected. Exiting.")
        return 0
    print(f"✓ Selected mod: {selected_mod}")

    print("\n[2/6] Resolving baseline folder...")
    baseline_data_path, baseline_sequence, baseline_error = resolve_baseline_folder()
    if baseline_error:
        print(f"Error: {baseline_error}", file=sys.stderr)
        return 1
    assert baseline_data_path is not None
    assert baseline_sequence is not None
    print(f"✓ Baseline: .icarus-data/{baseline_sequence}/data/")

    print("\n[3/6] Scanning mod files...")
    mod_files = find_mod_json_files(selected_mod)
    if not mod_files:
        print(f"No JSON files found in mods/{selected_mod}/data/")
        return 0
    print(f"✓ Found {len(mod_files)} JSON file(s)")

    print("\n[4/6] Validating and minimizing files...")
    scanned_files = 0
    changed_files = 0
    missing_baseline_files = 0
    skipped_or_error_files = 0
    approved_replacements = 0
    rejected_replacements = 0
    approved_relative_files: list[str] = []

    mod_data_root = MODS_DIR / selected_mod / "data"

    for mod_file in mod_files:
        scanned_files += 1
        relative_path = mod_file.relative_to(mod_data_root).as_posix()
        baseline_file = baseline_data_path / relative_path

        mod_data, mod_error = load_json_file(mod_file)
        if mod_error:
            print(f"  - Skipped (error): {mod_error}")
            skipped_or_error_files += 1
            continue

        if not baseline_file.exists():
            print(f"  - Missing baseline: {relative_path}")
            missing_baseline_files += 1
            continue

        baseline_data, baseline_error = load_json_file(baseline_file)
        if baseline_error:
            print(f"  - Skipped (error): {baseline_error}")
            skipped_or_error_files += 1
            continue

        minimized_payload, is_changed = build_minimized_payload(mod_data, baseline_data)
        if not is_changed or minimized_payload is None:
            print(f"  - Unchanged: {relative_path}")
            continue

        changed_files += 1
        sidecar_file = mod_file.with_name(f"{mod_file.stem}_exmod.json")
        success, save_error = save_json_file(sidecar_file, minimized_payload)
        if not success:
            print(f"  - Skipped (error): {save_error}")
            skipped_or_error_files += 1
            continue

        print(f"  - Review required: {relative_path}")
        open_for_review(mod_file)
        open_for_review(sidecar_file)

        while True:
            decision = ask_review_decision(relative_path)
            if decision == "q":
                print("User requested to quit review early.")
                break

            if decision == "n":
                rejected_replacements += 1
                sidecar_file.unlink(missing_ok=True)
                print(f"    Rejected. Original kept: {relative_path}")
                break

            is_valid_sidecar, sidecar_error = validate_reviewed_sidecar(
                sidecar_file,
                baseline_data,
            )
            if not is_valid_sidecar:
                print(f"    Sidecar failed revalidation: {sidecar_error}")
                print("    Please fix the file and review again.")
                open_for_review(sidecar_file)
                continue

            replace_ok, replace_error = replace_original_with_sidecar(mod_file, sidecar_file)
            if not replace_ok:
                print(f"  - Skipped (error): {replace_error}")
                skipped_or_error_files += 1
                break

            approved_replacements += 1
            approved_relative_files.append(relative_path)
            print(f"    Approved and replaced: {relative_path}")
            break

        if decision == "q":
            break

    print("\n[5/6] Updating metadata index...")
    metadata_ok, metadata_error = update_metadata_mod_index(
        selected_mod,
        baseline_sequence,
        approved_relative_files,
    )
    if not metadata_ok:
        print(f"Error: {metadata_error}", file=sys.stderr)
        return 1
    print("✓ metadata.json updated")

    print("\n[6/6] Summary")
    print("-" * 60)
    print(f"Mod: {selected_mod}")
    print(f"Baseline folder: {baseline_sequence}")
    print(f"Files scanned: {scanned_files}")
    print(f"Files changed: {changed_files}")
    print(f"Files missing baseline: {missing_baseline_files}")
    print(f"Files skipped/errors: {skipped_or_error_files}")
    print(f"Approved replacements: {approved_replacements}")
    print(f"Rejected replacements: {rejected_replacements}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
