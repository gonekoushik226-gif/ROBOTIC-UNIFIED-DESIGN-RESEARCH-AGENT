"""The simulated platform: a deterministic, in-memory computer (ADR 0046 P14-4, P14-9).

It implements the platform port for the dry run and the tests. **Nothing it does reaches
the real machine:** no process is started, no window is touched, no key is sent and
nothing is written to disk.

- **Applications:** a registered launch method "starts" its application: a window with the
  registry's sample title and the first detection image appears. Methods listed in
  `unavailable` cannot be resolved (a failing precondition); methods in `silent` start
  nothing (a launch that cannot be verified, to exercise recovery).
- **Files:** with `disk=True` (the dry run), reads fall through to the real disk, read-only,
  so preconditions such as "the parent folder exists" reflect the real machine; every
  write is kept in an in-memory overlay. With `disk=False` (tests), the file system is
  in memory only, seeded with `folders`.
- **Everything else** - typed text, keys, clicks, scrolls, the clipboard, screenshots - is
  recorded in `log`.
- **A focused edit field:** by default the focused control cannot be read back, as with
  most real controls. With `field` set to a string, the front window has a readable edit
  field holding that text: typed text and ctrl+v (the clipboard) are appended to it.

Handles, process identifiers and timestamps come from counters, so a run is reproducible.
"""

from pathlib import Path, PureWindowsPath

from app.actions import png
from app.actions.ports import FocusedText, LaunchOutcome, WindowInfo
from app.applications import APPLICATIONS, Application, LaunchMethod


class SimulatedPlatform:
    """An in-memory computer for dry runs and tests. `live` is always False."""

    live = False

    def __init__(
        self,
        *,
        disk: bool = False,
        folders: tuple[str, ...] = (),
        unavailable: tuple[str, ...] = (),
        silent: tuple[str, ...] = (),
        open_windows: tuple[str, ...] = (),
        applications: tuple[Application, ...] = APPLICATIONS,
        screen: tuple[int, int] = (1920, 1080),
        field: str | None = None,
    ) -> None:
        self.name = "simulated (dry run)" if disk else "simulated"
        self._disk = disk
        self._apps = applications
        self._unavailable = set(unavailable)
        self._silent = set(silent)
        self._screen = screen
        self._windows: list[WindowInfo] = []
        self._files: dict[str, bytes] = {}
        self._folders: set[str] = {self._key(f) for f in folders}
        self._removed: set[str] = set()
        self._mtime: dict[str, float] = {}
        self._tick = 0.0
        self._next = 0x1000
        self.clipboard: str | None = None
        #: The text of the front window's focused edit field; None when it cannot be read.
        self.field = field
        self.log: list[str] = []
        for name in open_windows:
            self._open(next(a for a in applications if a.name == name))

    # ------------------------------------------------------------------- helpers

    @staticmethod
    def _key(path: str) -> str:
        return str(PureWindowsPath(path)).casefold()

    def _open(self, app: Application) -> WindowInfo:
        self._next += 4
        window = WindowInfo(self._next, app.detection.sample_title, "SimulatedWindow",
                            self._next // 4, app.detection.process_images[0])
        self._windows.append(window)
        return window

    def _app_of(self, method: LaunchMethod) -> Application | None:
        return next((a for a in self._apps if method in a.launch), None)

    def _stamp(self, path: str) -> None:
        self._tick += 1.0
        self._mtime[self._key(path)] = self._tick

    # --------------------------------------------------------------------- state

    def windows(self) -> tuple[WindowInfo, ...]:
        return tuple(self._windows)

    def foreground(self) -> WindowInfo | None:
        return self._windows[-1] if self._windows else None

    def screen_size(self) -> tuple[int, int]:
        return self._screen

    def clipboard_text(self) -> str | None:
        return self.clipboard

    def focused_text(self) -> FocusedText | None:
        if self.field is None or not self._windows:
            return None
        return FocusedText(self._windows[-1].handle + 1, "Edit", self.field)

    def settle(self, seconds: float) -> None:
        """No time passes in a simulation."""

    # ------------------------------------------------------- applications, windows

    def can_launch(self, method: LaunchMethod) -> tuple[bool, str]:
        if method.value in self._unavailable:
            return False, f"{method.kind.value} {method.value} cannot be resolved on this (simulated) machine"
        return True, f"{method.kind.value} {method.value} (simulated: assumed resolvable)"

    def launch(self, method: LaunchMethod) -> LaunchOutcome:
        self.log.append(f"launch {method.kind.value} {method.value}")
        app = self._app_of(method)
        if app is None or method.value in self._unavailable:
            return LaunchOutcome(False, "no application answers to this method")
        if method.value in self._silent:
            return LaunchOutcome(True, "the launch call returned; no window will appear (simulated)")
        self._open(app)
        return LaunchOutcome(True, "started (simulated)")

    def open_with_default(self, path: str) -> LaunchOutcome:
        self.log.append(f"open {path}")
        self._next += 4
        name = PureWindowsPath(path).name
        self._windows.append(WindowInfo(self._next, f"{name} - Viewer", "SimulatedWindow", self._next // 4, "viewer.exe"))
        return LaunchOutcome(True, "opened with the default application (simulated)")

    def close_window(self, handle: int) -> bool:
        self.log.append(f"close window {handle}")
        before = len(self._windows)
        self._windows = [w for w in self._windows if w.handle != handle]
        return len(self._windows) < before

    # --------------------------------------------------------------------- files

    def exists(self, path: str) -> bool:
        key = self._key(path)
        if key in self._removed:
            return False
        if key in self._files or key in self._folders:
            return True
        return self._disk and Path(path).exists()

    def is_dir(self, path: str) -> bool:
        key = self._key(path)
        if key in self._removed or key in self._files:
            return False
        if key in self._folders:
            return True
        return self._disk and Path(path).is_dir()

    def read_bytes(self, path: str) -> bytes:
        key = self._key(path)
        if key in self._removed:
            raise FileNotFoundError(path)
        if key in self._files:
            return self._files[key]
        if self._disk:
            return Path(path).read_bytes()
        raise FileNotFoundError(path)

    def modified(self, path: str) -> float | None:
        key = self._key(path)
        if key in self._mtime:
            return self._mtime[key]
        if self._disk and key not in self._removed and Path(path).exists():
            return 0.0
        return None

    def write_new(self, path: str, data: bytes) -> None:
        if self.exists(path):
            raise FileExistsError(path)
        key = self._key(path)
        self._removed.discard(key)
        self._files[key] = bytes(data)
        self._stamp(path)
        self.log.append(f"write {path} ({len(data)} bytes)")

    def copy_new(self, source: str, destination: str) -> None:
        self.write_new(destination, self.read_bytes(source))

    def move_new(self, source: str, destination: str) -> None:
        data = self.read_bytes(source)
        self.write_new(destination, data)
        key = self._key(source)
        self._files.pop(key, None)
        self._removed.add(key)
        self.log.append(f"remove {source}")

    def make_dir(self, path: str) -> None:
        if self.exists(path):
            raise FileExistsError(path)
        key = self._key(path)
        self._removed.discard(key)
        self._folders.add(key)
        self.log.append(f"make folder {path}")

    # ------------------------------------------------------------ input, screen

    def send_text(self, text: str) -> None:
        self.log.append(f"type {len(text)} characters")
        if self.field is not None and self._windows:
            self.field += text

    def send_keys(self, keys: tuple[str, ...]) -> None:
        self.log.append("keys " + "+".join(keys))
        if keys == ("ctrl", "c") and self._windows:
            self.clipboard = f"(selection in {self._windows[-1].title})"
        if keys == ("ctrl", "v") and self.field is not None and self._windows:
            self.field += self.clipboard or ""

    def click(self, x: int, y: int, button: str, count: int) -> None:
        self.log.append(f"click {button} x{count} at {x},{y}")

    def scroll(self, direction: str, amount: int) -> None:
        self.log.append(f"scroll {direction} {amount}")

    def screenshot(self, path: str) -> tuple[int, int]:
        width, height = self._screen
        self.write_new(path, png.blank(width, height))
        return width, height
