"""Install, use, upgrade and uninstall RUDRA from its installer, as a user would, in a scratch folder.

    python windows\\test_installer.py dist\\installer\\RUDRA-Setup-1.0.0-win64.exe
    python windows\\test_installer.py SETUP.exe --upgrade-to NEWER-SETUP.exe

Everything happens under one temporary folder, which stands in for a clean user profile:
the program is installed into ``<scratch>\\Programs\\RUDRA`` (per user, no administrator
rights), and the installed program runs with ``LOCALAPPDATA`` pointed at
``<scratch>\\LocalAppData``, so its data folder is created exactly where an installed
RUDRA creates it - inside the scratch folder. Only the uninstall entry and the shortcuts
cannot go there: every install registers RUDRA for the current user and makes its Start
Menu shortcuts in the user's real Start Menu (the installer offers no way to leave them
out, so /NOICONS changes nothing), and the desktop shortcut, when chosen, goes on the
user's real desktop. Checks 1-6 choose no desktop shortcut (/TASKS=); check 7 chooses it.
Each uninstall step removes them again, and the test refuses to start where RUDRA is
already installed for the current user, whose entry and shortcuts it would replace and
then remove.

The installed programs run with a PATH that holds only Windows' own folders and with no
PYTHON* variables, so nothing can come from a Python installation. Checks:

  1. the silent per-user install succeeds and installs the programs, LICENSE and notices,
     and nothing private (no database, document, log or data folder);
  2. RUDRA-CLI.exe starts and creates its data folder in %LOCALAPPDATA%\\RUDRA;
  3. a PDF is imported, the knowledge inventory lists what was stored, a question is
     answered, RUDRA chooses the stored equations for a two-step calculation, and --help
     prints on a console that cannot show every character;
  4. RUDRA.exe's own self-test passes, with the network cut off (sources hidden until
     View Sources, formulas typeset, export and restore, the update check failing quietly);
  5. installing again (or the newer installer given with --upgrade-to) keeps the user data;
  6. uninstalling removes the program and keeps the user data;
  7. installed with the wizard's choices left as they are, RUDRA gets its Start Menu
     shortcuts and no desktop shortcut; with "Create a desktop shortcut" chosen
     (/TASKS=desktopicon), the desktop shortcut starts the installed RUDRA.exe; uninstalling
     removes them all.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import locale
import os
import subprocess
import sys
import tempfile
import time
import winreg
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
NO_TASKS = ("/NOICONS", "/TASKS=")
#: The current user's desktop and Start Menu programs folders: {userdesktop} and {userprograms}.
CSIDL_DESKTOPDIRECTORY, CSIDL_PROGRAMS = 0x10, 0x02
#: The uninstall entry of a per-user installation: the AppId of installer\RUDRA.iss, plus _is1.
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{05B1382D-1FC2-4ADB-888C-F03C39A3DF08}_is1"


#: The program folder of the installation under test, uninstalled when a check fails, so a
#: failed run never leaves a registered installation behind on the machine.
_INSTALLED: list[Path] = []


def uninstall(program: Path) -> None:
    """Run the uninstaller registered for this installation (Inno may number it unins001)."""
    uninstaller = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as entry:
            command, _ = winreg.QueryValueEx(entry, "UninstallString")
        command = str(command).strip()
        path = command[1:command.find('"', 1)] if command.startswith('"') else command.split(" ", 1)[0]
        candidate = Path(path)
        if candidate.is_file() and candidate.resolve().parent == program.resolve():
            uninstaller = candidate
    except (OSError, ValueError):
        pass
    if uninstaller is None:
        candidates = sorted(program.glob("unins*.exe"), key=lambda path: path.stat().st_mtime, reverse=True)
        uninstaller = next((path for path in candidates if path.is_file()), None)
    if uninstaller is None:
        return
    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300)
    deadline = time.monotonic() + 180
    while (program / "RUDRA.exe").exists() and time.monotonic() < deadline:
        time.sleep(1)  # the uninstaller finishes in a copy of itself


def check(label: str, ok: bool, detail: object = "") -> None:
    print(f"  {label:<28} {'ok' if ok else 'FAILED'}  {detail}", flush=True)
    if not ok:
        for program in _INSTALLED:
            uninstall(program)
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


def run_setup(setup: Path, program: Path, log: Path, options: tuple[str, ...] = NO_TASKS) -> None:
    command = [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", *options,
               f"/DIR={program}", f"/LOG={log}"]
    done = subprocess.run(command, timeout=600)
    check(f"install {setup.name}", done.returncode == 0 and (program / "RUDRA.exe").is_file(),
          f"exit {done.returncode}, into {program}")


def shell_folder(csidl: int) -> Path:
    """A folder of the current user's, found the way Inno Setup finds it."""
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) != 0:
        sys.exit(f"Installer test FAILED: shell folder {csidl:#x} not found.")
    return Path(buffer.value)


def shortcut_target(link: Path) -> Path | None:
    """The program a .lnk shortcut starts, allowing Explorer a moment to finish creating it."""
    quoted = str(link).replace("'", "''")
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              f"(New-Object -ComObject WScript.Shell).CreateShortcut('{quoted}').TargetPath")
    deadline = time.monotonic() + 15
    target = None
    while time.monotonic() < deadline:
        if link.is_file():
            done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                  capture_output=True, text=True, encoding="utf-8", timeout=120)
            target = Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None
            if target is not None and target.is_file():
                return target
        time.sleep(0.25)
    return target


def installed_rudra(desktop: Path, group: Path) -> list[str]:
    """What the current user has of an installed RUDRA: its uninstall entry and shortcuts."""
    found = [str(path) for path in (desktop, group) if path.exists()]
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY))
        found.append(rf"HKCU\{UNINSTALL_KEY}")
    except FileNotFoundError:
        pass
    return found


def left_behind(desktop: Path, group: Path) -> list[str]:
    """What an uninstall left of RUDRA's entry and shortcuts, given time to finish."""
    deadline = time.monotonic() + 60
    while (found := installed_rudra(desktop, group)) and time.monotonic() < deadline:
        time.sleep(1)  # the uninstaller finishes in a copy of itself
    return found


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
        # The long form of the folder: TEMP may name it in 8.3 form (C:\Users\RUNNER~1\...),
        # while RUDRA reports the folders it uses resolved, in full.
        base = Path(scratch).resolve()
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

        desktop = shell_folder(CSIDL_DESKTOPDIRECTORY) / "RUDRA.lnk"
        group = shell_folder(CSIDL_PROGRAMS) / "RUDRA"

        print("=== 1. Install", flush=True)
        found = installed_rudra(desktop, group)
        check("RUDRA not installed yet", not found, "; ".join(found) or "no uninstall entry, no shortcuts")
        _INSTALLED.append(program)
        run_setup(args.setup, program, base / "install.log")
        installed = {p.relative_to(program).as_posix() for p in program.rglob("*") if p.is_file()}
        for required in ("RUDRA.exe", "RUDRA-CLI.exe", "LICENSE.txt", "THIRD_PARTY_NOTICES.md",
                         "licenses/PYTHON-LICENSE.txt", "licenses/PYPDF-LICENSE.txt", "licenses/WHISPER-CPP-LICENSE.txt",
                         "licenses/OPENAI-WHISPER-LICENSE.txt", "licenses/SILERO-VAD-LICENSE.txt",
                         "speech/manifest.json", "speech/whisper-cli.exe", "speech/whisper.dll",
                         "speech/models/ggml-small.en-q5_1.bin", "speech/models/ggml-silero-v5.1.2.bin", "unins000.exe"):
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

        code, out = rudra("inventory", "--json")
        listing = json.loads(out[out.index("{"):out.rindex("}") + 1]) if code == 0 else {}
        totals = listing.get("totals", {})
        check("inventory", code == 0 and totals.get("documents") == 1 and totals.get("counts", {}).get("EQUATION") == 2
              and listing.get("calculation", {}).get("usable") == 2, totals.get("counts"))
        code, out = rudra("ask", "Calculate I when V = 12 V, R1 = 10 ohms and R2 = 20 ohms", "--json", "--dry-run")
        part = json.loads(out[out.index("{"):out.rindex("}") + 1])["parts"][0]
        steps = [line for line in part["calculation"] if line.startswith("Step")]
        check("two-step calculation", code == 0 and part["status"] == "ANSWERED" and part["answer"] == "I = 0.4 A"
              and len(steps) == 2, part["answer"])
        code, out = rudra("ask", "Calculate P when V = 10 V", "--json", "--dry-run")
        part = json.loads(out[out.index("{"):out.rindex("}") + 1])["parts"][0]
        check("insufficient values said so", part["status"] == "CANNOT_DETERMINE" and bool(part["missing"]),
              part["answer"][:70])
        code, out = rudra("--help")
        check("help prints", code == 0 and "usage: RUDRA-CLI.exe" in out and "python -m app" not in out, f"exit {code}")

        # Speech: the installed recogniser, offline, reads a sentence spoken into a WAV file.
        spoken = base / "request.wav"
        code, out = rudra("voice", "--say", "Open Calculator.", "--audio", str(spoken))
        check("speech output", code == 0 and spoken.is_file(), f"exit {code}")
        done = subprocess.run([str(cli), "voice", "--audio", str(spoken), "--dry-run", "--json", "--project-root",
                               str(data_home)], capture_output=True, text=True, env=clean_environment(local, offline=True),
                              cwd=str(base), encoding="utf-8", errors="replace", timeout=300)
        heard = json.loads(done.stdout[done.stdout.index("{"):done.stdout.rindex("}") + 1])["speech"]
        check("speech recognition (offline)", heard["text"].casefold().strip(" .") == "open calculator"
              and "Whisper" in heard["recognizer"], f"{heard['text']!r}, confidence {heard['confidence']}")

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
        uninstall(program)
        _INSTALLED.clear()
        check("program removed", not (program / "RUDRA.exe").exists() and not (program / "_internal").exists())
        check("data kept after uninstall", database.is_file() and digest(database) == before, data_home)

        print("=== 7. Shortcuts: the Start Menu always, the desktop when chosen", flush=True)
        menu = {"RUDRA.lnk": program / "RUDRA.exe", "RUDRA Command Line.lnk": program / "RUDRA-CLI.exe"}
        found = left_behind(desktop, group)
        check("nothing left by uninstall", not found, "; ".join(found) or "no uninstall entry, no shortcuts")
        _INSTALLED.append(program)
        run_setup(args.setup, program, base / "shortcuts.log", options=())  # every choice left as it is
        for name, target in menu.items():
            link = group / name
            check(f"Start Menu {name}", link.is_file() and shortcut_target(link) == target, link)
        uninstall_link = group / "Uninstall RUDRA.lnk"
        uninstall_target = shortcut_target(uninstall_link)
        uninstallers = {path.resolve() for path in program.glob("unins*.exe") if path.is_file()}
        check("Start Menu Uninstall RUDRA.lnk", uninstall_target is not None
              and uninstall_target.resolve() in uninstallers,
              f"{uninstall_link} -> {uninstall_target}; available uninstallers: {sorted(map(str, uninstallers))}")
        check("desktop shortcut not chosen", not desktop.exists(), f"no {desktop}")
        run_setup(args.setup, program, base / "desktop.log", options=("/TASKS=desktopicon",))
        target = shortcut_target(desktop) if desktop.is_file() else None
        check("desktop shortcut chosen", target == program / "RUDRA.exe", f"{desktop} -> {target}")
        uninstall(program)
        _INSTALLED.clear()
        found = left_behind(desktop, group)
        check("shortcuts removed", not found, "; ".join(found) or "no uninstall entry, no shortcuts")
        print("\nInstaller test passed.")


if __name__ == "__main__":
    main()
