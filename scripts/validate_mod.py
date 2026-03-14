"""Validate and minimize mod JSON files against extracted game baseline.

This script lets you select a mod under mods/, compares each JSON file in that mod
against the latest baseline data in .icarus-data/, creates minimized sidecar files
for review, and only replaces the source file after explicit user confirmation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from pak_files import create_pak_for_mod

ROOT_DIR = Path(__file__).resolve().parent.parent
MODS_DIR = ROOT_DIR / "mods"
DATA_ROOT_DIR = ROOT_DIR / ".icarus-data"
METADATA_FILE = ROOT_DIR / "metadata.json"
MODS_EXMODZ_DIR = ROOT_DIR / "mods-exmodz"
PRESERVED_TOP_LEVEL_KEYS = ("RowStruct", "Defaults", "GenerateEnum")
README_REQUIRED_FIELDS = (
    "name",
    "mod_slug",
    "version",
    "description",
    "image url",
    "readme url",
    "week compatibility",
)
NO_CHANGE = object()


def resolve_notepadpp_path() -> str | None:
    """Resolve Notepad++ executable path for Windows review opens."""
    candidate = shutil.which("notepad++")
    if candidate:
        return candidate

    common_paths = (
        Path("C:/Program Files/Notepad++/notepad++.exe"),
        Path("C:/Program Files (x86)/Notepad++/notepad++.exe"),
    )
    for candidate_path in common_paths:
        if candidate_path.exists():
            return str(candidate_path)

    return None


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


def find_mod_data_files(mod_slug: str) -> list[Path]:
    """Find all mod data files under mods/<slug>/data excluding sidecars."""
    data_dir = MODS_DIR / mod_slug / "data"
    files = [
        path
        for path in data_dir.rglob("*")
        if path.is_file() and not path.name.endswith("_exmod.json")
    ]
    files.sort()
    return files


def build_canonical_relative_file_list(mod_files: list[Path], mod_data_root: Path) -> list[str]:
    """Build sorted canonical relative file list from discovered mod files."""
    return sorted(path.relative_to(mod_data_root).as_posix() for path in mod_files)


def update_mod_readme_file_list(mod_slug: str, files: list[str]) -> tuple[bool, bool, str | None]:
    """Ensure mod README has an accurate File List section.

    Returns:
        (success, changed, error_message)
    """
    readme_file = MODS_DIR / mod_slug / "README.md"
    if not readme_file.exists():
        return False, False, f"README not found for mod: {mod_slug}"

    try:
        original_text = readme_file.read_text(encoding="utf-8")
    except OSError as exc:
        return False, False, f"Could not read {readme_file}: {exc}"

    lines = original_text.splitlines()
    file_list_header_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip().lower() == "## file list":
            file_list_header_index = index
            break

    file_list_body = [f"- {file_path}" for file_path in files]

    if file_list_header_index is None:
        new_lines = lines.copy()
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("## File List")
        new_lines.append("")
        new_lines.extend(file_list_body)
    else:
        section_end = len(lines)
        for index in range(file_list_header_index + 1, len(lines)):
            if re.match(r"^##\s+", lines[index].strip()):
                section_end = index
                break

        new_lines = lines[: file_list_header_index + 1]
        new_lines.append("")
        new_lines.extend(file_list_body)
        new_lines.extend(lines[section_end:])

    new_text = "\n".join(new_lines).rstrip() + "\n"
    if new_text == original_text:
        return True, False, None

    try:
        readme_file.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return False, False, f"Could not write {readme_file}: {exc}"

    return True, True, None


def parse_readme_metadata(mod_slug: str) -> tuple[dict[str, str] | None, str | None]:
    """Parse mod README metadata fields used for EXMOD payload generation."""
    readme_file = MODS_DIR / mod_slug / "README.md"
    if not readme_file.exists():
        return None, f"README not found for mod: {mod_slug}"

    try:
        content = readme_file.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"Could not read {readme_file}: {exc}"

    header_match = re.search(r"^##\s+", content, re.MULTILINE)
    metadata_block = content if header_match is None else content[: header_match.start()]

    fields: dict[str, str] = {}
    for line in metadata_block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("**"):
            continue

        key_end = stripped.find("**:")
        if key_end < 2:
            continue

        raw_key = stripped[2:key_end].strip().lower()
        raw_value = stripped[key_end + 3 :].strip()

        if raw_value.startswith("`"):
            raw_value = raw_value[1:]
        if raw_value.endswith("`"):
            raw_value = raw_value[:-1]

        raw_value = raw_value.strip()
        fields[raw_key] = raw_value

    missing = [field for field in README_REQUIRED_FIELDS if field not in fields]
    if missing:
        return None, f"Missing README field(s): {', '.join(missing)}"

    if fields["mod_slug"] != mod_slug:
        return (
            None,
            f"README mod_slug '{fields['mod_slug']}' does not match folder '{mod_slug}'",
        )

    return fields, None


def check_uasset_uexp_pair_integrity(mod_data_files: list[Path]) -> tuple[bool, str | None]:
    """Ensure .uasset and .uexp files are paired for every matching stem."""
    file_stems_by_parent: dict[Path, dict[str, set[str]]] = {}

    for file_path in mod_data_files:
        suffix = file_path.suffix.lower()
        if suffix not in {".uasset", ".uexp"}:
            continue

        parent_map = file_stems_by_parent.setdefault(file_path.parent, {})
        suffixes = parent_map.setdefault(file_path.stem, set())
        suffixes.add(suffix)

    for parent_path, stem_map in file_stems_by_parent.items():
        for stem_name, suffixes in stem_map.items():
            if suffixes != {".uasset", ".uexp"}:
                return (
                    False,
                    f"Missing .uasset/.uexp pair for '{stem_name}' in '{parent_path}'",
                )

    return True, None


def is_level_two_json(relative_path: str) -> bool:
    """Check if a data file path is level-2 JSON path: Category/File.json."""
    path_parts = Path(relative_path).parts
    return len(path_parts) == 2 and relative_path.lower().endswith(".json")


def to_current_file(relative_path: str) -> str | None:
    """Convert relative path to EXMOD CurrentFile format for level-2 JSON files."""
    if not is_level_two_json(relative_path):
        return None
    category, filename = relative_path.split("/", 1)
    return f"{category}-{filename}"


def build_exmod_rows(
    mod_json_files: list[Path],
    mod_data_root: Path,
    baseline_data_path: Path,
) -> tuple[list[dict] | None, str | None]:
    """Build EXMOD Rows from changed level-2 JSON files only."""
    rows: list[dict] = []

    for json_file in mod_json_files:
        relative_path = json_file.relative_to(mod_data_root).as_posix()
        current_file = to_current_file(relative_path)
        if current_file is None:
            continue

        baseline_file = baseline_data_path / relative_path
        if not baseline_file.exists():
            return None, f"Missing baseline required for EXMOD row generation: {relative_path}"

        mod_data, mod_error = load_json_file(json_file)
        if mod_error:
            return None, mod_error

        baseline_data, baseline_error = load_json_file(baseline_file)
        if baseline_error:
            return None, baseline_error

        minimized_payload, is_changed = build_minimized_payload(mod_data, baseline_data)
        if not is_changed or minimized_payload is None:
            continue

        if not isinstance(minimized_payload, dict):
            return None, f"Unsupported JSON diff payload for EXMOD row: {relative_path}"

        row: dict = {"CurrentFile": current_file}
        defaults = minimized_payload.get("Defaults")
        if defaults is not None:
            row["Defaults"] = defaults

        file_items = minimized_payload.get("Rows")
        if not isinstance(file_items, list):
            return None, f"Changed JSON file has no Rows list for EXMOD conversion: {relative_path}"

        row["File_Items"] = file_items
        rows.append(row)

    rows.append({"CurrentFile": "EndOfMod"})
    return rows, None


def get_required_exmodz_asset_paths(
    canonical_relative_files: list[str],
) -> list[str]:
    """Return relative data paths that must be physically included in EXMODZ.

    Includes all .uasset/.uexp files and any files beyond level-2 depth.
    """
    required_paths: list[str] = []

    for relative_path in canonical_relative_files:
        suffix = Path(relative_path).suffix.lower()
        depth = len(Path(relative_path).parts)

        if suffix in {".uasset", ".uexp"} or depth > 2:
            required_paths.append(relative_path)

    required_paths.sort()
    return required_paths


def build_exmod_payload(
    mod_slug: str,
    readme_fields: dict[str, str],
    rows: list[dict],
) -> tuple[dict | None, str | None]:
    """Build EXMOD payload from README metadata and generated rows."""
    metadata_data, metadata_error = load_json_file(METADATA_FILE)
    if metadata_error:
        return None, metadata_error

    if not isinstance(metadata_data, dict):
        return None, f"Unexpected metadata format in {METADATA_FILE}"

    author = metadata_data.get("author")
    if not isinstance(author, str):
        author = ""

    payload = {
        "name": readme_fields["name"],
        "author": author,
        "version": readme_fields["version"],
        "week": readme_fields["week compatibility"],
        "fileName": f"{mod_slug}",
        "imageURL": readme_fields["image url"],
        "readmeURL": readme_fields["readme url"],
        "description": readme_fields["description"],
        "Level2": "True",
        "Rows": rows,
    }
    return payload, None


def upsert_exmodz_for_mod(
    mod_slug: str,
    exmod_payload: dict,
    required_asset_paths: list[str],
) -> tuple[bool, str | None]:
    """Create or update one EXMODZ archive with EXMOD and required physical assets."""
    exmodz_file = MODS_EXMODZ_DIR / f"{mod_slug}.EXMODZ"
    mod_data_root = MODS_DIR / mod_slug / "data"

    MODS_EXMODZ_DIR.mkdir(parents=True, exist_ok=True)

    exmod_entry_name = f"Extracted Mods/{mod_slug}.EXMOD"
    exmod_json = json.dumps(exmod_payload, ensure_ascii=False, indent=2) + "\n"

    try:
        with zipfile.ZipFile(
            exmodz_file,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(exmod_entry_name, exmod_json)

            for relative_path in required_asset_paths:
                source_file = mod_data_root / relative_path
                if not source_file.exists():
                    return False, f"Required EXMODZ asset is missing: {source_file}"
                archive.write(source_file, arcname=f"{mod_slug}/data/{relative_path}")
    except OSError as exc:
        return False, f"Failed to write EXMODZ archive {exmodz_file}: {exc}"

    return True, str(exmodz_file)


def open_for_review(file_path: Path) -> None:
    """Open a file in Notepad++ when available, fallback to OS default app."""
    try:
        notepadpp = resolve_notepadpp_path()
        if notepadpp:
            subprocess.Popen([notepadpp, "-nosession", str(file_path)])
            return

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


def validate_single_mod(
    mod_slug: str,
    baseline_data_path: Path,
    baseline_sequence: str,
    *,
    no_prompt: bool,
) -> int:
    """Validate one mod end-to-end and build EXMODZ/pak on success."""
    print(f"\n=== Mod: {mod_slug} ===")

    print("\n[3/8] Scanning mod files...")
    mod_data_files = find_mod_data_files(mod_slug)
    mod_json_files = [path for path in mod_data_files if path.suffix.lower() == ".json"]
    if not mod_json_files:
        print(f"No JSON files found in mods/{mod_slug}/data/")
        return 0
    print(f"✓ Found {len(mod_json_files)} JSON file(s) and {len(mod_data_files)} data file(s)")

    print("\n[4/8] Validating and minimizing files...")
    scanned_files = 0
    changed_files = 0
    missing_baseline_files = 0
    skipped_or_error_files = 0
    approved_replacements = 0
    rejected_replacements = 0
    user_quit_early = False

    mod_data_root = MODS_DIR / mod_slug / "data"

    for mod_file in mod_json_files:
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

        if no_prompt:
            is_valid_sidecar, sidecar_error = validate_reviewed_sidecar(sidecar_file, baseline_data)
            if not is_valid_sidecar:
                print(f"  - Skipped (error): {sidecar_error}")
                skipped_or_error_files += 1
                sidecar_file.unlink(missing_ok=True)
                continue

            replace_ok, replace_error = replace_original_with_sidecar(mod_file, sidecar_file)
            if not replace_ok:
                print(f"  - Skipped (error): {replace_error}")
                skipped_or_error_files += 1
                continue

            approved_replacements += 1
            print(f"    Auto-approved and replaced: {relative_path}")
            continue

        print(f"  - Review required: {relative_path}")
        open_for_review(mod_file)
        open_for_review(sidecar_file)

        while True:
            decision = ask_review_decision(relative_path)
            if decision == "q":
                print("User requested to quit review early.")
                user_quit_early = True
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
            print(f"    Approved and replaced: {relative_path}")
            break

        if user_quit_early:
            break

    if user_quit_early:
        print(
            "Validation aborted early by user. Skipping EXMODZ, file-list updates, and pak creation."
        )
        return 0

    print("\n[5/8] Running strict validation checks...")
    if missing_baseline_files > 0 or skipped_or_error_files > 0:
        print(
            "Error: Validation did not fully pass. "
            "Fix missing baseline or skipped/error files before EXMODZ update.",
            file=sys.stderr,
        )
        return 1

    pair_ok, pair_error = check_uasset_uexp_pair_integrity(mod_data_files)
    if not pair_ok:
        print(f"Error: {pair_error}", file=sys.stderr)
        return 1
    print("✓ Strict validation checks passed")

    canonical_relative_files = build_canonical_relative_file_list(mod_data_files, mod_data_root)

    print("\n[6/8] Creating or updating EXMODZ...")
    readme_fields, readme_parse_error = parse_readme_metadata(mod_slug)
    if readme_parse_error:
        print(f"Error: {readme_parse_error}", file=sys.stderr)
        return 1
    assert readme_fields is not None

    exmod_rows, rows_error = build_exmod_rows(mod_json_files, mod_data_root, baseline_data_path)
    if rows_error:
        print(f"Error: {rows_error}", file=sys.stderr)
        return 1
    assert exmod_rows is not None

    exmod_payload, payload_error = build_exmod_payload(mod_slug, readme_fields, exmod_rows)
    if payload_error:
        print(f"Error: {payload_error}", file=sys.stderr)
        return 1
    assert exmod_payload is not None

    required_asset_paths = get_required_exmodz_asset_paths(canonical_relative_files)
    exmodz_ok, exmodz_output = upsert_exmodz_for_mod(
        mod_slug,
        exmod_payload,
        required_asset_paths,
    )
    if not exmodz_ok:
        print(f"Error: {exmodz_output}", file=sys.stderr)
        return 1
    print(f"✓ EXMODZ upserted: {exmodz_output}")

    print("\n[7/8] Validating file lists...")
    metadata_ok, metadata_error = update_metadata_mod_index(
        mod_slug,
        baseline_sequence,
        canonical_relative_files,
    )
    if not metadata_ok:
        print(f"Error: {metadata_error}", file=sys.stderr)
        return 1
    print("✓ metadata.json file list validated")

    readme_ok, readme_changed, readme_error = update_mod_readme_file_list(
        mod_slug,
        canonical_relative_files,
    )
    if not readme_ok:
        print(f"Error: {readme_error}", file=sys.stderr)
        return 1
    if readme_changed:
        print(f"✓ README File List auto-fixed: mods/{mod_slug}/README.md")
    else:
        print(f"✓ README File List already accurate: mods/{mod_slug}/README.md")

    print("\n[8/8] Creating pak file...")
    pak_ok, pak_output = create_pak_for_mod(mod_slug)
    if not pak_ok:
        print(f"Error: Failed to create pak for mod '{mod_slug}'", file=sys.stderr)
        return 1
    print(f"✓ Pak created: {pak_output}")

    print("\nSummary")
    print("-" * 60)
    print(f"Mod: {mod_slug}")
    print(f"Baseline folder: {baseline_sequence}")
    print(f"Files scanned: {scanned_files}")
    print(f"Files changed: {changed_files}")
    print(f"Files missing baseline: {missing_baseline_files}")
    print(f"Files skipped/errors: {skipped_or_error_files}")
    print(f"Approved replacements: {approved_replacements}")
    print(f"Rejected replacements: {rejected_replacements}")
    print("=" * 60)

    return 0


def main() -> int:
    """Main execution flow."""
    parser = argparse.ArgumentParser(description="Validate Icarus mod JSON and package outputs")
    parser.add_argument(
        "--noprompt",
        action="store_true",
        help="Run non-interactively: auto-approve changes and process all mods",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Alias for --noprompt",
    )
    args = parser.parse_args()
    no_prompt = args.noprompt or args.all

    print("=" * 60)
    print("Icarus Mod JSON Validator")
    print("=" * 60)

    print("\n[1/8] Discovering mods...")
    mod_names = discover_mods()
    if no_prompt:
        if not mod_names:
            print("Error: No mods found under mods/", file=sys.stderr)
            return 1
        print(f"✓ Selected all mods ({len(mod_names)}): {', '.join(mod_names)}")
    else:
        selected_mod = select_mod(mod_names)
        if selected_mod is None:
            print("No mod selected. Exiting.")
            return 0
        mod_names = [selected_mod]
        print(f"✓ Selected mod: {selected_mod}")

    print("\n[2/8] Resolving baseline folder...")
    baseline_data_path, baseline_sequence, baseline_error = resolve_baseline_folder()
    if baseline_error:
        print(f"Error: {baseline_error}", file=sys.stderr)
        return 1
    assert baseline_data_path is not None
    assert baseline_sequence is not None
    print(f"✓ Baseline: .icarus-data/{baseline_sequence}/data/")

    failed_mods: list[str] = []
    for mod_slug in mod_names:
        result = validate_single_mod(
            mod_slug,
            baseline_data_path,
            baseline_sequence,
            no_prompt=no_prompt,
        )
        if result != 0:
            failed_mods.append(mod_slug)
            if not no_prompt:
                return result

    if failed_mods:
        print(f"\nFailed mods: {', '.join(failed_mods)}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
