"""Install, use, upgrade and uninstall RUDRA from its installer, as a user would, in a scratch folder.

    python windows\\test_installer.py dist\\installer\\RUDRA-Setup-0.1.0-win64.exe
    python windows\\test_installer.py SETUP.exe --upgrade-to NEWER-SETUP.exe

Everything happens under one temporary folder, which stands in for a clean user profile:
the program is installed into ``<scratch>\\Programs\\RUDRA`` (per user, no administrator
rights), and the installed program runs with ``LOCALAPPDATA`` pointed at
``<scratch>\\LocalAppData``, so its data folder is created exactly where an installed
RUDRA creates it - inside the scratch folder. No Start Menu or desktop shortcut is made
(/NOICONS, no tasks). The uninstall entry the installer registers for the current user is
removed again by the uninstall step.

The installed programs run with a PATH that holds only Windows' own folders and with no
PYTHON* variables, so nothing can come from a Python installation. Checks:

  1. the silent per-user install succeeds and installs the programs, LICENSE and notices,
     and nothing private (no database, document, log or data folder);
  2. RUDRA-CLI.exe starts and creates its data folder in %LOCALAPPDATA%\\RUDRA;
  3. a PDF is imported and a question answered;
  4. RUDRA.exe's own self-test passes, with the network cut off (sources hidden until
     View Sources, formulas typeset, export and restore, the update check failing quietly);
  5. installing again (or the newer installer given with --upgrade-to) keeps the user data;
  6. uninstalling removes the program and keeps the user data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAGE = (
    "Resistance\n"
    "Resistance is defined as the opposition offered by a material \nto the flow of current.\n"
    "Rtotal = R1 + R2\n"
    "Ohm's law gives the current through the series circuit.\n"
    "I = V / Rtotal"
)
FORBIDDEN = (".db", ".db-wal", ".db-shm", ".pdf", ".log", ".rudrabackup")


def check(label: str, ok: bool, detail: object = "") -> None:
    print(f"  {label:<28} {'ok' if ok else 'FAILED'}  {detail}", flush=True)
    if not ok:
        sys.exit(f"Installer test FAILED at {label}.")


def clean_environment(local_app_data: Path, *, offline: bool = False) -> dict[str, str]:
    """A user's environment without any Python: Windows' folders only on PATH."""
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "RUDRA_", "VIRTUAL_ENV", "CONDA", "UV_"))}
    env["PATH"] = os.pathsep.join([str(Path(windows) / "System32"), windows,
                                   str(Path(windows) / "System32" / "Wbem")])
    env["LOCALAPPDATA"] = str(local_app_data)
    if offline:
        # An unreachable proxy for every request: the process has no working network.
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[name] = "http://127.0.0.1:9"
        env["NO_PROXY"] = env["no_proxy"] = ""
        env["RUDRA_SELFTEST_NO_NETWORK"] = "1"
    return env


def run_setup(setup: Path, program: Path, log: Path) -> None:
    command = [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/NOICONS",
               "/TASKS=", f"/DIR={program}", f"/LOG={log}"]
    done = subprocess.run(command, timeout=600)
    check(f"install {setup.name}", done.returncode == 0 and (program / "RUDRA.exe").is_file(),
          f"exit {done.returncode}, into {program}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("setup", type=Path)
    parser.add_argument("--upgrade-to", type=Path, default=None)
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):  # reports hold symbols such as − and Ω: write them as UTF-8
        stream.reconfigure(encoding="utf-8")
    from tests.unit.pdf_fixtures import make_pdf

    with tempfile.TemporaryDirectory(prefix="rudra-install-") as scratch:
        base = Path(scratch)
        program = base / "Programs" / "RUDRA"
        local = base / "LocalAppData"
        local.mkdir()
        data_home = local / "RUDRA"
        env = clean_environment(local)
        cli = program / "RUDRA-CLI.exe"

        def rudra(*arguments: str) -> tuple[int, str]:
            done = subprocess.run([str(cli), *arguments], capture_output=True, text=True, env=env, cwd=str(base),
                                  encoding=locale.getencoding(), errors="replace", timeout=300)
            return done.returncode, done.stdout + done.stderr

        print("=== 1. Install", flush=True)
        run_setup(args.setup, program, base / "install.log")
        installed = {p.relative_to(program).as_posix() for p in program.rglob("*") if p.is_file()}
        for required in ("RUDRA.exe", "RUDRA-CLI.exe", "LICENSE.txt", "THIRD_PARTY_NOTICES.md",
                         "licenses/PYTHON-LICENSE.txt", "licenses/PYPDF-LICENSE.txt", "unins000.exe"):
            check(f"installed {required}", required in installed)
        private = sorted(name for name in installed if name.lower().endswith(FORBIDDEN))
        check("nothing private installed", not private, private[:5] or f"{len(installed)} files")
        check("no data in program folder", not any((program / name).exists() for name in ("data", "config", "logs")))

        print("=== 2. First start, without Python", flush=True)
        code, out = rudra("start", "--json")
        started = json.loads(out[out.index("{"):out.rindex("}") + 1]) if code == 0 else {}
        check("start", code == 0 and started.get("started"), f"exit {code}")
        check("user data folder", Path(started["project_root"]) == data_home and (data_home / "data").is_dir(),
              started.get("project_root"))
        check("program folder untouched", not (program / "data").exists() and not (program / "logs").exists())

        print("=== 3. Import a PDF and ask", flush=True)
        pdf = base / "resistance.pdf"
        pdf.write_bytes(make_pdf([PAGE]))
        code, out = rudra("extract", str(pdf))
        check("import", code == 0 and "Status     : COMPLETED" in out, f"exit {code}")
        database = data_home / "data" / "database" / "knowledge.db"
        check("database created", database.is_file(), database)
        code, out = rudra("ask", "What is resistance?", "--json", "--dry-run")
        answer = json.loads(out[out.index("{"):out.rindex("}") + 1])
        check("question answered", code == 0 and answer["parts"][0]["status"] == "ANSWERED",
              answer["parts"][0]["answer"][:70])

        print("=== 4. The window's self-test, offline", flush=True)
        report = base / "gui-report.json"
        done = subprocess.run([str(program / "RUDRA.exe"), "--self-test", str(report), "--project-root",
                               str(base / "gui-project"), "--self-test-pdf", str(pdf)],
                              env=clean_environment(local, offline=True), timeout=600)
        outcome = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {"checks": {}}
        for name, entry in outcome["checks"].items():
            check(f"window: {name}", entry["ok"], entry["detail"][:90])
        check("window self-test", done.returncode == 0 and outcome.get("passed") and outcome.get("frozen"),
              f"exit {done.returncode}")

        print("=== 5. Upgrade keeps the user's data", flush=True)
        before = digest(database)
        run_setup(args.upgrade_to or args.setup, program, base / "upgrade.log")
        check("data kept after upgrade", database.is_file() and digest(database) == before)
        code, out = rudra("ask", "What is resistance?", "--json", "--dry-run")
        check("still answers after upgrade", code == 0 and '"ANSWERED"' in out)

        print("=== 6. Uninstall keeps the user's data", flush=True)
        uninstaller = program / "unins000.exe"
        subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300)
        deadline = time.monotonic() + 180
        while (program / "RUDRA.exe").exists() and time.monotonic() < deadline:
            time.sleep(1)  # the uninstaller finishes in a copy of itself
        check("program removed", not (program / "RUDRA.exe").exists() and not (program / "_internal").exists())
        check("data kept after uninstall", database.is_file() and digest(database) == before, data_home)
        print("\nInstaller test passed.")


if __name__ == "__main__":
    main()
