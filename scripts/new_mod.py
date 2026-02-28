import os
import re
import json
from datetime import datetime, timedelta
from pathlib import Path


def to_kebab_case(name: str) -> str:
    # Replace any non-alphanumeric characters (including spaces and underscores) with hyphens
    name = re.sub(r"[^a-zA-Z0-9]+", "-", name)
    # Remove leading and trailing hyphens, and convert to lowercase
    return name.strip("-").lower()


def get_week_from_metadata() -> str:
    metadata_file = Path(__file__).resolve().parent.parent / "metadata.json"
    try:
        with metadata_file.open("r", encoding="utf-8") as f:
            metadata = json.load(f)
        week = metadata.get("week") if isinstance(metadata, dict) else None
        return str(week) if week else "Unknown"
    except (OSError, json.JSONDecodeError):
        return "Unknown"


def main():
    # Prompt the user for the mod name
    raw_name = input("Enter mod name: ")

    # Process into kebab-case
    kebab_name = to_kebab_case(raw_name)

    if not kebab_name:
        print("Error: Invalid mod name provided.")
        return

    print(f"Formatted mod name: {kebab_name}")

    # Define the target paths
    base_dir = "mods"
    mod_dir = os.path.join(base_dir, kebab_name)
    data_dir = os.path.join(mod_dir, "data")

    # Create the directories
    try:
        # exist_ok=False will raise an error if the Mod folder already exists
        # using os.makedirs on data_dir will create mods/<name>/ as well
        os.makedirs(data_dir, exist_ok=False)
        print(f"Successfully created: {data_dir}")

        # Create README.md with template
        readme_path = os.path.join(mod_dir, "README.md")
        readme_url = f"`https://raw.githubusercontent.com/TheLysdexicOne/Icarus-Mods/main/mods/{kebab_name}/README.md`"

        # Get current date/time in EST (offset by -5 hours from UTC)
        est_offset = timedelta(hours=-5)
        date_created = (datetime.now() + est_offset).strftime("%Y-%m-%d %H:%M:%S EST")

        # Get current week number from metadata
        week_num = get_week_from_metadata()

        readme_content = f"""# Mod Info

**Name**:
**mod_slug**: `{kebab_name}`
**Author**: TheLysdexicOne
**Version**: 1.0
**Description**: A brief description of the mod and its features.
**Date Created**: `{date_created}`
**Date Updated**: `This will be updated when stage_mods.py is run`
**Week Compatibility**: `{week_num}`
**Image URL**:
**Readme URL**: {readme_url}

## File List

- This will be updated with `stage_mods.py`
"""
        with open(readme_path, "w") as f:
            f.write(readme_content)
        print(f"Successfully created: {readme_path}")

    except FileExistsError:
        print(f"Error: A mod with the name '{kebab_name}' already exists at {mod_dir}")
    except Exception as e:
        print(f"Error creating directories or files: {e}")


if __name__ == "__main__":
    main()
