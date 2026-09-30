"""The registry's data: applications, launch methods, detection and verification (P14-3).

**Launch methods**, tried in order (a `LOW`-risk launch may fall back to the next method
once, ADR 0046 P14-6):

    APP_PATH     an executable registered under Windows `App Paths` (resolved at launch)
    SYSTEM_PATH  an executable at a fixed path; environment variables are expanded
    PROTOCOL     a URI protocol the shell opens, such as `calculator:`
    SHORTCUT     a Start-menu shortcut (`.lnk`) the shell opens

**Detection** is the process image names and a window-title pattern that show the
application running. **Verification** of a launch is a top-level window whose process
image and title match: a new one, or - when the application was already open - one that
was already there, reported as such (section 105).

Version is not recorded here: it is read from the running application where the platform
can read it, never assumed (P6 section 27).
"""

import re
from dataclasses import dataclass
from enum import StrEnum

REGISTRY_NAME = "RUDRA application registry"
REGISTRY_VERSION = "1"


class LaunchKind(StrEnum):
    APP_PATH = "APP_PATH"
    SYSTEM_PATH = "SYSTEM_PATH"
    PROTOCOL = "PROTOCOL"
    SHORTCUT = "SHORTCUT"


@dataclass(frozen=True, slots=True)
class LaunchMethod:
    kind: LaunchKind
    #: The executable name, path, protocol or shortcut path, as registered.
    value: str


@dataclass(frozen=True, slots=True)
class Detection:
    #: Process image names (case-folded) whose top-level windows belong to the application.
    process_images: tuple[str, ...]
    #: A regular expression a window title of the application matches.
    title_pattern: str
    #: A representative title, used by the simulated platform and the documentation.
    sample_title: str

    def matches(self, image: str, title: str) -> bool:
        return image.casefold() in self.process_images and re.search(self.title_pattern, title) is not None


@dataclass(frozen=True, slots=True)
class Application:
    name: str
    #: DESKTOP, PACKAGED (a Microsoft Store / system app) or WEB_APP.
    kind: str
    #: The names a user says for it (case-folded), including its name.
    aliases: tuple[str, ...]
    launch: tuple[LaunchMethod, ...]
    detection: Detection
    verification: str
    limitations: tuple[str, ...] = ()


_WINDOW = "a top-level window of the application: its process image and title match the registry's detection"

APPLICATIONS: tuple[Application, ...] = (
    Application(
        "Notepad", "PACKAGED", ("notepad",),
        (LaunchMethod(LaunchKind.SYSTEM_PATH, r"%SystemRoot%\System32\notepad.exe"),
         LaunchMethod(LaunchKind.APP_PATH, "notepad.exe")),
        Detection(("notepad.exe",), r"Notepad", "Untitled - Notepad"),
        _WINDOW,
        ("Windows 11 Notepad may open a new tab in an existing window instead of a new window; "
         "the launch is then verified by the existing window, reported as already open.",),
    ),
    Application(
        "Calculator", "PACKAGED", ("calculator", "calc", "windows calculator"),
        (LaunchMethod(LaunchKind.PROTOCOL, "calculator:"),
         LaunchMethod(LaunchKind.SYSTEM_PATH, r"%SystemRoot%\System32\calc.exe")),
        Detection(("applicationframehost.exe", "calculatorapp.exe"), r"^Calculator$", "Calculator"),
        _WINDOW,
        ("A packaged app: its top-level window belongs to ApplicationFrameHost.exe.",),
    ),
    Application(
        "Chrome", "DESKTOP", ("chrome", "google chrome"),
        (LaunchMethod(LaunchKind.APP_PATH, "chrome.exe"),),
        Detection(("chrome.exe",), r"Google Chrome$", "New Tab - Google Chrome"),
        _WINDOW,
        ("RUDRA opens the browser only; it does not browse, search or sign in (section 108).",),
    ),
    Application(
        "Word", "DESKTOP", ("word", "microsoft word", "ms word", "winword"),
        (LaunchMethod(LaunchKind.APP_PATH, "winword.exe"),),
        Detection(("winword.exe",), r"Word$", "Document1 - Word"),
        _WINDOW,
    ),
    Application(
        "Notepad++", "DESKTOP", ("notepad++", "notepad plus plus"),
        (LaunchMethod(LaunchKind.APP_PATH, "notepad++.exe"),),
        Detection(("notepad++.exe",), r"Notepad\+\+", "new 1 - Notepad++"),
        _WINDOW,
    ),
    Application(
        "Visual Studio Code", "DESKTOP", ("visual studio code", "vs code", "vscode"),
        (LaunchMethod(LaunchKind.SYSTEM_PATH, r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
         LaunchMethod(LaunchKind.APP_PATH, "code.exe")),
        Detection(("code.exe",), r"Visual Studio Code", "Welcome - Visual Studio Code"),
        _WINDOW,
    ),
    Application(
        "Keil µVision", "DESKTOP", ("keil", "keil uvision", "keil µvision", "uvision", "µvision"),
        (LaunchMethod(LaunchKind.SYSTEM_PATH, r"C:\Keil_v5\UV4\UV4.exe"),),
        Detection(("uv4.exe",), r"µVision", "µVision"),
        _WINDOW,
    ),
    Application(
        "MATLAB", "WEB_APP", ("matlab",),
        (LaunchMethod(LaunchKind.SHORTCUT, r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Chrome Apps\MATLAB.lnk"),),
        Detection(("chrome.exe",), r"MATLAB", "MATLAB"),
        _WINDOW,
        ("MATLAB here is a Chrome web app, not a desktop install: it needs the Internet and the "
         "user's own sign-in, which RUDRA never handles (P6 section 3).",),
    ),
)

_BY_NAME = {alias: app for app in APPLICATIONS for alias in (app.name.casefold(), *app.aliases)}


def find(name: str) -> Application | None:
    """The registered application with this name or alias (case-folded), or None."""
    if not isinstance(name, str):
        return None
    return _BY_NAME.get(" ".join(name.casefold().split()))
