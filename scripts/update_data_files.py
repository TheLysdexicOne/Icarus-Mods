"""Update Icarus game data files and synchronize metadata.

This script extracts the latest Icarus game data files from the game's data.pak
using UnrealPak, stages them in the .icarus-data/ directory, fetches the current
week number from Steam news, and updates metadata.json with the sync timestamp
and week number.
"""

import subprocess
import json
import shutil
import sys
import os
import urllib.request
import re
from datetime import datetime
from pathlib import Path


# Configuration
UNREALPAK_PATH = r"UnrealPak\Engine\Binaries\Win64\UnrealPak.exe"
ICARUS_DATA_PAK = r"D:\SteamLibrary\steamapps\common\Icarus\Icarus\Content\Data\data.pak"
ICARUS_VERSION_JSON = r"D:\SteamLibrary\steamapps\common\Icarus\Icarus\Config\version.json"
OUTPUT_DIR = ".icarus-data"
METADATA_FILE = "metadata.json"
TEMP_EXTRACT_DIR = ".icarus-data-temp"
MAX_VERSIONS = 5


def get_game_version() -> str | None:
    """Fetch the current Icarus game version from version.json.

    Returns:
        str: Version string (e.g., "2.4.1.149492") or None if reading fails.
    """
    try:
        with open(ICARUS_VERSION_JSON, "r") as f:
            data = json.load(f)
            version = data.get("Version", {})
            major = version.get("Major", 0)
            minor = version.get("Minor", 0)
            patch = version.get("Patch", 0)
            changelist = version.get("Changelist")
            if changelist is None:
                changelist = data.get("Data", {}).get("Changelist", 0)
            return f"{major}.{minor}.{patch}.{changelist}"
    except Exception as e:
        print(f"Error reading version.json: {e}", file=sys.stderr)
        return None


def get_week() -> str | None:
    """Fetch the current Icarus week number from Steam news.

    Returns:
        str: Week number string (e.g., "Week 221") or None if fetch fails.
    """
    url = "https://store.steampowered.com/news/app/1149460?updates=true"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    try:
        with urllib.request.urlopen(req) as response:
            # We ignore decode errors in case of any weird characters on the page
            content = response.read().decode("utf-8", errors="ignore")

        match = re.search(r"(Week\s+\d+)", content)
        if match:
            return match.group(1)
        else:
            print("Could not find 'Week ...'", file=sys.stderr)
            return None

    except Exception as e:
        print(f"Error fetching data: {e}", file=sys.stderr)
        return None


def validate_prerequisites() -> bool:
    """Validate that required files and tools exist.

    Returns:
        bool: True if all prerequisites are valid, False otherwise.
    """
    # Check UnrealPak.exe
    if not Path(UNREALPAK_PATH).exists():
        print(f"Error: UnrealPak.exe not found at {UNREALPAK_PATH}", file=sys.stderr)
        return False

    # Check data.pak
    if not Path(ICARUS_DATA_PAK).exists():
        print(f"Error: Icarus data.pak not found at {ICARUS_DATA_PAK}", file=sys.stderr)
        return False

    # Check version.json
    if not Path(ICARUS_VERSION_JSON).exists():
        print(f"Error: Icarus version.json not found at {ICARUS_VERSION_JSON}", file=sys.stderr)
        return False

    return True


def extract_pak_file() -> bool:
    """Extract the Icarus data.pak file using UnrealPak.

    Returns:
        bool: True if extraction succeeded, False otherwise.
    """
    print(f"Extracting {ICARUS_DATA_PAK}...")

    # Clean up temp directory if it exists from a previous failed attempt
    if Path(TEMP_EXTRACT_DIR).exists():
        shutil.rmtree(TEMP_EXTRACT_DIR)

    Path(TEMP_EXTRACT_DIR).mkdir(exist_ok=True)

    try:
        # Run UnrealPak with -Extract flag
        # Use absolute path for temp directory to ensure extraction works correctly
        abs_temp_dir = Path(TEMP_EXTRACT_DIR).absolute()

        result = subprocess.run(
            [UNREALPAK_PATH, ICARUS_DATA_PAK, "-Extract", str(abs_temp_dir)],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        if result.returncode != 0:
            print(
                f"Error: UnrealPak extraction failed (exit code {result.returncode})",
                file=sys.stderr,
            )
            if result.stderr:
                print(f"UnrealPak stderr: {result.stderr}", file=sys.stderr)
            return False

        print("Extraction completed successfully")
        return True

    except subprocess.TimeoutExpired:
        print("Error: UnrealPak extraction timed out (exceeded 5 minutes)", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: Failed to run UnrealPak: {e}", file=sys.stderr)
        return False


def parse_version(version_str: str) -> list[int]:
    """Parse a version string like '221.1' into a list of ints [221, 1] for sorting."""
    # Split by '.' and convert to int
    parts = []
    for part in version_str.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return parts


def determine_target_sequence(root_dir: Path, week: str) -> str:
    """Determine the next valid target sequence folder name.

    If week hasn't been used yet, returns 'week'.
    If 'week' exists, checks for 'week.1', 'week.2', etc and returns the next available.
    """
    base_folder = root_dir / week
    if not base_folder.exists():
        return week

    # Check for increments
    increment = 1
    while True:
        hotfix_folder_name = f"{week}.{increment}"
        if not (root_dir / hotfix_folder_name).exists():
            return hotfix_folder_name
        increment += 1


def stage_extracted_data(target_sequence: str) -> bool:
    """Move extracted data to .icarus-data/<target_sequence>/data/ directory.

    UnrealPak extracts files directly to the target directory, creating
    subdirectories like Accolades/, AI/, etc. directly.

    Args:
        target_sequence: The sequence name (e.g. "221", "221.1")

    Returns:
        bool: True if staging succeeded, False otherwise.
    """
    print(f"Staging extracted data files to {OUTPUT_DIR}/{target_sequence}/data/ ...")

    temp_path = Path(TEMP_EXTRACT_DIR)

    # Verify that the temp directory exists and has contents
    if not temp_path.exists():
        print(f"Error: Extraction directory {TEMP_EXTRACT_DIR} not found", file=sys.stderr)
        return False

    # Check if directory has any contents by checking for subdirectories
    # (UnrealPak creates subdirectories like Accolades/, AI/, etc.)
    try:
        items = os.listdir(temp_path)
        if not items:
            print(f"Error: No files extracted to {TEMP_EXTRACT_DIR}", file=sys.stderr)
            return False
    except OSError as e:
        print(f"Error: Cannot read extraction directory: {e}", file=sys.stderr)
        return False

    try:
        output_path = Path(OUTPUT_DIR)

        # Cleanup legacy direct-extraction structure in .icarus-data/
        # if there are folders like 'AI/' alongside '221/' this removes them
        if output_path.exists():
            for item in output_path.iterdir():
                if item.is_dir() and item.name not in ["data"]:
                    # If this doesn't look like a sequence folder
                    if not item.name.replace(".", "").isdigit():
                        print(f"Removing old legacy folder/file: {item.name}")
                        shutil.rmtree(item) if item.is_dir() else item.unlink()

        output_path.mkdir(exist_ok=True)

        target_dir = output_path / target_sequence
        if target_dir.exists():
            # In case somehow we picked an existing directory, we won't nuke it here.
            # but ideally determine_target_sequence ensures uniqueness.
            shutil.rmtree(target_dir)

        target_dir.mkdir(parents=True)
        target_data_dir = target_dir / "data"

        # Rename the temp extraction directory to the output directory
        temp_path.rename(target_data_dir)

        # Copy version.json
        shutil.copy2(ICARUS_VERSION_JSON, target_dir / "version.json")

        print(f"Data staged to {target_data_dir}/")
        return True

    except Exception as e:
        print(f"Error: Failed to stage data: {e}", file=sys.stderr)
        return False


def prune_old_versions():
    """Keep only the new MAX_VERSIONS sequences in .icarus-data/ and delete the rest."""
    output_path = Path(OUTPUT_DIR)
    if not output_path.exists():
        return

    # Find sequence folders (folders where the name is digit or digit.digit)
    sequences = []
    for item in output_path.iterdir():
        if item.is_dir() and item.name.replace(".", "").isdigit():
            sequences.append(item.name)

    # Sort descending
    sequences.sort(key=parse_version, reverse=True)

    if len(sequences) > MAX_VERSIONS:
        print(f"Pruning older versions (keeping newest {MAX_VERSIONS})...")
        to_delete = sequences[MAX_VERSIONS:]
        for old_seq in to_delete:
            old_dir = output_path / old_seq
            print(f"Deleting {old_dir}/")
            shutil.rmtree(old_dir)


def update_metadata(week: str | None, game_version: str, target_sequence: str) -> bool:
    """Update metadata.json with current week, version details, and sync timestamp.

    Args:
        week: Week number string (e.g., "221") or None if fetch failed.
        game_version: Extracted Major.Minor.Patch.Changelist string
        target_sequence: The selected sequence string for the latest extraction

    Returns:
        bool: True if metadata was updated successfully, False otherwise.
    """
    print("Updating metadata.json...")

    try:
        # Load current metadata
        with open(METADATA_FILE, "r") as f:
            metadata = json.load(f)

        # Update fields
        metadata["last_data_sync"] = datetime.now().isoformat()

        if week:
            # Extract just the number from "Week 221" format
            week_num = week.split()[-1] if "Week" in week else week
            metadata["week"] = week_num
            print(f"Week updated to: {week_num}")
        else:
            print("Warning: Could not fetch current week; keeping existing week number")

        if game_version:
            metadata["game_version"] = game_version

        metadata["latest_data_folder"] = target_sequence

        # Write to temporary file first (atomic operation)
        temp_file = f"{METADATA_FILE}.tmp"
        with open(temp_file, "w") as f:
            json.dump(metadata, f, indent=2)

        # Atomic rename
        Path(temp_file).replace(METADATA_FILE)

        print(f"Sync timestamp: {metadata['last_data_sync']}")
        return True

    except FileNotFoundError:
        print(f"Error: {METADATA_FILE} not found", file=sys.stderr)
        return False
    except json.JSONDecodeError:
        print(f"Error: {METADATA_FILE} is not valid JSON", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: Failed to update metadata: {e}", file=sys.stderr)
        return False


def main():
    """Main execution flow."""
    print("=" * 60)
    print("Icarus Data Update")
    print("=" * 60)

    # Step 1: Validate prerequisites
    print("\n[1/6] Validating prerequisites...")
    if not validate_prerequisites():
        return 1
    print("✓ Prerequisites validated")

    # Fetch game version
    game_version = get_game_version()
    if not game_version:
        print("Error: Could not determine game version from version.json", file=sys.stderr)
        return 1
    print(f"Detected Game Version: {game_version}")

    # Fetch week
    week = get_week()
    week_num = week.split()[-1] if week and "Week" in week else week

    if week_num is None:
        print("Error: Could not determine week number", file=sys.stderr)
        return 1

    # Open metadata and evaluate skip criteria
    try:
        with open(METADATA_FILE, "r") as f:
            metadata = json.load(f)
            stored_week = metadata.get("week")
            stored_version = metadata.get("game_version")

            if stored_week == week_num and stored_version == game_version:
                print(
                    f"\n=> Local data is already up-to-date with Week {week_num} (Version {game_version}). Extraction skipped."
                )
                return 0
    except (FileNotFoundError, json.JSONDecodeError):
        metadata = {}

    target_sequence = determine_target_sequence(Path(OUTPUT_DIR), week_num)
    print(f"Target Sequence Folder for extraction: {target_sequence}")

    # Step 2: Extract pak file
    print("\n[2/6] Extracting pak file...")
    if not extract_pak_file():
        return 1
    print("✓ Pak file extracted")

    # Step 3: Stage extracted data
    print("\n[3/6] Staging extracted data...")
    if not stage_extracted_data(target_sequence):
        return 1
    print(f"✓ Data staged to {target_sequence}")

    # Step 4: Prune old versions
    print("\n[4/6] Pruning old versions...")
    prune_old_versions()
    print("✓ Kept latest versions only")

    # Step 5: Update metadata
    print("\n[5/6] Updating metadata...")
    if not update_metadata(week_num, game_version, target_sequence):
        return 1
    print("✓ Metadata updated")

    print("\n" + "=" * 60)
    print(f"✓ Data update completed successfully -> {target_sequence}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
