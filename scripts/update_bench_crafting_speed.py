"""Set crafting speed for every item row tagged as an Item.Bench.

This script updates a DataTable JSON file in-place. Any row that contains the
configured gameplay tag in Manual_Tags or Generated_Tags gets the configured
AdditionalStats entry set to the requested value.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT_DIR / "mods" / "crafting-speed-200" / "data" / "Items" / "D_ItemsStatic.json"
METADATA_FILE = ROOT_DIR / "metadata.json"
DEFAULT_TAG = "Item.Bench"
DEFAULT_STAT_KEY = '(Value="BaseCraftingSpeed_+%")'
DEFAULT_STAT_VALUE = 200


def load_json_file(file_path: Path) -> tuple[object | None, str | None]:
    """Load JSON from disk."""
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
            json.dump(payload, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
        return True, None
    except OSError as exc:
        return False, f"Could not write {file_path}: {exc}"


def load_metadata() -> tuple[dict | None, str | None]:
    """Load repository metadata used to resolve the latest baseline folder."""
    payload, error_message = load_json_file(METADATA_FILE)
    if error_message:
        return None, error_message
    if not isinstance(payload, dict):
        return None, f"Metadata file is not a JSON object: {METADATA_FILE}"
    return payload, None


def resolve_baseline_file(
    target_file: Path, baseline_file: str | None
) -> tuple[Path | None, str | None]:
    """Resolve the baseline file path for the target DataTable JSON file."""
    if baseline_file:
        return Path(baseline_file), None

    try:
        data_index = target_file.parts.index("data")
    except ValueError:
        return None, f"Target file must live under a data/ folder: {target_file}"

    metadata, error_message = load_metadata()
    if error_message:
        return None, error_message
    if metadata is None:
        return None, f"Could not load metadata from {METADATA_FILE}"

    latest_data_folder = metadata.get("latest_data_folder")
    if not isinstance(latest_data_folder, str) or not latest_data_folder:
        return None, f"metadata.json does not define latest_data_folder: {METADATA_FILE}"

    relative_data_path = Path(*target_file.parts[data_index + 1 :])
    baseline_path = ROOT_DIR / ".icarus-data" / latest_data_folder / "data" / relative_data_path
    return baseline_path, None


def row_has_tag(row: dict, target_tag: str) -> bool:
    """Check Manual_Tags and Generated_Tags for the requested gameplay tag."""
    for tag_group_name in ("Manual_Tags", "Generated_Tags"):
        tag_group = row.get(tag_group_name)
        if not isinstance(tag_group, dict):
            continue

        gameplay_tags = tag_group.get("GameplayTags")
        if not isinstance(gameplay_tags, list):
            continue

        for tag_entry in gameplay_tags:
            if not isinstance(tag_entry, dict):
                continue
            if tag_entry.get("TagName") == target_tag:
                return True

    return False


def insert_key_before(row: dict, new_key: str, value: object, before_key: str) -> dict:
    """Return a new dict with new_key inserted before before_key when possible."""
    updated_row: dict = {}
    inserted = False

    for key, existing_value in row.items():
        if key == before_key and not inserted:
            updated_row[new_key] = value
            inserted = True
        updated_row[key] = existing_value

    if not inserted:
        updated_row[new_key] = value

    return updated_row


def index_rows_by_name(rows: list[object]) -> dict[str, dict]:
    """Build an index of row payloads keyed by Name."""
    indexed_rows: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_name = row.get("Name")
        if not isinstance(row_name, str):
            continue
        indexed_rows[row_name] = row
    return indexed_rows


def ensure_crafting_speed(row: dict, stat_key: str, stat_value: int) -> tuple[dict, bool]:
    """Ensure the target AdditionalStats entry exists and equals stat_value."""
    additional_stats = row.get("AdditionalStats")

    if isinstance(additional_stats, dict):
        if additional_stats.get(stat_key) == stat_value:
            return row, False
        additional_stats[stat_key] = stat_value
        return row, True

    updated_row = insert_key_before(
        row,
        "AdditionalStats",
        {stat_key: stat_value},
        "Manual_Tags",
    )
    return updated_row, True


def update_rows(
    payload: object,
    baseline_payload: object,
    target_tag: str,
    stat_key: str,
    stat_value: int,
) -> tuple[int, int]:
    """Update matching rows in a DataTable payload using baseline tag data.

    Returns:
        tuple[int, int]: (matching_row_count, changed_row_count)
    """
    if not isinstance(payload, dict):
        raise ValueError("Top-level JSON payload must be an object.")
    if not isinstance(baseline_payload, dict):
        raise ValueError("Baseline JSON payload must be an object.")

    rows = payload.get("Rows")
    baseline_rows = baseline_payload.get("Rows")
    if not isinstance(rows, list):
        raise ValueError("JSON payload is missing a Rows list.")
    if not isinstance(baseline_rows, list):
        raise ValueError("Baseline JSON payload is missing a Rows list.")

    existing_rows = index_rows_by_name(rows)
    managed_rows: list[dict] = []
    managed_names: set[str] = set()

    matching_rows = 0
    changed_rows = 0

    for baseline_row in baseline_rows:
        if not isinstance(baseline_row, dict):
            continue
        if not row_has_tag(baseline_row, target_tag):
            continue

        row_name = baseline_row.get("Name")
        if not isinstance(row_name, str):
            continue

        matching_rows += 1
        managed_names.add(row_name)

        current_row = dict(existing_rows.get(row_name, {"Name": row_name}))
        updated_row, changed = ensure_crafting_speed(current_row, stat_key, stat_value)
        if changed:
            changed_rows += 1
        managed_rows.append(updated_row)

    unmanaged_rows = []
    for row in rows:
        if not isinstance(row, dict):
            unmanaged_rows.append(row)
            continue
        row_name = row.get("Name")
        if isinstance(row_name, str) and row_name in managed_names:
            continue
        unmanaged_rows.append(row)

    payload["Rows"] = managed_rows + unmanaged_rows

    return matching_rows, changed_rows


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Set a crafting speed AdditionalStats value for all Item.Bench rows."
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=str(DEFAULT_FILE),
        help="Path to the DataTable JSON file to update.",
    )
    parser.add_argument(
        "--tag",
        default=DEFAULT_TAG,
        help="Gameplay tag to match in the baseline file's Manual_Tags or Generated_Tags.",
    )
    parser.add_argument(
        "--stat-key",
        default=DEFAULT_STAT_KEY,
        help="AdditionalStats key to set on matching rows.",
    )
    parser.add_argument(
        "--value",
        type=int,
        default=DEFAULT_STAT_VALUE,
        help="Numeric value to assign to the AdditionalStats entry.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report matching and changed rows without writing the file.",
    )
    parser.add_argument(
        "--baseline-file",
        help="Optional baseline DataTable JSON file to use when discovering tagged rows.",
    )
    return parser.parse_args()


def main() -> int:
    """Update the target file in-place."""
    args = parse_args()
    file_path = Path(args.file)
    baseline_file_path, error_message = resolve_baseline_file(file_path, args.baseline_file)
    if error_message:
        print(error_message, file=sys.stderr)
        return 1
    if baseline_file_path is None:
        print("Could not resolve baseline file path.", file=sys.stderr)
        return 1

    payload, error_message = load_json_file(file_path)
    if error_message:
        print(error_message, file=sys.stderr)
        return 1

    baseline_payload, error_message = load_json_file(baseline_file_path)
    if error_message:
        print(error_message, file=sys.stderr)
        return 1

    try:
        matching_rows, changed_rows = update_rows(
            payload,
            baseline_payload,
            args.tag,
            args.stat_key,
            args.value,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if matching_rows == 0:
        print(f"No rows with gameplay tag '{args.tag}' were found in {baseline_file_path}.")
        return 0

    print(f"Using baseline file: {baseline_file_path}")
    print(f"Matched {matching_rows} rows with gameplay tag '{args.tag}'.")
    print(f"Updated {changed_rows} rows to set {args.stat_key} = {args.value}.")

    if args.dry_run or changed_rows == 0:
        return 0

    success, error_message = save_json_file(file_path, payload)
    if not success:
        print(error_message, file=sys.stderr)
        return 1

    print(f"Saved updated file: {file_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
