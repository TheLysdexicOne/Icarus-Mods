"""Generate crafting-time mod files from the latest processor recipe baseline.

This script reads the latest baseline Crafting/D_ProcessorRecipes.json file,
finds every RequiredMillijoules entry, divides it by the configured amount for
each target mod, and writes the transformed payload to the mod's data file.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
METADATA_FILE = ROOT_DIR / "metadata.json"
BASELINE_RELATIVE_PATH = Path("Crafting") / "D_ProcessorRecipes.json"
MJ_REQUIREMENTS_FILE = ROOT_DIR / "documentation" / "mj_requirements.txt"
X5_RECIPE_NAMES = ()
X10_RECIPE_NAMES = (
    "crushed_bone",
    "crushed_limestone",
    "limestone_concrete_mix",
    "concrete_mix",
    "epoxy",
    "epoxy_2",
    "tree_sap",
    "tree_sap_wood",
    "oil_plastics",
    "oil_epoxy",
    "organic_resin",
    "composite_paste",
    "composite_paste_plat",
    "carbon_paste",
    "steel_bloom",
    "steel_bloom2",
    "steel_bloom3",
    "steel_bloom4",
    "steel_bloom_limestone",
    "copper_wire",
    "gold_wire",
)
X5_RECIPE_PATTERNS = ()
X10_RECIPE_PATTERNS = (re.compile(r".*_(arrow|bolt)$"),)
TARGET_MODS = (
    ("crafting-time-half", 2),
    ("crafting-time-quarter", 4),
)


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


def save_text_file(file_path: Path, content: str) -> tuple[bool, str | None]:
    """Write text content to disk."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("w", encoding="utf-8") as handle:
            handle.write(content)
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


def resolve_baseline_file(baseline_file: str | None) -> tuple[Path | None, str | None]:
    """Resolve the baseline file path for D_ProcessorRecipes.json."""
    if baseline_file:
        return Path(baseline_file), None

    metadata, error_message = load_metadata()
    if error_message:
        return None, error_message
    if metadata is None:
        return None, f"Could not load metadata from {METADATA_FILE}"

    latest_data_folder = metadata.get("latest_data_folder")
    if not isinstance(latest_data_folder, str) or not latest_data_folder:
        return None, f"metadata.json does not define latest_data_folder: {METADATA_FILE}"

    baseline_path = ROOT_DIR / ".icarus-data" / latest_data_folder / "data" / BASELINE_RELATIVE_PATH
    return baseline_path, None


def scale_recipe_for_batch(recipe_row: dict, multiplier: int) -> None:
    """Scale a recipe row in place for a larger batch size."""
    if multiplier <= 0:
        raise ValueError("Multiplier must be a positive integer.")

    required_millijoules = recipe_row.get("RequiredMillijoules")
    if required_millijoules is not None:
        if not isinstance(required_millijoules, int):
            raise ValueError("Expected integer RequiredMillijoules when scaling recipe.")
        recipe_row["RequiredMillijoules"] = required_millijoules * multiplier

    for list_key in ("Inputs", "Outputs"):
        entries = recipe_row.get(list_key)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise ValueError(f"Expected {list_key} to be a list when scaling recipe.")

        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Expected {list_key}[{index}] to be an object when scaling recipe."
                )
            count = entry.get("Count")
            if not isinstance(count, int):
                raise ValueError(f"Expected integer {list_key}[{index}].Count when scaling recipe.")
            entry["Count"] = count * multiplier

    resource_outputs = recipe_row.get("ResourceOutputs")
    if resource_outputs is not None:
        if not isinstance(resource_outputs, list):
            raise ValueError("Expected ResourceOutputs to be a list when scaling recipe.")

        for index, entry in enumerate(resource_outputs):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Expected ResourceOutputs[{index}] to be an object when scaling recipe."
                )
            required_units = entry.get("RequiredUnits")
            if not isinstance(required_units, int):
                raise ValueError(
                    f"Expected integer ResourceOutputs[{index}].RequiredUnits when scaling recipe."
                )
            entry["RequiredUnits"] = required_units * multiplier


def build_multi_craft_recipe(base_row: dict, multiplier: int) -> dict:
    """Create a new multi-craft recipe row from a baseline recipe row."""
    recipe_name = base_row.get("Name")
    if not isinstance(recipe_name, str) or not recipe_name:
        raise ValueError("Recipe row must define a non-empty Name.")

    recipe_row = copy.deepcopy(base_row)
    recipe_row["Name"] = f"{recipe_name}_{multiplier}"
    scale_recipe_for_batch(recipe_row, multiplier)
    return recipe_row


def recipe_matches_any_pattern(recipe_name: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Return True when a recipe name matches any configured regex pattern."""
    return any(pattern.fullmatch(recipe_name) for pattern in patterns)


def add_multi_craft_recipes(payload: object) -> int:
    """Add configured x5 and x10 multi-craft recipe rows when they do not exist."""
    if not isinstance(payload, dict):
        raise ValueError("Baseline JSON payload must be an object.")

    rows = payload.get("Rows")
    if not isinstance(rows, list):
        raise ValueError("Baseline JSON payload must contain a Rows array.")

    x5_recipe_names: set[str] = set(X5_RECIPE_NAMES)
    x10_recipe_names: set[str] = set(X10_RECIPE_NAMES)
    x5_recipe_patterns = X5_RECIPE_PATTERNS
    x10_recipe_patterns = X10_RECIPE_PATTERNS
    configured_recipe_names: set[str] = x5_recipe_names | x10_recipe_names
    found_recipe_names: set[str] = set()
    existing_recipe_names = {
        row["Name"].lower()
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("Name"), str) and row["Name"]
    }

    new_rows: list[object] = []
    added_recipe_count = 0

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Expected Rows[{index}] to be an object.")

        new_rows.append(row)

        recipe_name = row.get("Name")
        if not isinstance(recipe_name, str) or not recipe_name:
            continue

        recipe_name_key = recipe_name.lower()
        matches_x5_recipe = recipe_name_key in x5_recipe_names or recipe_matches_any_pattern(
            recipe_name_key, x5_recipe_patterns
        )
        matches_x10_recipe = recipe_name_key in x10_recipe_names or recipe_matches_any_pattern(
            recipe_name_key, x10_recipe_patterns
        )
        if not matches_x5_recipe and not matches_x10_recipe:
            continue

        if recipe_name_key in configured_recipe_names:
            found_recipe_names.add(recipe_name_key)

        for multiplier, is_enabled in ((5, matches_x5_recipe), (10, matches_x10_recipe)):
            if not is_enabled:
                continue

            multi_craft_recipe_name = f"{recipe_name}_{multiplier}"
            multi_craft_recipe_key = multi_craft_recipe_name.lower()
            if multi_craft_recipe_key in existing_recipe_names:
                continue

            new_rows.append(build_multi_craft_recipe(row, multiplier))
            existing_recipe_names.add(multi_craft_recipe_key)
            added_recipe_count += 1

    missing_recipe_names = sorted(configured_recipe_names - found_recipe_names)
    if missing_recipe_names:
        raise ValueError(
            "Could not find configured recipes in baseline payload: "
            + ", ".join(missing_recipe_names)
        )

    payload["Rows"] = new_rows
    return added_recipe_count


def resolve_default_required_millijoules(baseline_payload: dict) -> int:
    """Return the default RequiredMillijoules value for rows that omit it."""
    defaults = baseline_payload.get("Defaults")
    if not isinstance(defaults, dict):
        return 2500

    value = defaults.get("RequiredMillijoules", 2500)
    if not isinstance(value, int):
        raise ValueError("Baseline Defaults.RequiredMillijoules must be an integer when present.")
    return value


def collect_required_millijoules_groups(baseline_payload: dict) -> dict[int, list[str]]:
    """Collect recipe names grouped by their effective RequiredMillijoules."""
    rows = baseline_payload.get("Rows")
    if not isinstance(rows, list):
        raise ValueError("Baseline JSON payload must contain a Rows array.")

    default_required_millijoules = resolve_default_required_millijoules(baseline_payload)
    groups: dict[int, list[str]] = defaultdict(list)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Expected Rows[{index}] to be an object.")

        recipe_name = row.get("Name")
        if not isinstance(recipe_name, str) or not recipe_name:
            continue

        required_millijoules = row.get("RequiredMillijoules", default_required_millijoules)
        if not isinstance(required_millijoules, int):
            raise ValueError(f"Expected integer RequiredMillijoules for recipe {recipe_name}.")

        groups[required_millijoules].append(recipe_name.lower())

    for recipe_names in groups.values():
        recipe_names.sort()

    return dict(sorted(groups.items()))


def format_required_millijoules_groups(groups: dict[int, list[str]]) -> str:
    """Format grouped recipe names for output."""
    lines: list[str] = []

    for required_millijoules, recipe_names in groups.items():
        lines.append(str(required_millijoules))
        lines.extend(f"  {recipe_name}" for recipe_name in recipe_names)

    return "\n".join(lines) + "\n"


def export_required_millijoules_report(baseline_payload: object) -> tuple[bool, str | None]:
    """Write the baseline recipe requirements report to documentation/mj_requirements.txt."""
    if not isinstance(baseline_payload, dict):
        raise ValueError("Baseline JSON payload must be an object.")

    groups = collect_required_millijoules_groups(baseline_payload)
    return save_text_file(MJ_REQUIREMENTS_FILE, format_required_millijoules_groups(groups))


def divide_required_millijoules(payload: object, divisor: int) -> int:
    """Recursively divide every RequiredMillijoules integer by divisor.

    Values are rounded to the nearest integer so the output stays compatible
    with the integer field used by the recipe table.
    """
    if divisor <= 0:
        raise ValueError("Divisor must be a positive integer.")

    updates = 0

    def visit(node: object, path: str) -> None:
        nonlocal updates

        if isinstance(node, dict):
            for key, value in node.items():
                current_path = f"{path}.{key}" if path else key
                if key == "RequiredMillijoules":
                    if not isinstance(value, int):
                        raise ValueError(
                            f"Expected integer RequiredMillijoules at {current_path}, got {type(value).__name__}."
                        )
                    quotient, remainder = divmod(value, divisor)
                    node[key] = quotient + (1 if remainder * 2 >= divisor else 0)
                    updates += 1
                    continue
                visit(value, current_path)
            return

        if isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{path}[{index}]")

    visit(payload, "")
    return updates


def get_target_file(mod_slug: str) -> Path:
    """Return the target recipe file for a mod slug."""
    return ROOT_DIR / "mods" / mod_slug / "data" / BASELINE_RELATIVE_PATH


def build_mod_payload(baseline_payload: object, divisor: int) -> tuple[object, int]:
    """Create a transformed copy of the baseline payload for one mod."""
    payload = copy.deepcopy(baseline_payload)
    add_multi_craft_recipes(payload)
    updated_count = divide_required_millijoules(payload, divisor)
    return payload, updated_count


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate crafting-time mod files from the latest processor recipe baseline."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned updates without writing target files.",
    )
    parser.add_argument(
        "--baseline-file",
        help="Optional baseline D_ProcessorRecipes.json file to use instead of the latest synced version.",
    )
    return parser.parse_args()


def main() -> int:
    """Generate the crafting-time mod files."""
    args = parse_args()
    baseline_file_path, error_message = resolve_baseline_file(args.baseline_file)
    if error_message:
        print(error_message, file=sys.stderr)
        return 1
    if baseline_file_path is None:
        print("Could not resolve baseline file path.", file=sys.stderr)
        return 1

    baseline_payload, error_message = load_json_file(baseline_file_path)
    if error_message:
        print(error_message, file=sys.stderr)
        return 1

    if not isinstance(baseline_payload, dict):
        print("Baseline JSON payload must be an object.", file=sys.stderr)
        return 1

    print(f"Using baseline file: {baseline_file_path}")

    try:
        success, error_message = export_required_millijoules_report(baseline_payload)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not success:
        print(error_message, file=sys.stderr)
        return 1

    print(f"Saved MJ requirements report: {MJ_REQUIREMENTS_FILE}")

    for mod_slug, divisor in TARGET_MODS:
        try:
            payload, updated_count = build_mod_payload(baseline_payload, divisor)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        target_file = get_target_file(mod_slug)
        print(
            f"Prepared {updated_count} RequiredMillijoules updates for {mod_slug} "
            f"(divisor {divisor})."
        )

        if args.dry_run:
            continue

        success, error_message = save_json_file(target_file, payload)
        if not success:
            print(error_message, file=sys.stderr)
            return 1

        print(f"Saved updated file: {target_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
