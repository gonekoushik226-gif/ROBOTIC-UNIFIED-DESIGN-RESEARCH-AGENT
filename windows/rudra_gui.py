"""Entry point of the packaged RUDRA.exe: the desktop window (ADR 0057).

RUDRA.exe is a windowed program - no console opens with it - so Python starts it with no
standard output or error at all. They are pointed at the null device, so that nothing
written to them can fail; every command's own output is captured by the window
(`app.ui.gui.commands.run`). The command line is RUDRA-CLI.exe (`rudra_launcher.py`).
"""

from __future__ import annotations

import os
import sys

from app.ui.gui.launch import main

if __name__ == "__main__":
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # open for the process's life
    sys.exit(main(sys.argv[1:]))
