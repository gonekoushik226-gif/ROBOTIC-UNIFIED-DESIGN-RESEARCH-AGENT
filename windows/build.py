"""Build RUDRA as a Windows application: dist/RUDRA/, and optionally its installer.

    python windows\\build.py                          # tests, build, self-tests, package check
    python windows\\build.py --skip-tests             # build, self-tests and package check only
    python windows\\build.py --installer              # ... and dist/installer/RUDRA-Setup-<version>-win64.exe

Needs the build tools once:  python -m pip install -r requirements/build.txt
The installer needs Inno Setup 6 (ISCC.exe on PATH, in the ISCC environment variable,
or in its default install folder).

The result is one folder holding two programs over one shared _internal/ folder:
    dist/RUDRA/RUDRA.exe       the desktop window
    dist/RUDRA/RUDRA-CLI.exe   the command line, as python -m app

Steps
  1. Refuse to build over a dist/RUDRA that holds RUDRA project data (config/, data/
     or logs/): PyInstaller deletes the folder it rebuilds, and that data is the user's.
  2. Run the test suite (skip with --skip-tests).
  3. PyInstaller, from windows/RUDRA.spec (the build's configuration).
  4. Self-test both programs in temporary project folders, then delete them. RUDRA-CLI.exe:
     start, version, db, calculate, extract, lookup, provenance. RUDRA.exe: the window's
     own self-test, which drives its forms through the same workflows. The live project
     and its knowledge.db are never touched.
  5. Check the package: no database, document, log, backup, cache or project folder, and
     no path of the build machine inside any file or embedded archive.
  6. Collect the third-party license texts into build/licenses/ (installed with RUDRA).
  7. With --installer: compile installer/RUDRA.iss with Inno Setup and write
     dist/installer/SHA256SUMS.txt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import marshal
import os
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.version import PHASE, VERSION  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
TARGET = DIST / "RUDRA"
SPEC = ROOT / "windows" / "RUDRA.spec"
GUI = TARGET / "RUDRA.exe"
CLI = TARGET / "RUDRA-CLI.exe"
PROJECT_FOLDERS = ("config", "data", "logs")
LICENSES = BUILD / "licenses"
INSTALLER_SCRIPT = ROOT / "installer" / "RUDRA.iss"
INSTALLER_DIR = DIST / "installer"

#: Nothing of these kinds may ever be part of the application folder.
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".pdf", ".log",
                      ".rudrabackup", ".env", ".key", ".pem", ".secret")
FORBIDDEN_FOLDERS = ("data", "config", "logs", ".venv", "venv", "tests", "build", "__pycache__", ".git",
                     ".pytest_cache", "backups", "documents")

# The one-page document the Phase 12 acceptance extracts.
SELF_TEST_PAGE = (
    "Resistance\n"
    "Resistance is defined as the opposition offered by a material \nto the flow of current.\n"
    "Rtotal = R1 + R2\n"
    "Ohm's law gives the current through the series circuit.\n"
    "I = V / Rtotal"
)


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def run(command: list[object], **kwargs) -> None:
    print(">", subprocess.list2cmdline([str(part) for part in command]), flush=True)
    subprocess.run([str(part) for part in command], check=True, **kwargs)


def refuse_to_overwrite_project_data(target: Path) -> None:
    """Stop when `target` holds a RUDRA project: rebuilding would delete it."""
    held = [name for name in PROJECT_FOLDERS if (target / name).exists()]
    if held:
        sys.exit(
            f"{target} holds RUDRA project data ({', '.join(held)}/), which a rebuild would "
            "delete. Move that folder somewhere else first; nothing was changed."
        )


def pyinstaller() -> None:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST,
         "--workpath", BUILD / "pyinstaller", SPEC], cwd=ROOT)


def check(label: str, ok: bool, detail: object) -> None:
    print(f"  {label:<20} {'ok' if ok else 'FAILED'}  {detail}")
    if not ok:
        sys.exit(f"Self-test FAILED at {label}.")


def self_test_pdf(folder: Path) -> Path:
    from tests.unit.pdf_fixtures import make_pdf

    pdf = folder / "resistance.pdf"
    pdf.write_bytes(make_pdf([SELF_TEST_PAGE]))
    return pdf


def self_test_cli(exe: Path) -> None:
    """A representative workflow with RUDRA-CLI.exe in a scratch project."""
    with tempfile.TemporaryDirectory(prefix="rudra-selftest-") as scratch:
        root = Path(scratch) / "project"

        def rudra(*arguments: str) -> str:
            # Redirected, the program writes the ANSI code page (the packaged interpreter
            # ignores PYTHONIOENCODING), and prints "?" for what that page cannot encode.
            done = subprocess.run(
                [str(exe), *arguments, "--project-root", str(root)],
                capture_output=True, text=True, encoding=locale.getencoding(), errors="replace",
                timeout=300,
            )
            if done.returncode != 0:
                sys.exit(f"Self-test FAILED: {exe.name} {' '.join(arguments)} exited "
                         f"{done.returncode}\n{done.stdout}\n{done.stderr}")
            return done.stdout

        started = json.loads(rudra("start", "--json"))
        check("start", started["started"] and Path(started["project_root"]) == root.resolve(),
              f"{len(started['directories_created'])} directories created in the scratch project")
        version = json.loads(rudra("version", "--json"))
        check("version", version["phase"] == PHASE and version["version"] == VERSION,
              f"{version['version']}, {version['phase']}, Python {version['python']}")
        database = json.loads(rudra("db", "--json"))
        check("db", database["created"] and database["integrity"] == "ok",
              f"created, schema version {database['schema_version']}, integrity {database['integrity']}")
        calculation = json.loads(rudra(
            "calculate", "I", "--formula", "I = V / Rtotal", "--formula", "Rtotal = R1 + R2",
            "--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V", "--json"))
        result = calculation["result"]
        check("calculate", calculation["status"] == "CALCULATED" and result["exact"] == "1/3",
              f"I {result['relation']} {result['displayed']} {result['unit']} (exact {result['exact']})")
        extraction = rudra("extract", str(self_test_pdf(Path(scratch))))  # extract has no --json mode
        check("extract", "\nStatus     : COMPLETED" in extraction, "one generated page, run COMPLETED (pypdf)")
        lookup = json.loads(rudra("lookup", "--name", "Resistance", "--json"))
        check("lookup", lookup["match_count"] == 1, lookup["matches"][0]["concept"]["id"])
        definition = lookup["matches"][0]["definitions"][0]["knowledge"]["id"]
        provenance = json.loads(rudra("provenance", definition, "--json"))
        check("provenance", provenance["status"] == "AVAILABLE" and provenance["verification"] == "VERIFIED",
              f"{definition} {provenance['status']}, {provenance['verification']}")


def self_test_gui(exe: Path) -> None:
    """RUDRA.exe's own self-test: the real window, driven through its forms, in a scratch project."""
    with tempfile.TemporaryDirectory(prefix="rudra-selftest-gui-") as scratch:
        report = Path(scratch) / "report.json"
        done = subprocess.run(
            [str(exe), "--self-test", str(report), "--project-root", str(Path(scratch) / "project"),
             "--self-test-pdf", str(self_test_pdf(Path(scratch)))],
            timeout=300,
        )
        if not report.exists():
            sys.exit(f"Self-test FAILED: {exe.name} wrote no report (exit code {done.returncode}).")
        outcome = json.loads(report.read_text(encoding="utf-8"))
        for name, entry in outcome["checks"].items():
            check(name, entry["ok"], entry["detail"][:110])
        check("window program", done.returncode == 0 and outcome["passed"] and outcome["frozen"],
              f"exit {done.returncode}, packaged {outcome['frozen']}")


def _private_markers() -> list[bytes]:
    """Paths of this build machine that must not appear in anything distributed."""
    markers = set()
    for path in (ROOT, Path.home()):
        text = str(path)
        for form in (text, text.replace("\\", "/"), text.replace("\\", "\\\\")):
            markers.add(form.encode("utf-8"))
            markers.add(form.encode("utf-16-le"))
    return sorted(markers)


def _contains_marker(data: bytes, markers: list[bytes]) -> bytes | None:
    lowered = data.lower()
    return next((marker for marker in markers if marker.lower() in lowered), None)


def verify_package(target: Path = TARGET) -> None:
    """The application folder holds the application and nothing of the build machine's."""
    problems: list[str] = []
    markers = _private_markers()
    files = [path for path in target.rglob("*") if path.is_file()]
    for path in target.rglob("*"):
        relative = path.relative_to(target)
        if path.is_dir() and path.name.lower() in FORBIDDEN_FOLDERS:
            problems.append(f"folder {relative}")
        if path.is_file() and path.name.lower().endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"file {relative}")
    for path in files:
        found = _contains_marker(path.read_bytes(), markers)
        if found is not None:
            problems.append(f"{path.relative_to(target)} contains a build-machine path")
    # Python modules inside the executables are compressed: look inside them too.
    from PyInstaller.archive.readers import CArchiveReader

    for exe in (target / "RUDRA.exe", target / "RUDRA-CLI.exe"):
        if not exe.is_file():
            problems.append(f"{exe.name} is missing")
            continue
        archive = CArchiveReader(str(exe))
        for name, entry in archive.toc.items():
            if entry[-1] == "z":
                embedded = archive.open_embedded_archive(name)
                for module in embedded.toc:
                    code = embedded.extract(module)
                    raw = code if isinstance(code, (bytes, bytearray)) else marshal.dumps(code)
                    if raw and _contains_marker(bytes(raw), markers):
                        problems.append(f"{exe.name}:{module} contains a build-machine path")
            else:
                data = archive.extract(name)
                if isinstance(data, (bytes, bytearray)) and _contains_marker(bytes(data), markers):
                    problems.append(f"{exe.name}:{name} contains a build-machine path")
    check("package contents", not problems, f"{len(files)} files, nothing private" if not problems
          else "; ".join(problems[:10]))


def collect_licenses() -> Path:
    """The license texts of what the application bundles, from the exact packages bundled."""
    if LICENSES.exists():
        shutil.rmtree(LICENSES)
    LICENSES.mkdir(parents=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    sources = {"PYTHON-LICENSE.txt": python_license}
    for distribution, name in (("pypdf", "PYPDF-LICENSE.txt"), ("pyinstaller", "PYINSTALLER-COPYING.txt")):
        files = metadata.distribution(distribution).files or []
        found = next((f for f in files if "licenses" in str(f).lower() and not str(f).endswith("/")), None)
        if found is None:
            sys.exit(f"The license file of {distribution} was not found; nothing was built.")
        sources[name] = Path(found.locate())
    for name, source in sources.items():
        if not source.is_file():
            sys.exit(f"The license file {source} does not exist; nothing was built.")
        shutil.copyfile(source, LICENSES / name)
    check("licenses", True, ", ".join(sorted(sources)))
    return LICENSES


def find_iscc() -> Path:
    """Inno Setup's command-line compiler."""
    candidates = [os.environ.get("ISCC", ""), shutil.which("iscc") or ""]
    for variable, folder in (("LOCALAPPDATA", "Programs"), ("ProgramFiles(x86)", ""), ("ProgramFiles", "")):
        if os.environ.get(variable):
            candidates.append(str(Path(os.environ[variable]) / folder / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    sys.exit("Inno Setup 6 was not found. Install it, or set ISCC to the path of ISCC.exe.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def build_installer(version: str = VERSION, output: Path = INSTALLER_DIR) -> Path:
    """Compile the installer and write SHA256SUMS.txt beside it."""
    output = Path(output).resolve()  # Inno Setup reads a relative OutputDir against the .iss folder
    output.mkdir(parents=True, exist_ok=True)
    run([find_iscc(), "/Qp", f"/DAppVersion={version}", f"/DOutputDir={output}", INSTALLER_SCRIPT], cwd=ROOT)
    setup = output / f"RUDRA-Setup-{version}-win64.exe"
    if not setup.is_file():
        sys.exit(f"Inno Setup did not produce {setup}.")
    sums = output / "SHA256SUMS.txt"
    sums.write_text(f"{sha256(setup)}  {setup.name}\n", encoding="ascii", newline="\n")
    check("installer", True, f"{setup.name}, {setup.stat().st_size / 1024 ** 2:.1f} MB; {sums.name} written")
    return setup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-tests", action="store_true", help="do not run the test suite first")
    parser.add_argument("--installer", action="store_true", help="also build the Inno Setup installer")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):  # reports hold symbols such as − and Ω: write them as UTF-8
        stream.reconfigure(encoding="utf-8")
    started = time.monotonic()

    refuse_to_overwrite_project_data(TARGET)

    if not args.skip_tests:
        step("Running the test suite")
        run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], cwd=ROOT,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    step("Building dist/RUDRA (PyInstaller, windows/RUDRA.spec)")
    pyinstaller()

    step("Self-test: RUDRA-CLI.exe, the command line")
    self_test_cli(CLI)
    step("Self-test: RUDRA.exe, the desktop window")
    self_test_gui(GUI)

    step("Checking the package")
    verify_package(TARGET)
    collect_licenses()

    if args.installer:
        step("Building the installer (Inno Setup, installer/RUDRA.iss)")
        build_installer()

    size = sum(path.stat().st_size for path in TARGET.rglob("*") if path.is_file())
    step("Done")
    print(f"  {GUI}")
    print(f"  {CLI}")
    print(f"  folder {TARGET}: {size / 1024 ** 2:.1f} MB")
    print(f"  {(time.monotonic() - started) / 60:.1f} minutes")


if __name__ == "__main__":
    main()
