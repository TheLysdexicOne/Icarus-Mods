"""Create .pak files for all mods using UnrealPak.

For each mod in the mods/ directory, creates a .pak file in the mods-pak/ directory.
"""

import subprocess
import sys
from pathlib import Path
import tempfile


# Configuration
ROOT_DIR = Path(__file__).resolve().parent.parent
UNREALPAK_PATH = ROOT_DIR / "UnrealPak" / "Engine" / "Binaries" / "Win64" / "UnrealPak.exe"
MODS_DIR = ROOT_DIR / "mods"
OUTPUT_DIR = ROOT_DIR / "mods-pak"


def validate_prerequisites() -> bool:
    """Validate that required tools and directories exist.

    Returns:
        bool: True if all prerequisites are valid, False otherwise.
    """
    # Check UnrealPak.exe
    if not UNREALPAK_PATH.exists():
        print(f"Error: UnrealPak.exe not found at {UNREALPAK_PATH}", file=sys.stderr)
        return False

    # Check mods directory exists
    if not MODS_DIR.exists():
        print(f"Error: mods directory not found at {MODS_DIR}", file=sys.stderr)
        return False

    return True


def get_mod_directories() -> list:
    """Get list of mod directories.

    Returns:
        list: List of mod directory paths.
    """
    mod_dirs = [d for d in MODS_DIR.iterdir() if d.is_dir()]
    return sorted(mod_dirs)


def create_response_file(mod_dir: Path, temp_dir: Path) -> str:
    """Create a response file for UnrealPak.

    Args:
        mod_dir: Path to the mod directory.
        temp_dir: Path to temp directory for response file.

    Returns:
        str: Absolute path to the response file.
    """
    response_file = Path(temp_dir) / "response.txt"

    # Get the absolute path to the data directory
    data_dir = (mod_dir / "data").absolute()

    # Response file format for UnrealPak
    # Each line is: "source_path" "mount_path"
    with response_file.open("w", encoding="utf-8") as f:
        if data_dir.exists():
            for item in data_dir.rglob("*"):
                if item.is_file():
                    # Get relative path from data directory
                    rel_path = item.relative_to(data_dir)
                    # Mount path uses forward slashes
                    mount_path = "/" + str(rel_path).replace("\\", "/")
                    # Write: "absolute_file_path" "mount_path"
                    f.write(f'"{item.absolute()}" "{mount_path}"\n')

    # Return absolute path to response file
    return str(response_file.absolute())


def create_pak_file(mod_dir: Path, mod_name: str) -> bool:
    """Create a .pak file for a mod using UnrealPak.

    Args:
        mod_dir: Path to the mod directory.
        mod_name: Name of the mod (folder name).

    Returns:
        bool: True if pak creation succeeded, False otherwise.
    """
    print(f"Creating pak for: {mod_name}")

    # Create output directory if it doesn't exist
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Output pak file path (use absolute path for UnrealPak)
    output_pak = (OUTPUT_DIR / f"{mod_name}.pak").absolute()

    # Create temporary directory for response file
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # Ensure mod_dir is absolute for UnrealPak
            abs_mod_dir = mod_dir.absolute()

            # Create response file
            response_file = create_response_file(abs_mod_dir, Path(temp_dir))

            # Check if there's anything to pack
            if not Path(response_file).stat().st_size > 0:
                print(f"  Warning: No files found in {mod_name}/data/", file=sys.stderr)
                return False

            # Run UnrealPak
            result = subprocess.run(
                [str(UNREALPAK_PATH), str(output_pak), f"-Create={response_file}"],
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout
            )

            if result.returncode != 0:
                print(
                    f"  Error: UnrealPak failed (exit code {result.returncode})",
                    file=sys.stderr,
                )
                if result.stderr:
                    print(f"  UnrealPak stderr: {result.stderr}", file=sys.stderr)
                return False

            print(f"  ✓ Created: {output_pak}")
            return True

        except subprocess.TimeoutExpired:
            print(f"  Error: UnrealPak timed out (exceeded 10 minutes)", file=sys.stderr)
            return False
        except Exception as e:
            print(f"  Error: Failed to create pak: {e}", file=sys.stderr)
            return False


def create_pak_for_mod(mod_name: str) -> tuple[bool, str | None]:
    """Create a .pak for one mod and return output path on success."""
    if not validate_prerequisites():
        return False, None

    mod_dir = MODS_DIR / mod_name
    if not mod_dir.exists() or not mod_dir.is_dir():
        print(f"Error: Mod not found: {mod_name}", file=sys.stderr)
        return False, None

    if not (mod_dir / "data").exists():
        print(f"Error: Mod data directory not found: {mod_dir / 'data'}", file=sys.stderr)
        return False, None

    success = create_pak_file(mod_dir, mod_name)
    if not success:
        return False, None

    return True, str(OUTPUT_DIR / f"{mod_name}.pak")


def main():
    """Main execution flow."""
    print("=" * 60)
    print("Icarus Mods Pak Creator")
    print("=" * 60)

    # Step 1: Validate prerequisites
    print("\n[1/3] Validating prerequisites...")
    if not validate_prerequisites():
        return 1
    print("✓ Prerequisites validated")

    # Step 2: Get mod directories
    print("\n[2/3] Scanning mod directories...")
    mod_dirs = get_mod_directories()
    if not mod_dirs:
        print("Warning: No mod directories found", file=sys.stderr)
        return 1
    print(f"✓ Found {len(mod_dirs)} mod(s)")

    # Step 3: Create pak files
    print("\n[3/3] Creating pak files...")
    failed_mods = []
    for mod_dir in mod_dirs:
        mod_name = mod_dir.name
        success = create_pak_file(mod_dir, mod_name)
        if not success:
            failed_mods.append(mod_name)

    # Summary
    print("\n" + "=" * 60)
    successful = len(mod_dirs) - len(failed_mods)
    print(f"✓ Created {successful}/{len(mod_dirs)} pak file(s)")
    if failed_mods:
        print(f"✗ Failed: {', '.join(failed_mods)}", file=sys.stderr)
        print("=" * 60)
        return 1
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
