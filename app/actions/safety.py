"""Parameter safety: confined paths and a closed key vocabulary (ADR 0046 P14-10).

**Paths** (`ARCHITECTURE.md` §14.4): an action's path must be an absolute Windows path on a
drive (`C:\\...`); network paths, device names (`CON`, `NUL`, `COM1`, …), wildcard and
reserved characters, and `..` components are refused. A path is checked before any plan
is built; nothing here touches the disk.

**Keys:** a key name comes from a closed table with its Windows virtual-key code, so a
key can be sent (Phase 15) exactly as named; modifiers are `ctrl`, `alt`, `shift`, `win`.
"""

import re
from pathlib import PureWindowsPath

_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_BAD_CHARACTERS = re.compile(r'[<>"|?*\x00-\x1f]')

#: Virtual-key codes (Microsoft "Virtual-Key Codes") for the names an action accepts.
KEYS: dict[str, int] = {
    "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "space": 0x20,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
}
MODIFIERS: dict[str, int] = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
_KEY_ALIASES = {"esc": "escape", "return": "enter", "del": "delete", "control": "ctrl", "pgup": "pageup",
                "pgdn": "pagedown", "windows": "win"}


class UnsafeValue(ValueError):
    """A parameter value refused by the safety rules; the message says which rule."""


def confined_path(text: str) -> str:
    """The absolute, confined form of a path, or `UnsafeValue`."""
    if not isinstance(text, str) or not text.strip():
        raise UnsafeValue("a path is required")
    raw = text.strip()
    if raw.startswith(("\\\\", "//")):
        raise UnsafeValue("network (UNC) paths are not accepted")
    path = PureWindowsPath(raw)
    if not path.drive or not path.is_absolute():
        raise UnsafeValue(f"{raw!r} is not an absolute path on a drive, such as C:\\folder\\file.txt")
    for part in path.parts[1:]:
        if part in (".", ".."):
            raise UnsafeValue("'.' and '..' components are not accepted")
        if _BAD_CHARACTERS.search(part) or ":" in part:
            raise UnsafeValue(f"{part!r} contains a character Windows reserves")
        if part.split(".")[0].casefold() in _DEVICES:
            raise UnsafeValue(f"{part!r} is a reserved device name")
    return str(path)


def bare_name(text: str) -> str:
    """A file or folder name with no path in it."""
    name = text.strip() if isinstance(text, str) else ""
    if not name or any(c in name for c in "\\/:") or name in (".", ".."):
        raise UnsafeValue("a new name is a bare file name, with no path")
    if _BAD_CHARACTERS.search(name) or name.split(".")[0].casefold() in _DEVICES:
        raise UnsafeValue(f"{name!r} is not an acceptable file name")
    return name


def key_name(text: str) -> str:
    """One non-modifier key from the closed table."""
    name = _KEY_ALIASES.get(text.strip().casefold(), text.strip().casefold())
    if name not in KEYS:
        raise UnsafeValue(f"{text!r} is not a key name RUDRA sends (for example enter, tab, f5, a)")
    return name


def key_combination(text: str) -> tuple[str, ...]:
    """Modifiers then exactly one key, joined by '+', such as ctrl+shift+s."""
    parts = [p.strip().casefold() for p in text.split("+")] if isinstance(text, str) else []
    parts = [_KEY_ALIASES.get(p, p) for p in parts]
    if len(parts) < 2 or any(not p for p in parts):
        raise UnsafeValue("a hotkey is one or more modifiers and one key, such as ctrl+s")
    *modifiers, key = parts
    if any(m not in MODIFIERS for m in modifiers) or len(set(modifiers)) != len(modifiers):
        raise UnsafeValue(f"the modifiers of {text!r} must be distinct, from ctrl, alt, shift, win")
    return (*modifiers, key_name(key))
