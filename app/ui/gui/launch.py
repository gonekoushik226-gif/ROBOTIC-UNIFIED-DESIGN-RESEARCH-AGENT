"""Starting the desktop window: its few options, display scaling, the taskbar identity (ADR 0057).

    python -m app.ui.gui [--project-root PATH] [--full]      from the source tree
    RUDRA.exe [--project-root PATH] [--full]                 packaged

The window starts as section 139's compact assistant; --full opens the full interface.
The project root defaults as the command line's does. Commands are not options of the
window: RUDRA.exe given one explains that the command line is RUDRA-CLI.exe.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from app.ui.cli import main as cli

APP_ID = "RUDRA.Desktop"


def parser() -> argparse.ArgumentParser:
    options = argparse.ArgumentParser(prog="RUDRA", description="RUDRA's desktop window.")
    options.add_argument("--project-root", default=None, help="The project folder, as for the command line.")
    options.add_argument("--full", action="store_true", help="Open the full interface instead of the assistant.")
    options.add_argument("--self-test", default=None, metavar="REPORT", help=argparse.SUPPRESS)
    options.add_argument("--self-test-pdf", default=None, metavar="PDF", help=argparse.SUPPRESS)
    return options


def prepare_display() -> None:
    """Sharp text on scaled displays, and RUDRA's own taskbar button (both best effort)."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        return


def refuse_commands(unknown: list[str]) -> int:
    """RUDRA.exe was given what looks like a command line: say where the command line is."""
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "RUDRA",
        "RUDRA.exe opens RUDRA's window; it takes no commands.\n\n"
        f"For '{' '.join(unknown)}', use the command line: RUDRA-CLI.exe {' '.join(unknown)}\n"
        "(from the source tree: python -m app ...), or the window's Command page.",
        parent=root,
    )
    root.destroy()
    return 2


def main(argv: list[str] | None = None) -> int:
    args, unknown = parser().parse_known_args(argv)
    if unknown:
        return refuse_commands(unknown)
    project_root = cli._project_root(argparse.Namespace(project_root=args.project_root))
    prepare_display()
    if args.self_test is not None:
        from app.ui.gui import selftest

        if args.project_root is None:
            return refuse_commands(["--self-test needs --project-root: a scratch project folder"])
        return selftest.run(Path(args.self_test), project_root,
                            Path(args.self_test_pdf) if args.self_test_pdf else None)

    from app.ui.gui.window import RudraWindow

    root = tk.Tk()
    RudraWindow(root, project_root, full=args.full)
    root.mainloop()
    return 0
