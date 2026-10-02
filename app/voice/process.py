"""How RUDRA starts the helper programs speech uses: invisible, with nothing to wait on."""

import subprocess


def hidden_process() -> dict:
    """`Popen` options that keep a helper program's console away from the user.

    RUDRA's window has no console, so a console program it starts is given a new console
    window of its own - a black box that flashes up whenever the microphone button is
    pressed - unless the process is created without one. The user should only ever see
    RUDRA, so the program runs with no console and a hidden window, and no standard input
    to wait on (a caller that sends input replaces `stdin`).

    The window is hidden only through these process options: a hidden-window flag on a
    PowerShell command line is what security software treats as malicious.
    """
    options: dict = {"stdin": subprocess.DEVNULL}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = info
    return options
