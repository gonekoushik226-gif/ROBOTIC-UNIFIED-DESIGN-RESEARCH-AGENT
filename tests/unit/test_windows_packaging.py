"""The Windows packaging (ADRs 0056, 0057): the frozen project root, the launchers, the build.

RUDRA.exe is the desktop window and RUDRA-CLI.exe the command line. None of these tests
needs PyInstaller or a built program; `windows/build.py` runs both built programs
itself (their self-tests).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from app.ui.cli import main as cli
from tests.conftest import PROJECT_ROOT
from windows import build, rudra_launcher

EXE = r"C:\Apps\RUDRA\RUDRA-CLI.exe"


class _Terminal:
    def isatty(self) -> bool:
        return True


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", EXE)


# ---------------------------------------------------------------- the project root


def test_from_source_the_project_root_is_unchanged():
    assert cli._project_root(argparse.Namespace(project_root=None)) == PROJECT_ROOT.resolve()


def test_packaged_the_project_root_is_the_users_data_folder(frozen, monkeypatch, tmp_path):
    """Installed, data lives in %LOCALAPPDATA%/RUDRA, apart from the program folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert cli._project_root(argparse.Namespace(project_root=None)) == tmp_path / "RUDRA"


def test_a_portable_copy_keeps_its_data_beside_the_executable(tmp_path):
    from app.core.paths import packaged_project_root

    program = tmp_path / "RUDRA"
    program.mkdir()
    exe = program / "RUDRA.exe"
    elsewhere = {"LOCALAPPDATA": str(tmp_path / "local")}
    assert packaged_project_root(exe, elsewhere) == tmp_path / "local" / "RUDRA"
    (program / "portable.txt").write_text("", encoding="utf-8")
    assert packaged_project_root(exe, elsewhere) == program.resolve()
    (program / "portable.txt").unlink()
    (program / "data").mkdir()  # a copy that already holds its data beside it keeps using it
    assert packaged_project_root(exe, elsewhere) == program.resolve()


def test_packaged_an_explicit_project_root_still_wins(frozen, tmp_path):
    assert cli._project_root(argparse.Namespace(project_root=str(tmp_path))) == tmp_path.resolve()


# ---------------------------------------------------------------- when the session starts


def _double_clicked(monkeypatch) -> None:
    """RUDRA.exe alone in an interactive console window. Called inside the test body:
    pytest's output capture reinstalls its own stdin and stdout when the body starts."""
    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr(sys, "stdout", _Terminal())
    monkeypatch.setattr(rudra_launcher, "owns_console", lambda: True)


def test_a_double_click_starts_the_session(frozen, monkeypatch):
    _double_clicked(monkeypatch)
    assert rudra_launcher.is_double_click_start([EXE])


def test_arguments_never_start_the_session(frozen, monkeypatch):
    _double_clicked(monkeypatch)
    assert not rudra_launcher.is_double_click_start([EXE, "version"])


def test_a_terminal_never_starts_the_session(frozen, monkeypatch):
    _double_clicked(monkeypatch)
    monkeypatch.setattr(rudra_launcher, "owns_console", lambda: False)
    assert not rudra_launcher.is_double_click_start([EXE])


def test_redirected_input_never_starts_the_session(frozen, monkeypatch):
    _double_clicked(monkeypatch)
    monkeypatch.setattr(sys, "stdin", None)
    assert not rudra_launcher.is_double_click_start([EXE])


def test_running_from_source_never_starts_the_session(frozen, monkeypatch):
    _double_clicked(monkeypatch)
    monkeypatch.delattr(sys, "frozen")
    assert not rudra_launcher.is_double_click_start([EXE])


def test_with_arguments_the_launcher_is_the_command_line_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(rudra_launcher, "main", lambda argv: calls.append(argv) or 7)
    assert rudra_launcher.launch([EXE, "ask", "What is resistance?"]) == 7
    assert calls == [["ask", "What is resistance?"]]


# ---------------------------------------------------------------- the session


def test_session_lines():
    assert rudra_launcher.session_arguments("  exit ") is None
    assert rudra_launcher.session_arguments("QUIT") is None
    assert rudra_launcher.session_arguments("help") == "--help"
    assert rudra_launcher.session_arguments("   ") == ""
    assert rudra_launcher.session_arguments(' ask "What is resistance?" ') == 'ask "What is resistance?"'


def _session(monkeypatch, lines):
    started, commands = [], []
    monkeypatch.setattr(rudra_launcher, "main", lambda argv: started.append(argv) or 0)

    def read(prompt):
        assert prompt == rudra_launcher.PROMPT
        line = lines.pop(0)
        if isinstance(line, BaseException):
            raise line
        return line

    code = rudra_launcher.run_session(EXE, read=read, run=lambda command, check: commands.append(command))
    return code, started, commands


def test_the_session_starts_rudra_then_runs_each_line_as_its_own_process(monkeypatch, capsys):
    code, started, commands = _session(
        monkeypatch, ["", "version", r'extract "C:\books\a b.pdf"', KeyboardInterrupt(), "help", "exit", "never"])

    assert code == 0 and started == [[]]
    assert commands == [
        f'"{EXE}" version',
        f'"{EXE}" extract "C:\\books\\a b.pdf"',
        f'"{EXE}" --help',
    ]
    assert "'exit' closes the window" in capsys.readouterr().out


def test_end_of_input_closes_the_session(monkeypatch):
    code, _, commands = _session(monkeypatch, ["version", EOFError()])
    assert code == 0 and commands == [f'"{EXE}" version']


# ---------------------------------------------------------------- the build


def test_the_build_refuses_to_delete_project_data_in_the_output_folder(tmp_path):
    (tmp_path / "data").mkdir()
    with pytest.raises(SystemExit) as refused:
        build.refuse_to_overwrite_project_data(tmp_path)
    assert "data/" in str(refused.value) and "nothing was changed" in str(refused.value)


def test_the_build_proceeds_over_a_folder_without_project_data(tmp_path):
    (tmp_path / "_internal").mkdir()
    build.refuse_to_overwrite_project_data(tmp_path)
    build.refuse_to_overwrite_project_data(tmp_path / "absent")


SPEC = PROJECT_ROOT / "windows" / "RUDRA.spec"


def test_the_build_bundles_the_migrations_the_application_reads():
    from app.storage import migrator

    assert migrator.SCHEMA_DIR == PROJECT_ROOT.resolve() / "app" / "storage" / "schema"
    assert sorted(path.name for path in migrator.SCHEMA_DIR.glob("*.sql"))
    assert '"app/storage/schema"' in SPEC.read_text(encoding="utf-8")


def test_the_build_makes_the_window_and_the_command_line_over_one_folder():
    spec = SPEC.read_text(encoding="utf-8")
    assert 'program("rudra_gui.py", "RUDRA", "RUDRA", console=False)' in spec
    assert 'program("rudra_launcher.py", "RUDRA-CLI", "RUDRA command line", console=True)' in spec
    assert 'COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name="RUDRA"' in spec
    assert build.GUI.name == "RUDRA.exe" and build.CLI.name == "RUDRA-CLI.exe" and build.SPEC == SPEC
    for script in ("rudra_gui.py", "rudra_launcher.py"):
        assert (PROJECT_ROOT / "windows" / script).is_file()


def test_the_build_bundles_the_icon_the_marks_and_the_help_documents():
    spec = SPEC.read_text(encoding="utf-8")
    assert 'ICON = str(ROOT / "app" / "ui" / "gui" / "assets" / "rudra.ico")' in spec and "icon=ICON" in spec
    for source, destination in (('"app" / "ui" / "gui" / "assets" / "*"', '"app/ui/gui/assets"'),
                                ('"README.md"', '"."'), ('"docs" / "LIMITATIONS.md"', '"docs"')):
        assert f"{source}), {destination})" in spec, source
    assert (PROJECT_ROOT / "app" / "ui" / "gui" / "assets" / "rudra.ico").is_file()


def test_the_window_takes_no_commands_and_says_where_the_command_line_is():
    from app.ui.gui import launch

    args, unknown = launch.parser().parse_known_args(["--project-root", "X", "--full", "version"])
    assert args.project_root == "X" and args.full and unknown == ["version"]
    assert launch.parser().parse_known_args([])[1] == []


# ---------------------------------------------------------------- the package and the installer


def test_the_package_check_refuses_private_data_and_build_machine_paths(tmp_path):
    (tmp_path / "data" / "database").mkdir(parents=True)
    (tmp_path / "data" / "database" / "knowledge.db").write_bytes(b"SQLite format 3")
    (tmp_path / "notes.log").write_text("log", encoding="utf-8")
    (tmp_path / "readme.txt").write_text(f"built in {PROJECT_ROOT}", encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        build.verify_package(tmp_path)
    message = str(refused.value)
    assert "package contents" in message


def test_the_installer_is_per_user_and_keeps_user_data_apart():
    script = (PROJECT_ROOT / "installer" / "RUDRA.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in script  # no administrator rights
    assert r"DefaultDirName={autopf}\{#AppName}" in script  # per user: %LOCALAPPDATA%\Programs\RUDRA
    assert "OutputBaseFilename=RUDRA-Setup-{#AppVersion}-win64" in script
    assert r'Source: "..\dist\RUDRA\*"' in script and r'Source: "..\LICENSE"' in script
    assert r'Source: "..\THIRD_PARTY_NOTICES.md"' in script and r'Source: "..\build\licenses\*"' in script
    assert 'Tasks: desktopicon' in script and "Flags: unchecked" in script  # the desktop shortcut is optional
    # ... offered as an unchecked "Create a desktop shortcut" box, and it starts the installed RUDRA.exe.
    assert ('Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; '
            'GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked') in script
    assert r'Name: "{autodesktop}\{#AppName}"; Filename: "{app}\RUDRA.exe"; Tasks: desktopicon' in script
    assert r'Name: "{group}\{#AppName}"; Filename: "{app}\RUDRA.exe"' in script  # Start Menu
    # The installer never touches the user's data: nothing is written to or deleted from it.
    assert "[UninstallDelete]" not in script and r"localappdata}\rudra" not in script.lower()
    assert r'Type: filesandordirs; Name: "{app}\_internal"' in script  # only the program's own runtime


def test_the_installer_test_recognizes_an_installed_rudra():
    from windows import test_installer

    script = (PROJECT_ROOT / "installer" / "RUDRA.iss").read_text(encoding="utf-8")
    app_id = script.split("AppId={", 1)[1].split("\n", 1)[0]  # "{{...}" in the script is "{...}"
    assert test_installer.UNINSTALL_KEY.endswith("\\" + app_id + "_is1")


def test_the_release_names_its_files_as_published():
    assert build.INSTALLER_SCRIPT == PROJECT_ROOT / "installer" / "RUDRA.iss"
    assert build.INSTALLER_DIR == PROJECT_ROOT / "dist" / "installer"
