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
from datetime import datetime
from pathlib import Path

from get_week import get_week


# Configuration
UNREALPAK_PATH = r"UnrealPak\Engine\Binaries\Win64\UnrealPak.exe"
ICARUS_DATA_PAK = r"D:\SteamLibrary\steamapps\common\Icarus\Icarus\Content\Data\data.pak"
OUTPUT_DIR = ".icarus-data"
METADATA_FILE = "metadata.json"
TEMP_EXTRACT_DIR = ".icarus-data-temp"


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


def stage_extracted_data() -> bool:
    """Move extracted data to .icarus-data/ directory.

    UnrealPak extracts files directly to the target directory, creating
    subdirectories like Accolades/, AI/, etc. directly.

    Returns:
        bool: True if staging succeeded, False otherwise.
    """
    print("Staging extracted data files...")

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
        # Remove existing .icarus-data/ if present
        if Path(OUTPUT_DIR).exists():
            shutil.rmtree(OUTPUT_DIR)

        # Rename the temp extraction directory to the output directory
        temp_path.rename(OUTPUT_DIR)

        print(f"Data staged to {OUTPUT_DIR}/")
        return True

    except Exception as e:
        print(f"Error: Failed to stage data: {e}", file=sys.stderr)
        return False


def update_metadata(week: str | None) -> bool:
    """Update metadata.json with current week and sync timestamp.

    Args:
        week: Week number string (e.g., "Week 221") or None if fetch failed.

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
            week_num = week.split()[-1] if week else metadata.get("week", "")
            metadata["week"] = week_num
            print(f"Week updated to: {week_num}")
        else:
            print("Warning: Could not fetch current week; keeping existing week number")

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
    print("\n[1/5] Validating prerequisites...")
    if not validate_prerequisites():
        return 1
    print("✓ Prerequisites validated")

    # Step 2: Extract pak file
    print("\n[2/5] Extracting pak file...")
    if not extract_pak_file():
        return 1
    print("✓ Pak file extracted")

    # Step 3: Stage extracted data
    print("\n[3/5] Staging extracted data...")
    if not stage_extracted_data():
        return 1
    print("✓ Data staged")

    # Step 4: Fetch current week
    print("\n[4/5] Fetching current week...")
    week = get_week()
    if week:
        print(f"✓ Week fetched: {week}")
    else:
        print("⚠ Could not fetch week (network error or page change)")

    # Step 5: Update metadata
    print("\n[5/5] Updating metadata...")
    if not update_metadata(week):
        return 1
    print("✓ Metadata updated")

    print("\n" + "=" * 60)
    print("✓ Data update completed successfully")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
