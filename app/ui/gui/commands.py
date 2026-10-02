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
import json
import re
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
    """The startup report in a few words, for the compact window: ready, or what needs attention."""
    if not result.ok:
        return f"RUDRA could not start properly (exit {result.exit_code}).\n{result.stderr.strip()}"
    attention = startup_attention(result)
    summary = ["RUDRA is ready."]
    summary += ["Attention:", *(f"  {line}" for line in attention)] if attention else []
    return "\n".join(summary)


#: Printed by `_print_failure` (app.ui.cli.main) before every `RudraError`'s own report;
#: the line after it is always `FailureReport.summary` - the one-line, plain-English
#: statement of what went wrong (Part 4 section 136). Relied on here, not duplicated.
_FAILURE_BANNER = "RUDRA could not continue. ["


def failure_headline(result: CommandResult) -> str:
    """The plain-English first line of a failed command's own report, for a normal user.

    The rest of `result.stderr` (Reason, Stage, Missing, Next options - still in plain
    English, just more detailed) belongs behind a "Details" disclosure, not in the headline.
    """
    lines = result.stderr.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(_FAILURE_BANNER) and index + 1 < len(lines):
            return lines[index + 1].strip()
    return f"RUDRA could not complete this (exit {result.exit_code}: {result.meaning})."


def failure_advice(result: CommandResult) -> tuple[str, ...]:
    """What a failed command's own report tells the person to do, in its own words.

    The reason is shown when the report was written for people (a bad request: "save the file as
    .docx"); a parser's or the system's wording is not. The first suggested next step is added.
    """
    lines = result.stderr.splitlines()
    advice: list[str] = []
    for index, line in enumerate(lines):
        if not line.startswith(_FAILURE_BANNER):
            continue
        written_for_people = "[INVALID_INPUT]" in line or "[MISSING_INFORMATION]" in line
        for follower in lines[index + 2:]:
            if follower.startswith("Reason:") and written_for_people:
                reason = follower[len("Reason:"):].strip()
                if len(reason) < 220 and ":\\" not in reason:  # a path in the reason would only confuse
                    advice.append(reason)
            elif follower.strip().startswith("1. "):
                advice.append(follower.strip()[3:].rstrip("."))
                break
        break
    return tuple(advice)


def is_extract_command(argv: list[str] | tuple[str, ...]) -> bool:
    """Adding a document (`extract`): the window shows a plain summary, detail on request."""
    return bool(argv) and argv[0] == "extract"


def index() -> list[str]:
    """Bring the derived keyword-search index up to date (the unchanged `index` command): Add
    document runs it right after `extract`, so what was added is searchable at once."""
    return ["index"]


def inventory(kind: str = "", document: str = "") -> list[str]:
    """What RUDRA knows (the `inventory` command, as JSON): summaries, or - for a kind of
    knowledge - the items themselves, optionally only those of one document."""
    argv = ["inventory", "--json"]
    if kind:
        argv += ["--knowledge-type", kind]
    if document:
        argv += ["--in-document", document]
    return argv


def is_friendly_command(argv: list[str] | tuple[str, ...]) -> bool:
    """Commands whose result the window shows in its own words, never as a command transcript."""
    return bool(argv) and argv[0] in ("ask", "extract", "inventory")


def working_text(argv: list[str] | tuple[str, ...]) -> str:
    """What the output says while a command runs: in words, not the command line."""
    command = argv[0] if argv else "start"
    return {
        "extract": "Adding the document: reading its pages and finding what it states. This can take a minute "
                   "for a long book.",
        "ask": "Looking through your documents...",
        "inventory": "Listing what RUDRA knows...",
    }.get(command, "Working...")


def finished_state(result: CommandResult) -> tuple[str, bool]:
    """The status bar after a command whose result the window words itself: (text, all is well).

    An answer of "I do not know" is an answer, not a failure, so asking never leaves an error
    code in the status bar; only a document that could not be added does.
    """
    command = result.argv[0] if result.argv else ""
    if command == "extract":
        return ("READY · document added", True) if result.ok else ("NOT ADDED · see the message", False)
    if command == "ask":
        if result.stdout.lstrip().startswith("{") or result.ok or result.exit_code == 3:
            return "READY · answered", True
        return "NOT ANSWERED · see the message", False
    return ("READY", True) if result.ok else ("NOT COMPLETED · see the message", False)


def startup_attention(result: CommandResult) -> tuple[str, ...]:
    """What the startup report says is limited on this computer, as one plain line each:
    "[WARNING] RAM: Little headroom ..." becomes "Memory is low: Little headroom ..."."""
    names = {"RAM": "Memory", "Disk": "Disk space", "OCR": "Text recognition"}
    found = []
    for line in result.stdout.splitlines():
        if not line.startswith("  ["):
            continue
        match = re.match(r"\s*\[(\w+)\]\s+([^:]+):\s*(.*)", line)
        if match:
            found.append(f"{names.get(match.group(2).strip(), match.group(2).strip())}: {match.group(3)}")
    return tuple(found)


def welcome_facts(result: CommandResult) -> dict:
    """What the first screen says about the knowledge base: counts, from the `inventory` command."""
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:
        data = {}
    totals = data.get("totals") or {}
    counts = totals.get("counts") or {}
    return {"documents": int(totals.get("documents") or 0), "counts": counts,
            "usable": (data.get("calculation") or {}).get("usable", 0),
            "uncertain": totals.get("uncertain", 0)}


@dataclass(frozen=True)
class ExtractSummary:
    """What `extract`'s (and, when it ran, `index`'s) own report means for someone who
    just wants their document added and askable - every query mode, not only the ones
    that never needed the derived index."""

    ok: bool
    already_had: bool
    document_id: str | None
    pages: str | None
    ocr_count: int
    issue_total: int
    partially_processed: bool
    #: The document can be searched and asked about straight away.
    ready: bool
    headline: str
    #: For a document that could not be added: what to do about it.
    advice: tuple[str, ...] = ()
    #: Added, but nothing in it was stored as knowledge (no definitions, equations or the like).
    empty: bool = False
    #: What RUDRA found in it, in plain words (from `inventory`), when that was read.
    stored: tuple[str, ...] = ()
    not_stored: tuple[str, ...] = ()
    linked: str | None = None


def _counted(number: str, unit: str) -> str:
    """"1 page", "137 pages": the unit agrees with the number."""
    if number != "1":
        return f"{number} {unit}"
    return f"{number} " + {"pages": "page", "slides": "slide", "sections": "section", "sheet parts": "sheet part",
                           "images/pages": "image/page"}.get(unit, unit)


def _field(lines: list[str], prefix: str) -> str | None:
    return next((line[len(prefix):].strip() for line in lines if line.startswith(prefix)), None)


def extract_summary(results: list[CommandResult]) -> ExtractSummary:
    """`extract`'s own plain-English report (app.ui.cli.main._cmd_extract), read for the
    window's summary - never a second source of truth about what happened - and, when the
    `inventory` result that follows it is given, what RUDRA found in the document.

    `results` is `[extract's result]`, or `extract`, `index` and `inventory` in that order: Add
    Document runs `index` and `inventory` right after a successful `extract`, so what was added
    is searchable at once and the person is told what was stored and what was not; a raw
    `extract` typed on the Command page runs alone.
    """
    result = results[0]
    lines = result.stdout.splitlines()
    document_line = _field(lines, "Document   :")
    already_had = bool(document_line and "already ingested" in document_line)
    document_id = document_line.split(maxsplit=1)[0] if document_line else None
    pages = None
    if document_line:
        found = re.search(r"(\d[\d,]*) (pages|slides|sheet parts|images/pages|sections)\b", document_line)
        if found:
            pages = _counted(found.group(1), found.group(2))
    ocr_line = _field(lines, "OCR        :")
    ocr_match = re.match(r"(\d+)", ocr_line) if ocr_line else None
    ocr_count = int(ocr_match.group(1)) if ocr_match else 0
    issue_total = 0
    in_issues = False
    for line in lines:
        if line.startswith("Issues     :"):
            in_issues = True
            continue
        if in_issues:
            if not line.startswith("  ") or ":" in line:
                break
            parts = line.split()
            if parts and parts[-1].isdigit():
                issue_total += int(parts[-1])
    status_line = _field(lines, "Document status:")
    partially_processed = bool(status_line and "PARTIALLY_PROCESSED" in status_line)
    indexed = next((r for r in results[1:] if r.argv and r.argv[0] == "index"), None)
    ready = result.ok and (already_had or (indexed is not None and indexed.ok))
    if not result.ok:
        headline = failure_headline(result)
    elif already_had:
        headline = "This document is already in your knowledge base."
    elif partially_processed:
        headline = "Added to your knowledge base. Some of its content could not be stored."
    else:
        headline = "Added to your knowledge base."
    listing = next((r for r in results[1:] if r.argv and r.argv[0] == "inventory"), None)
    stored, not_stored, linked = _found_in(listing, document_id)
    empty = bool(result.ok and not already_had and listing is not None and listing.ok and not stored
                 and not not_stored)
    if empty:
        headline = "Added, but RUDRA found nothing in it to store as knowledge."
    advice = () if result.ok else failure_advice(result)
    return ExtractSummary(result.ok, already_had, document_id, pages, ocr_count, issue_total,
                          partially_processed, ready, headline, advice, empty, stored, not_stored, linked)


def _found_in(result: CommandResult | None, document_id: str | None) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """What the `inventory` command says one document gave: stored kinds, what was not stored, links."""
    if result is None or not result.ok or document_id is None:
        return (), (), None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return (), (), None
    document = next((d for d in data.get("documents", ()) if d.get("id") == document_id), None)
    if document is None:
        return (), (), None
    labels = {"CONCEPT": "concept", "DEFINITION": "definition", "EQUATION": "equation", "VARIABLE": "variable",
              "UNIT": "unit", "PROPERTY": "property", "RULE": "rule", "RELATIONSHIP": "relationship",
              "EXAMPLE": "example", "PROCEDURE": "procedure"}
    plural = {"property": "properties"}
    stored = []
    for kind, label in labels.items():
        count = document.get("stored", {}).get(kind, 0)
        if count:
            stored.append(f"{count} {plural.get(label, label + 's') if count != 1 else label}")
    not_stored = tuple(f"{group['count']} {group['short']}" for group in document.get("problems", ())
                       if group.get("not_stored"))
    linked = None
    if document.get("linked"):
        linked = f"{document['linked']} already known from other evidence"
    return tuple(stored), not_stored, linked


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
        backup = ("\n\nYour knowledge base already holds knowledge. To be able to go back, use Settings > "
                  "Back up your knowledge first.")
    if command == "db":
        return Approval("Create or upgrade the knowledge base",
                        f"This creates {where}, or upgrades it to the current format after taking a safety "
                        f"copy.{backup}")
    if command == "extract":
        return Approval("Add a document",
                        f"RUDRA keeps a private copy of the document and adds what it finds to {where}. Your "
                        f"original file is not changed.{backup}")
    if command in KNOWLEDGE_WRITERS:
        return Approval(f"Run '{command}'", f"'{command}' changes {where}.{backup}")
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


# ------------------------------------------------------------------ uninstalling


#: installer/RUDRA.iss's `AppId` (the braces are literal: Inno Setup's `{{...}` escapes to
#: `{...}`). Identifies the one uninstall entry Inno Setup itself registers; RUDRA never
#: builds a second, custom uninstall path (master specification: "use the real uninstaller").
RUDRA_APP_ID = "{05B1382D-1FC2-4ADB-888C-F03C39A3DF08}"


def find_uninstaller() -> str | None:
    """The command Inno Setup registered to uninstall this installation, or None when RUDRA
    was not installed by it (for example, running from source - there is nothing to find)."""
    if sys.platform != "win32":
        return None
    import winreg

    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{RUDRA_APP_ID}_is1"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            command, _ = winreg.QueryValueEx(key, "UninstallString")
    except OSError:
        return None
    return command or None


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
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"{DOCUMENTS[title]} could not be read: {exc}"
    # The part of the README about building RUDRA from source is not for someone using it.
    text = text.split("\n# For developers", 1)[0].rstrip().removesuffix("---").rstrip()
    return re.sub(r"\*\*For developers\*\*\n(?:\n- .*)+\n", "", text) + "\n"
