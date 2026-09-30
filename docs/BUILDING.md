# Building, testing and releasing RUDRA

## Development setup

RUDRA runs from the source tree; it is not installed as a package.

```powershell
python -m venv .venv                                     # Python 3.14, 64-bit
.venv\Scripts\python.exe -m pip install -r requirements\build.txt
.venv\Scripts\python.exe -m app --help                   # the command line
.venv\Scripts\python.exe -m app.ui.gui                   # the desktop window
```

`python -m app` must be run from the repository root. Run from source, RUDRA's project
folder is the repository itself: `config\`, `data\` and `logs\` are created there and are
ignored by Git. Use `--project-root PATH` to work in another folder, for example a
throwaway one for experiments.

The only third-party runtime dependency is `pypdf`. Everything else is the Python
standard library or a component of Windows: the desktop window (Tkinter), the document
readers, OCR and PDF page rendering (Windows' own `Windows.Media.Ocr` and
`Windows.Data.Pdf`, reached through PowerShell), the Windows Credential Manager (through
`ctypes`), the formula renderer, the backup format, the update check and the optional AI
connections (HTTPS with `urllib`).

## Tests

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.venv\Scripts\python.exe -m pytest -p no:cacheprovider
```

Every test works in temporary folders on generated documents; the live knowledge
database is never opened. AI provider tests use recorded request and reply shapes with fake
keys and no network. Tests that need Windows OCR skip themselves where no OCR language is
installed. The suite takes several minutes. A few tests read one real textbook when the environment variable
`RUDRA_TEST_REAL_PDF` names it; without it they are skipped.

## The Windows application

```powershell
.venv\Scripts\python.exe windows\build.py --installer
```

`windows/build.py`:

1. refuses to build over a `dist\RUDRA` folder that holds RUDRA data;
2. runs the test suite (skip with `--skip-tests`);
3. builds `dist\RUDRA\` with PyInstaller from `windows\RUDRA.spec`: `RUDRA.exe` (the
   window) and `RUDRA-CLI.exe` (the command line) over one `_internal\` folder;
4. self-tests both programs in temporary folders — the command line through a
   representative workflow, and the window through its own forms, including the answer
   view, View Sources, formula typesetting, export and restore;
5. checks the package: no database, document, log, backup or data folder, and no path of
   the build machine inside any file or embedded archive;
6. collects the license texts of the bundled components into `build\licenses\`;
7. with `--installer`, compiles `installer\RUDRA.iss` with Inno Setup 6 into
   `dist\installer\RUDRA-Setup-<version>-win64.exe` and writes `SHA256SUMS.txt`.

### Installer test

```powershell
.venv\Scripts\python.exe windows\test_installer.py dist\installer\RUDRA-Setup-0.1.0-win64.exe
```

It installs silently for the current user into a temporary folder, runs the installed
programs with no Python on `PATH` and with `LOCALAPPDATA` pointed into that folder, imports
a PDF, asks a question, runs the window's self-test with the network cut off, installs
again over the existing installation (or a newer installer given with `--upgrade-to`),
uninstalls, and checks that the user's data survived both.

## Releasing

See [RELEASING.md](RELEASING.md): version numbers, the tag, the **Release** workflow that
builds, tests and drafts a release, and the review before publishing. The **CI** workflow
(`.github/workflows/ci.yml`) runs the test suite on every push and pull request.

## Project layout

| Path | What it holds |
|---|---|
| `app/` | The application: storage, extraction, query, reasoning, calculation, provenance, interpretation, actions, the command line (`app/ui/cli`) and the window (`app/ui/gui`) |
| `app/storage/schema/` | The database migrations |
| `app/storage/archive.py` | Knowledge-base backup and restore |
| `app/updates/` | The update notice |
| `app/documents/` | Format detection, the document readers, OCR, and PDF equation reconstruction (`pdfmath.py`) |
| `app/providers/` | Optional AI assistance: provider connections, consent settings, the credential store, grounding checks |
| `app/ui/gui/mathrender.py` | The formula parser, layout and renderer |
| `config/rudra.toml` | The default configuration, with every setting explained |
| `tests/` | Unit and integration tests |
| `windows/` | The PyInstaller specification, entry points, build script and installer test |
| `installer/RUDRA.iss` | The Inno Setup installer |
| `requirements/build.txt` | Exact versions for tests and builds |
