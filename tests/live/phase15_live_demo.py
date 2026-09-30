"""Phase 15 live demonstration (ADR 0047 P15-11, P15-14). NOT collected by pytest.

Run explicitly, once, on the machine RUDRA controls:

    .venv\\Scripts\\python.exe tests\\live\\phase15_live_demo.py SCRATCH_FOLDER

It acts on the live desktop, which the regular test suite never does (section 168):

1. section 211 - `do "Open Calculator."`, every stage shown;
2. section 209 live - `act OPEN_APPLICATION` for Notepad and Calculator with `--execute`;
3. section 210's low-risk operations - inspect windows, create a temporary test file in
   SCRATCH_FOLDER and read it back, open it, take a screenshot into SCRATCH_FOLDER;
4. closing exactly the windows the demonstration opened, by handle, with confirmation.

Every command is a separate `python -m app` process against a scratch project root inside
SCRATCH_FOLDER, so the live `knowledge.db` is never involved. The screenshot is deleted
after its size is recorded: it holds whatever was on the screen.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def rudra(project: Path, *args: str) -> tuple[int, dict | str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-m", "app", *args, "--project-root", str(project)],
                          capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT), timeout=300)
    out = done.stdout
    try:
        return done.returncode, json.loads(out)
    except json.JSONDecodeError:
        return done.returncode, out


def opened_handle(report: dict) -> int | None:
    """The handle of a window a step reports as NEW (never one that was already open)."""
    for step in report.get("steps", []):
        observed = step.get("observed", "")
        if step.get("status") == "VERIFIED" and observed.startswith("new window"):
            return int(observed.rsplit("window ", 1)[1].rstrip(")"))
    return None


def main() -> None:
    scratch = Path(sys.argv[1]).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    project = scratch / "project"
    opened: list[int] = []
    evidence: dict = {}

    # 1. Section 211.
    code, report = rudra(project, "do", "Open Calculator.", "--json")
    evidence["211"] = {"exit": code, "outcome": report["outcome"], "message": report["message"],
                       "stages": [[s["name"], s["outcome"]] for s in report["stages"]]}
    handle = opened_handle(report["execution"]) if report.get("execution") else None
    if handle:
        opened.append(handle)

    # 2. Section 209, live.
    for application in ("Notepad", "Calculator"):
        code, report = rudra(project, "act", "OPEN_APPLICATION", "--param", f"application={application}",
                             "--execute", "--json")
        step = report["steps"][0]
        evidence[f"209 {application}"] = {"exit": code, "status": step["status"], "observed": step["observed"],
                                          "attempts": step["attempts"], "dry_run": report["dry_run"]}
        handle = opened_handle(report)
        if handle:
            opened.append(handle)

    # 3. Section 210's low-risk operations.
    test_file = scratch / "rudra-phase15-test.txt"
    code, report = rudra(project, "act", "CREATE_FILE", "--param", f"path={test_file}",
                         "--param", "content=RUDRA Phase 15 test file", "--execute", "--json")
    evidence["210 create temporary test file"] = {"exit": code, "status": report["steps"][0]["status"],
                                                  "observed": report["steps"][0]["observed"]}
    evidence["210 read file"] = {"content": test_file.read_text(encoding="utf-8")}
    code, report = rudra(project, "act", "OPEN_FILE", "--param", f"path={test_file}", "--execute", "--json")
    evidence["210 open file"] = {"exit": code, "status": report["steps"][0]["status"],
                                 "observed": report["steps"][0]["observed"]}
    handle = opened_handle(report)
    if handle:
        opened.append(handle)
    shot = scratch / "rudra-phase15-screenshot.png"
    code, report = rudra(project, "act", "TAKE_SCREENSHOT", "--param", f"path={shot}", "--execute", "--json")
    evidence["210 screenshot"] = {"exit": code, "status": report["steps"][0]["status"],
                                  "observed": report["steps"][0]["observed"]}
    if shot.exists():
        shot.unlink()  # it holds whatever was on the screen

    sys.path.insert(0, str(ROOT))
    from app.applications.registry import APPLICATIONS
    from app.computer.windows import WindowsPlatform

    windows = WindowsPlatform().windows()
    evidence["210 inspect windows"] = {"visible top-level windows": len(windows),
                                       "opened by this demonstration and still open":
                                           [[w.handle, w.title, w.image] for w in windows if w.handle in opened]}

    # 4. Close exactly the windows opened here (MEDIUM risk: confirmed).
    closes = []
    for handle in opened:
        window = next((w for w in WindowsPlatform().windows() if w.handle == handle), None)
        if window is None:
            continue
        # The registry names the application the window belongs to: an opened file shows in
        # whichever application the machine's file association chooses (here Notepad++).
        application = next((a.name for a in APPLICATIONS if a.detection.matches(window.image, window.title)), None)
        if application is None:
            closes.append([handle, window.title, "not closed: no registered application owns this window"])
            continue
        code, report = rudra(project, "act", "CLOSE_APPLICATION", "--param", f"application={application}",
                             "--param", f"window={handle}", "--execute", "--confirm", "--json")
        closes.append([handle, window.title, code, report["steps"][0]["status"] if isinstance(report, dict) else report])
    evidence["close opened windows"] = closes
    print(json.dumps(evidence, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
