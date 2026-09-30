"""Entry point of the packaged RUDRA-CLI.exe, the command line (ADRs 0056, 0057; RUDRA.exe is the window).

With arguments, RUDRA-CLI.exe is exactly ``python -m app <arguments>``: the same parser,
the same commands, the same output and exit codes.

Started with no arguments in a console window of its own - a double-click in
Explorer - it runs the default ``start`` command and then keeps the window open as a
session: each line typed is run as a separate ``RUDRA-CLI.exe <line>`` process, exactly
as if it had been typed after ``RUDRA-CLI.exe`` in a terminal. The session adds no
command and changes none; ``exit`` closes it. Started with no arguments from a
terminal, a script or a pipe, RUDRA-CLI.exe runs ``start`` and exits, like
``python -m app``.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from collections.abc import Callable, Sequence

from app.ui.cli.main import main

PROMPT = "RUDRA> "
EXIT_WORDS = frozenset({"exit", "quit"})
HELP_WORDS = frozenset({"help", "?"})

SESSION_HELP = """\
RUDRA session: this window stays open. Type a command as you would after RUDRA-CLI.exe:
  version
  calculate I --formula "I = V / R" --input "V=10 V" --input "R=5 Ω"
  extract "C:\\path\\to\\book.pdf"     and then     ask "What is <a concept it defines>?"
'help' lists every command and flag; 'exit' closes the window."""


def owns_console() -> bool:
    """True when this process is the only one attached to its console window.

    That is the case when Explorer starts RUDRA-CLI.exe, and never when a terminal does:
    the terminal's shell is attached to the same console.
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return False
    processes = (ctypes.c_uint32 * 2)()
    return kernel32.GetConsoleProcessList(processes, 2) == 1


def is_double_click_start(argv: Sequence[str]) -> bool:
    """No arguments, the packaged executable, and an interactive window of its own."""
    return (
        len(argv) <= 1
        and bool(getattr(sys, "frozen", False))
        and sys.stdin is not None
        and sys.stdin.isatty()
        and sys.stdout is not None
        and sys.stdout.isatty()
        and owns_console()
    )


def session_arguments(line: str) -> str | None:
    """What one typed line runs after RUDRA-CLI.exe: "" for nothing, None to close the session."""
    text = line.strip()
    if text.lower() in EXIT_WORDS:
        return None
    if text.lower() in HELP_WORDS:
        return "--help"
    return text


def run_session(
    executable: str,
    *,
    read: Callable[[str], str] = input,
    run: Callable[..., object] = subprocess.run,
) -> int:
    """Run ``start``, then each typed line as its own ``RUDRA-CLI.exe`` process, until 'exit'."""
    main([])
    print()
    print(SESSION_HELP)
    while True:
        try:
            line = read(PROMPT)
        except EOFError:
            return 0
        except KeyboardInterrupt:
            print()
            continue
        arguments = session_arguments(line)
        if arguments is None:
            return 0
        if not arguments:
            continue
        # A string, not a list: Windows parses it exactly as it parses what a terminal
        # passes after RUDRA-CLI.exe, quotes and backslashes included.
        try:
            run(f'"{executable}" {arguments}', check=False)
        except KeyboardInterrupt:
            print()
        except OSError as exc:
            print(f"Could not run the command: {exc}", file=sys.stderr)


def launch(argv: Sequence[str]) -> int:
    """RUDRA-CLI.exe: the session on a double-click, otherwise the command line unchanged."""
    if is_double_click_start(argv):
        return run_session(sys.executable)
    return main(list(argv[1:]))


if __name__ == "__main__":
    sys.exit(launch(sys.argv))
