"""The platform port: what an action may ask of the computer (ADR 0046 P14-4).

The action engine depends on this interface, never on a concrete adapter
(`ARCHITECTURE.md` §4.2). Phase 14 provides `SimulatedPlatform`; Phase 15 provides the
Windows adapter behind the same interface. Every method either reads state or performs
exactly one operation; none decides whether an operation is allowed - that is the
permission engine's job (Phase 15), and none decides whether it succeeded - that is the
engine's postcondition check (section 18).
"""

from dataclasses import dataclass
from typing import Protocol

from app.applications import LaunchMethod


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """One visible top-level window, as the platform sees it now."""

    handle: int
    title: str
    class_name: str
    pid: int
    #: The process image name, case-folded (for example `notepad.exe`).
    image: str


@dataclass(frozen=True, slots=True)
class FocusedText:
    """The text of the edit field that has the keyboard focus, read back from the field itself.

    Only standard edit fields are read, never a password field. The text is used to check
    an action's effect and is not copied into any report.
    """

    #: The field's own window handle, so a check can tell when the focus moved.
    handle: int
    class_name: str
    text: str


@dataclass(frozen=True, slots=True)
class LaunchOutcome:
    """What a launch call returned. It says nothing about whether the objective was met."""

    started: bool
    detail: str


class Platform(Protocol):
    """The operations an action may perform, and the state it may inspect."""

    #: A short name for reports; `live` is False for any simulation.
    name: str
    live: bool

    # --- state
    def windows(self) -> tuple[WindowInfo, ...]: ...
    def foreground(self) -> WindowInfo | None: ...
    def screen_size(self) -> tuple[int, int]: ...
    def clipboard_text(self) -> str | None: ...
    def focused_text(self) -> FocusedText | None: ...
    def settle(self, seconds: float) -> None: ...

    # --- applications and windows
    def can_launch(self, method: LaunchMethod) -> tuple[bool, str]: ...
    def launch(self, method: LaunchMethod) -> LaunchOutcome: ...
    def open_with_default(self, path: str) -> LaunchOutcome: ...
    def close_window(self, handle: int) -> bool: ...

    # --- files (absolute, confined paths only; `write_new` never overwrites)
    def exists(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def read_bytes(self, path: str) -> bytes: ...
    def modified(self, path: str) -> float | None: ...
    def write_new(self, path: str, data: bytes) -> None: ...
    def copy_new(self, source: str, destination: str) -> None: ...
    def move_new(self, source: str, destination: str) -> None: ...
    def make_dir(self, path: str) -> None: ...

    # --- input and screen
    def send_text(self, text: str) -> None: ...
    def send_keys(self, keys: tuple[str, ...]) -> None: ...
    def click(self, x: int, y: int, button: str, count: int) -> None: ...
    def scroll(self, direction: str, amount: int) -> None: ...
    def screenshot(self, path: str) -> tuple[int, int]: ...
