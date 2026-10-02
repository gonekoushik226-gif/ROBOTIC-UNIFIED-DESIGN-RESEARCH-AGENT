"""Version constants.

Only versions of things that actually exist are defined here. The specification
requires several other version numbers (database schema, extraction pipeline,
knowledge schema, provider adapters). They are deliberately absent rather than set
to zero, because giving a version to a subsystem that does not exist would be a
false claim (master specification Part 5 section 242, "no pretend features").
"""

from typing import Final

APP_NAME: Final = "RUDRA"
APP_SHORT_NAME: Final = "RUDRA"
APP_FULL_NAME: Final = "ROBOTIC UNIFIED DESIGN RESEARCH AGENT"

#: Application version. 1.0.0 was the first public release.
VERSION: Final = "1.1.0"

#: Which edition of RUDRA this is, shown beside the version.
EDITION: Final = "for Windows"

#: Version of the configuration file format (Part 4 section 150).
#: Increment only together with a migration path for existing config files.
CONFIG_SCHEMA_VERSION: Final = 1

#: Where RUDRA is published. The update check reads only this repository's public
#: release information; the download page is where it sends the user. Change the
#: repository here and nowhere else.
GITHUB_REPOSITORY: Final = "gonekoushik226-gif/ROBOTIC-UNIFIED-DESIGN-RESEARCH-AGENT"
RELEASES_PAGE: Final = f"https://github.com/{GITHUB_REPOSITORY}/releases"
LATEST_RELEASE_API: Final = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
