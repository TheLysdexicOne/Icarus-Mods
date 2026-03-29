"""Output recipe names grouped by their effective RequiredMillijoules value.

This script reads the latest baseline Crafting/D_ProcessorRecipes.json file,
resolves the effective RequiredMillijoules for each named recipe row, and
prints the recipe names grouped by millijoules from least to most.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from update_crafting import load_json_file, resolve_baseline_file


DEFAULT_REQUIRED_MILLIJOULES = 2500


def resolve_default_required_millijoules(baseline_payload: dict) -> int:
    """Return the default RequiredMillijoules value for rows that omit it."""
    defaults = baseline_payload.get("Defaults")
    if not isinstance(defaults, dict):
        return DEFAULT_REQUIRED_MILLIJOULES

    value = defaults.get("RequiredMillijoules", DEFAULT_REQUIRED_MILLIJOULES)
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
    """Format grouped recipe names for console output."""
    lines: list[str] = []

    for required_millijoules, recipe_names in groups.items():
        lines.append(str(required_millijoules))
        lines.extend(f"  {recipe_name}" for recipe_name in recipe_names)

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Output recipe names grouped by RequiredMillijoules."
    )
    parser.add_argument(
        "--baseline-file",
        help="Optional baseline D_ProcessorRecipes.json file to use instead of the latest synced version.",
    )
    return parser.parse_args()


def main() -> int:
    """Print recipe names grouped by effective RequiredMillijoules."""
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

    try:
        groups = collect_required_millijoules_groups(baseline_payload)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(format_required_millijoules_groups(groups))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
