"""What the desktop window runs: the command line's own commands, in this process (ADR 0057).

The window adds no capability and no logic of its own (master specification sections
138 and 140). Every action it offers is a command line - the arguments `python -m app`
takes - run through `app.ui.cli.main.main` with its output captured, so the answer, its
provenance, its checks and its exit code are exactly the command line's.

This module holds everything about that which needs no window: building the command
lines from form fields, running one, describing the result, finding the documents the
Help page shows, and deciding which command lines to put to the user first because they
write the knowledge database, act on this computer or reach the Internet.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import io
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import load_config
from app.core.paths import PathLayout
from app.storage import DatabaseRole, database_path
from app.ui.cli import main as cli

STATUS_COMMANDS = ("start", "env", "paths", "config", "version")
LOOKUP_MODES = ("name", "identifier", "keyword")
HELP_ARGUMENTS = ("--help",)
KNOWLEDGE_WRITERS = frozenset({"db", "extract", "classify", "edition", "merge"})


class FormError(ValueError):
    """A form the window cannot turn into a command line. Nothing was run."""


@dataclass(frozen=True)
class CommandResult:
    """One command line, run: what it printed, and how it ended."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    seconds: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def meaning(self) -> str:
        return exit_meaning(self.exit_code)


@dataclass(frozen=True)
class Approval:
    """A command line to put to the user before it runs, and why."""

    title: str
    message: str


# ------------------------------------------------------------------ running


def run(argv: list[str] | tuple[str, ...], project_root: Path | None) -> CommandResult:
    """Run one command line through the command line's own entry point, capturing its output.

    The project root is passed first, so a `--project-root` typed later still wins, as it
    would on the command line.
    """
    full = ["--project-root", str(project_root), *argv] if project_root is not None else list(argv)
    out, err = io.StringIO(), io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(full)
        except SystemExit as exc:  # argparse: --help, or arguments it rejects (it printed why)
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    return CommandResult(tuple(argv), int(code), out.getvalue(), err.getvalue(), time.perf_counter() - started)


def exit_meaning(code: int) -> str:
    """The command line's own name for an exit code, in words."""
    try:
        return cli.ExitCode(code).name.replace("_", " ").lower()
    except ValueError:
        return f"exit code {code}"


def is_answer_line(line: str) -> bool:
    """The line a command's answer is on (`ask`'s ANSWER, `calculate`'s and others' Answer): shown emphasized."""
    head, colon, _ = line.partition(":")
    return bool(colon) and head.strip() in ("ANSWER", "Answer")


def startup_summary(result: CommandResult) -> str:
    """The startup report in a few lines, for the compact window: who, whether ready, what needs attention."""
    if not result.ok:
        return f"Startup did not complete: {result.meaning} (exit {result.exit_code}).\n{result.stderr.strip()}"
    lines = result.stdout.splitlines()
    summary = [lines[0] if lines else "RUDRA", "Ready: startup complete."]
    attention = [line.strip() for line in lines if line.startswith("  [")]
    summary += ["Attention:", *(f"  {line}" for line in attention)] if attention else ["Nothing needs attention."]
    return "\n".join(summary)


def display_command(argv: list[str] | tuple[str, ...]) -> str:
    """The same command as it would be typed in a terminal."""
    program = "RUDRA-CLI.exe" if getattr(sys, "frozen", False) else "python -m app"
    return f"{program} {subprocess.list2cmdline(list(argv))}".rstrip()


@lru_cache(maxsize=1)
def command_names() -> tuple[str, ...]:
    """Every command the command line accepts, from its own parser."""
    for action in cli._build_parser()._actions:
        if isinstance(action, argparse._StoreAction) and action.dest == "command":
            return tuple(action.choices)
    return ()


def split_command_line(text: str) -> list[str]:
    """Split a typed command line as Windows splits what follows a program's name."""
    if sys.platform != "win32":
        return shlex.split(text)
    shell32 = ctypes.WinDLL("shell32")
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    words = shell32.CommandLineToArgvW(f"RUDRA {text}", ctypes.byref(count))
    if not words:
        raise FormError("Windows could not split that command line.")
    try:
        return [words[index] for index in range(1, count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(words, ctypes.c_void_p))


# ------------------------------------------------------------------ forms to command lines


def _required(value: str, message: str) -> str:
    text = value.strip()
    if not text:
        raise FormError(message)
    return text


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def status(command: str) -> list[str]:
    if command not in STATUS_COMMANDS:
        raise FormError(f"Not a status command: {command}")
    return [command]


def ask(question: str, *, act: bool = False, confirm: bool = False) -> list[str]:
    """`ask`, answered as JSON for the answer view. Without `act`, an action request runs on
    the simulated computer (--dry-run)."""
    argv = ["ask", _required(question, "Type a question or a request."), "--json"]
    if not act:
        argv.append("--dry-run")
    elif confirm:
        argv.append("--confirm")
    return argv


def is_answer_command(argv: list[str] | tuple[str, ...]) -> bool:
    """An `ask` run as JSON: the window shows its answer, with the sources on request."""
    return bool(argv) and argv[0] == "ask" and "--json" in argv


def lookup(mode: str, value: str) -> list[str]:
    """A concept by exact name (`lookup`), any stored item by identifier or keyword (`query`)."""
    text = _required(value, "Type a concept name, an identifier or a keyword.")
    if mode == "name":
        return ["lookup", "--name", text]
    if mode == "identifier":
        return ["query", text]
    if mode == "keyword":
        return ["query", "--keyword", text]
    raise FormError(f"Not a lookup mode: {mode}")


def calculate(target: str, formulas: str, inputs: str, assumptions: str = "", admit: str = "") -> list[str]:
    """`calculate`: one formula, input or assumption per line, at most one admitted equation."""
    argv = ["calculate", _required(target, "Name the symbol to calculate, for example I.")]
    for line in _lines(formulas):
        argv += ["--formula", line]
    for line in _lines(inputs):
        argv += ["--input", line]
    for line in _lines(assumptions):
        argv += ["--assume", line]
    if admit.strip():
        argv += ["--admit", admit.strip()]
    return argv


def provenance(identifier: str) -> list[str]:
    return ["provenance", _required(identifier, "Type an identifier, for example K-00000001.")]


def extract(pdf: str, manual: str = "") -> list[str]:
    argv = ["extract", _required(pdf, "Choose a document to import.")]
    if manual.strip():
        argv += ["--manual", manual.strip()]
    return argv


def typed(text: str) -> list[str]:
    """A command line typed in full, as it would follow the program's name."""
    words = split_command_line(_required(text, "Type a command, for example version."))
    if [word.lower() for word in words] in (["help"], ["?"], ["/?"]):
        return list(HELP_ARGUMENTS)
    return words


def assistant(text: str, *, act: bool = False, confirm: bool = False) -> list[str]:
    """The compact window's one input: '/command ...' runs a command; anything else is asked."""
    stripped = _required(text, "Type a question, or /help.")
    if stripped.startswith("/"):
        return typed(stripped[1:] or "help")
    return ask(stripped, act=act, confirm=confirm)


# ------------------------------------------------------------------ approval


def _command_of(argv: list[str] | tuple[str, ...]) -> str | None:
    names = set(command_names())
    return next((word for word in argv if word in names), None)


def _flag(argv: list[str] | tuple[str, ...], name: str) -> bool:
    return any(word == name or word.startswith(name + "=") for word in argv)


def knowledge_database(project_root: Path) -> Path | None:
    """Where this project keeps its knowledge database, per its configuration."""
    try:
        loaded = load_config(project_root=project_root)
        return database_path(PathLayout.from_config(project_root, loaded.config), DatabaseRole.KNOWLEDGE)
    except Exception:  # noqa: BLE001 - an unreadable configuration only loses the path in a message
        return None


def approval(argv: list[str] | tuple[str, ...], project_root: Path) -> Approval | None:
    """What to ask before running `argv`, or None when it writes nothing and acts on nothing.

    The command line itself asks nothing; its rules still apply after this question (the
    permission engine, `--confirm`).
    """
    command = _command_of(argv)
    database = knowledge_database(project_root)
    where = f"the knowledge database {database}" if database else "this project's knowledge database"
    backup = ""
    if database is not None and database.exists():
        backup = ("\n\nThat database already holds knowledge. To be able to go back, export a backup "
                  "first (Backup page, Export Knowledge Base).")
    if command == "db":
        return Approval("Create or upgrade the database",
                        f"This creates {where}, or migrates it to the current schema after taking a "
                        f"backup.{backup}")
    if command == "extract":
        return Approval("Import a document",
                        f"This copies the document into RUDRA's document store and writes its knowledge to "
                        f"{where}, creating or migrating the database first if needed.{backup}")
    if command in KNOWLEDGE_WRITERS:
        return Approval(f"Run '{command}'", f"'{command}' writes to {where}.{backup}")
    if command == "research" and _flag(argv, "--site"):
        return Approval("Retrieve a web page",
                        "This connects to the Internet to retrieve the one page you named, and "
                        f"stores the page in {where}.{backup}")
    if command == "source" and _flag(argv, "--delete-file"):
        return Approval("Delete RUDRA's copy of a document",
                        "This deletes RUDRA's preserved copy of the document. The knowledge and its "
                        "provenance stay; your original file is never touched.")
    if command == "procedure" and (_flag(argv, "--execute") or _flag(argv, "--dry-run")):
        live = "live on this computer" if _flag(argv, "--execute") else "on the simulated computer"
        return Approval("Run a procedure", f"This runs the procedure {live} and records each step's "
                                           f"result in {where}.")
    if command == "voice" and _flag(argv, "--say"):
        return None  # speaks into the new WAV file --audio names; nothing is carried out
    if command == "act" and _flag(argv, "--execute"):
        return Approval("Act on this computer", "This carries out the action on this computer, after "
                                                "RUDRA's permission check.")
    if command in ("do", "ask", "voice") and not _flag(argv, "--dry-run"):
        return Approval("Act on this computer",
                        f"'{command}' carries out what it understands as an action live on this computer, "
                        "after RUDRA's permission check. Add --dry-run to use the simulated computer.")
    return None


# ------------------------------------------------------------------ resources


def bundle_root() -> Path:
    """The source tree, or the packaged program's `_internal` folder."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))


def asset(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name


DOCUMENTS = {"README": "README.md", "Limitations": "docs/LIMITATIONS.md"}


def document(title: str) -> str:
    """A document the Help page shows, read from the source tree or the packaged program."""
    path = bundle_root() / DOCUMENTS[title]
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"{DOCUMENTS[title]} could not be read: {exc}"
