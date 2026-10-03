# Releasing RUDRA

This is for maintainers. Users only download the installer from the Releases page.

## What a release contains

| File | What it is |
|---|---|
| `RUDRA-Setup-<version>-win64.exe` | The per-user Windows installer (Inno Setup), holding the PyInstaller build of `RUDRA.exe` and `RUDRA-CLI.exe` |
| `SHA256SUMS.txt` | The installer's SHA-256 checksum |
| `TEST_REPORT_<version>.md` | The manual cross-topic test report for this version |

The release notes are the `## <version>` section of `CHANGELOG.md`, followed by download
notes (`windows/release_notes.py`).

## Version numbers

`app/version.py` holds `VERSION`; `pyproject.toml` must hold the same. The tag is
`v<VERSION>` — the release workflow refuses a tag that does not match. The update notice
compares this version with the latest published, non-prerelease GitHub release.

## Steps

1. Update `VERSION` in `app/version.py` and `pyproject.toml`, add a `## <version>`
   section to `CHANGELOG.md`, and write `docs/TEST_REPORT_<version>.md` with the manual
   test scope and results.
2. Build and test locally (see [BUILDING.md](BUILDING.md)):

   ```powershell
   .venv\Scripts\python.exe -m pytest -p no:cacheprovider
   .venv\Scripts\python.exe windows\build.py --skip-tests --installer
   .venv\Scripts\python.exe windows\test_installer.py dist\installer\RUDRA-Setup-<version>-win64.exe
   ```

3. Commit, then tag and push the tag:

   ```powershell
   git tag v<version>
   git push origin v<version>
   ```

4. The **Release** workflow (`.github/workflows/release.yml`) runs on a Windows runner:
   installs the pinned requirements, checks the tag against `app/version.py`, runs the
   test suite, builds and self-tests the application, checks the package for private
   files, builds the installer, runs the installer test (silent install, offline use,
   upgrade to a higher-versioned installer of the same program, uninstall with the user's
   data kept), verifies `SHA256SUMS.txt`, and creates a **draft** release with the two
   files and the release notes.
5. Review the draft on GitHub — download the installer, check its checksum, install it —
   and **Publish** it. Only then does RUDRA's update notice offer it. Never mark a build
   that failed any step as a release; pre-releases are ignored by the update notice.

## Checks the build makes

`windows/build.py` refuses to build over a `dist\RUDRA` folder holding user data, and
after building it checks that the package holds no database, document, log, backup or
data folder, no key or secret file and no path of the build machine. The installer test runs with no
Python on `PATH` and with the network cut off for the self-test.

## Continuous integration

The **CI** workflow (`.github/workflows/ci.yml`) runs the test suite on every push to
`main` and on every pull request. Tests that need Windows OCR skip themselves on machines
without an OCR language.
