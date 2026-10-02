"""Print the release notes of one version, from CHANGELOG.md, followed by the download notes.

    python windows/release_notes.py 1.1.0 > release-notes.md
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(version: str, changelog: str) -> str:
    """The text under '## <version>' up to the next '## ' heading."""
    lines = changelog.splitlines()
    try:
        start = lines.index(f"## {version}") + 1
    except ValueError:
        sys.exit(f"CHANGELOG.md has no '## {version}' section.")
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).strip()


def notes(version: str) -> str:
    body = section(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    return (f"{body}\n\n### Download\n\n"
            f"- `RUDRA-Setup-{version}-win64.exe` — the installer for Windows (64-bit).\n"
            "- `SHA256SUMS.txt` — its SHA-256 checksum. Check it with "
            f"`Get-FileHash RUDRA-Setup-{version}-win64.exe -Algorithm SHA256`.\n\n"
            "Your knowledge base is kept when you upgrade. See the README for installation, "
            "backup and restore.\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python windows/release_notes.py VERSION")
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1]))
