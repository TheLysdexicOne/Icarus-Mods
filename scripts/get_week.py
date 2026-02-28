import urllib.request
import re
import sys


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


def main():
    week = get_week()
    if week:
        print(week)


if __name__ == "__main__":
    main()
