"""The action catalogue: section 208's nineteen parameterised actions (ADR 0046 P14-1 ... P14-7).

Each `ActionDefinition` has its parameter schema, declared risk (section 106), the
preconditions it checks (section 104), its expected postcondition and how it is verified
(section 105), and **one** implementation (section 99). An implementation receives only
validated parameters and a `Context` (the platform port and a polling helper); it checks
its preconditions, performs its operation through the port, inspects the state and
returns an `Outcome`. It never decides whether it is allowed to run - that is the
permission engine's (Phase 15) - and no application name appears in any of them: an
application is resolved from the registry.

Verification is honest (section 18): an operation whose effect cannot be observed is
`INCONCLUSIVE`, never `VERIFIED`. Typed and pasted text is read back from the focused
edit field when the platform can read it (a standard Windows edit field, never a password
field): `VERIFIED` only when the field now holds the text one more time than before,
`FAILED` when the field can be read and the text is not in it, and `INCONCLUSIVE`
otherwise - an unreadable field, the focus having moved, or text that was already there.
Keys, clicks and scrolling have no such evidence and stay `INCONCLUSIVE`.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from app.actions import png
from app.actions.ports import Platform, WindowInfo
from app.actions.results import Attempt, Condition, StepStatus
from app.actions.safety import UnsafeValue, bare_name, confined_path, key_combination, key_name
from app.applications import Application, find
from app.models.enums import RiskLevel

#: Seconds an application launch or a window close is given to show its effect.
LAUNCH_TIMEOUT = 15.0
CLOSE_TIMEOUT = 5.0
#: Seconds typed or pasted text is given to appear in the focused field.
INPUT_TIMEOUT = 3.0
MAX_TEXT = 10_000


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    kind: str
    required: bool = True
    default: str | None = None
    choices: tuple[str, ...] = ()
    low: int | None = None
    high: int | None = None


@dataclass(slots=True)
class Outcome:
    status: StepStatus
    observed: str
    detail: str
    conditions: list[Condition] = field(default_factory=list)
    executed: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    failure_stage: str | None = None
    error: str | None = None


@dataclass(slots=True)
class Context:
    platform: Platform

    def poll(self, check: Callable[[], object], timeout: float) -> object:
        """Inspect state until `check` returns something truthy, or the time is up.

        A simulated platform answers at once; a live one is given `timeout` seconds, in
        quarter-second steps.
        """
        found = check()
        waited = 0.0
        while not found and self.platform.live and waited < timeout:
            self.platform.settle(0.25)
            waited += 0.25
            found = check()
        return found


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    name: str
    params: tuple[Param, ...]
    risk: RiskLevel
    preconditions: tuple[str, ...]
    expected: str
    verification: str
    run: Callable[[Context, dict], Outcome]


# ------------------------------------------------------------------ validation


def validate(definition: ActionDefinition, given: dict[str, str]) -> dict:
    """Validated, normalised parameters, or `UnsafeValue` naming the problem."""
    unknown = sorted(set(given) - {p.name for p in definition.params})
    if unknown:
        raise UnsafeValue(f"{definition.name} takes no parameter {', '.join(unknown)}")
    values: dict = {}
    for param in definition.params:
        raw = given.get(param.name, param.default)
        if raw is None:
            if param.required:
                raise UnsafeValue(f"{definition.name} needs the parameter {param.name}")
            continue
        values[param.name] = _value(param, raw)
    return values


def _value(param: Param, raw: str):
    if param.kind == "application":
        app = find(raw)
        if app is None:
            raise UnsafeValue(f"{raw!r} is not in the application registry")
        return app
    if param.kind == "path":
        path = confined_path(raw)
        if param.name == "path" and param.choices and not path.casefold().endswith(param.choices):
            raise UnsafeValue(f"the path must end with {' or '.join(param.choices)}")
        return path
    if param.kind == "name":
        return bare_name(raw)
    if param.kind == "text":
        if len(raw) > MAX_TEXT:
            raise UnsafeValue(f"the text is longer than {MAX_TEXT} characters")
        return raw
    if param.kind == "key":
        return key_name(raw)
    if param.kind == "keys":
        return key_combination(raw)
    if param.kind == "choice":
        value = raw.strip().casefold()
        if value not in param.choices:
            raise UnsafeValue(f"{param.name} must be one of {', '.join(param.choices)}")
        return value
    if param.kind == "int":
        try:
            number = int(raw)
        except (TypeError, ValueError):
            raise UnsafeValue(f"{param.name} must be a whole number") from None
        if (param.low is not None and number < param.low) or (param.high is not None and number > param.high):
            raise UnsafeValue(f"{param.name} must be between {param.low} and {param.high}")
        return number
    raise UnsafeValue(f"unknown parameter kind {param.kind}")  # pragma: no cover - catalogue error


def shown(values: dict) -> tuple[tuple[str, str], ...]:
    """Parameters as text, in schema order, for plans and reports."""
    out = []
    for name, value in values.items():
        if isinstance(value, Application):
            value = value.name
        elif isinstance(value, tuple):
            value = "+".join(value)
        out.append((name, str(value)))
    return tuple(out)


# -------------------------------------------------------------------- helpers


def _blocked(conditions: list[Condition], detail: str) -> Outcome:
    return Outcome(StepStatus.BLOCKED, "nothing was done", detail, conditions, failure_stage="PRECONDITION")


def _check(conditions: list[Condition], text: str, satisfied: bool, detail: str = "") -> bool:
    conditions.append(Condition(text, satisfied, detail))
    return satisfied


def _window_text(window: WindowInfo) -> str:
    return f"'{window.title}' (process {window.image}, pid {window.pid}, window {window.handle})"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parent(path: str) -> str:
    return str(PureWindowsPath(path).parent)


def _foreground(ctx: Context, conditions: list[Condition]) -> WindowInfo | None:
    window = ctx.platform.foreground()
    _check(conditions, "a window has the keyboard focus", window is not None,
           "" if window is None else _window_text(window))
    return window


# ------------------------------------------------------------- the actions


def open_application(ctx: Context, values: dict) -> Outcome:
    """OPEN_APPLICATION(application): one implementation for every application (section 99)."""
    app: Application = values["application"]
    platform = ctx.platform
    conditions: list[Condition] = []
    usable = []
    for method in app.launch:
        ok, detail = platform.can_launch(method)
        conditions.append(Condition(f"launch method {method.kind.value} {method.value} is available", ok, detail))
        if ok:
            usable.append(method)
    if not usable:
        return _blocked(conditions, f"no registered launch method for {app.name} is available")
    before = platform.windows()
    already = [w for w in before if app.detection.matches(w.image, w.title)]
    known = {w.handle for w in before}
    outcome = Outcome(StepStatus.FAILED, "", "", conditions, executed=True)
    # Recovery (section 115; P14-6): a LOW-risk launch may try one alternative method,
    # and only when the application was not already open.
    for method in usable[: 1 if already else 2]:
        returned = platform.launch(method)
        outcome.attempts.append(Attempt(f"{method.kind.value} {method.value}", returned.detail))
        new = ctx.poll(lambda: [w for w in platform.windows()
                                if w.handle not in known and app.detection.matches(w.image, w.title)],
                       LAUNCH_TIMEOUT)
        if new:
            outcome.status = StepStatus.VERIFIED
            outcome.observed = "new window " + _window_text(new[0])
            outcome.detail = f"{app.name} is open: a new window of it appeared"
            return outcome
    if already:
        present = [w for w in platform.windows() if app.detection.matches(w.image, w.title)]
        if present:
            outcome.status = StepStatus.VERIFIED
            outcome.observed = "window " + _window_text(present[0]) + ", present before the action"
            outcome.detail = f"{app.name} was already open; no new window appeared, and it is open"
            return outcome
    outcome.observed = "no window of the application appeared"
    outcome.detail = (f"{app.name} could not be verified as opened: no matching window appeared after "
                      f"{len(outcome.attempts)} attempt(s) (section 18)")
    outcome.failure_stage = "VERIFICATION"
    return outcome


def close_application(ctx: Context, values: dict) -> Outcome:
    app: Application = values["application"]
    platform = ctx.platform
    conditions: list[Condition] = []
    windows = [w for w in platform.windows() if app.detection.matches(w.image, w.title)]
    if "window" in values:
        target = next((w for w in windows if w.handle == values["window"]), None)
        if not _check(conditions, f"window {values['window']} is a window of {app.name}", target is not None):
            return _blocked(conditions, "the named window is not open")
    elif len(windows) == 1:
        target = windows[0]
        _check(conditions, f"exactly one window of {app.name} is open", True, _window_text(target))
    else:
        listed = "; ".join(_window_text(w) for w in windows) or "none"
        _check(conditions, f"exactly one window of {app.name} is open", False,
               f"{len(windows)} open: {listed}")
        detail = (f"{app.name} has no open window" if not windows else
                  f"{len(windows)} windows of {app.name} are open; name one with window=HANDLE - RUDRA does "
                  "not choose (section 97)")
        return _blocked(conditions, detail)
    closed = platform.close_window(target.handle)
    outcome = Outcome(StepStatus.INCONCLUSIVE, "", "", conditions, executed=True,
                      attempts=[Attempt("close the window (a normal close request)", "sent" if closed else "refused")])
    gone = ctx.poll(lambda: all(w.handle != target.handle for w in platform.windows()), CLOSE_TIMEOUT)
    if gone:
        outcome.status, outcome.observed = StepStatus.VERIFIED, f"window {target.handle} is no longer open"
        outcome.detail = f"{app.name}'s window closed"
    else:
        outcome.observed = f"window {target.handle} is still open"
        outcome.detail = "the window is still open; the application may be asking to save changes"
    return outcome


def open_file(ctx: Context, values: dict) -> Outcome:
    path = values["path"]
    conditions: list[Condition] = []
    if not _check(conditions, "the file exists", ctx.platform.exists(path) and not ctx.platform.is_dir(path), path):
        return _blocked(conditions, f"{path} does not exist")
    known = {w.handle for w in ctx.platform.windows()}
    returned = ctx.platform.open_with_default(path)
    name = PureWindowsPath(path).name.casefold()
    new = ctx.poll(lambda: [w for w in ctx.platform.windows() if w.handle not in known and name in w.title.casefold()],
                   LAUNCH_TIMEOUT)
    outcome = Outcome(StepStatus.INCONCLUSIVE, "no new window titled with the file's name appeared",
                      "opened with the default application, but no window showing it could be identified",
                      conditions, executed=True, attempts=[Attempt("open with the default application", returned.detail)])
    if new:
        outcome.status, outcome.observed = StepStatus.VERIFIED, "new window " + _window_text(new[0])
        outcome.detail = "a window showing the file appeared"
    return outcome


def create_file(ctx: Context, values: dict) -> Outcome:
    path, content = values["path"], values.get("content", "").encode("utf-8")
    conditions: list[Condition] = []
    ok = _check(conditions, "the parent folder exists", ctx.platform.is_dir(_parent(path)), _parent(path))
    ok = _check(conditions, "nothing exists at the path (never overwritten)", not ctx.platform.exists(path), path) and ok
    if not ok:
        return _blocked(conditions, "a precondition failed")
    ctx.platform.write_new(path, content)
    found = ctx.platform.read_bytes(path) if ctx.platform.exists(path) else None
    status = StepStatus.VERIFIED if found == content else StepStatus.FAILED
    return Outcome(status, f"{path} exists with {len(found)} bytes" if found is not None else f"{path} does not exist",
                   "the file exists with exactly the content written" if status is StepStatus.VERIFIED
                   else "the file is missing or its content differs", conditions, executed=True,
                   attempts=[Attempt("write a new file", f"{len(content)} bytes")],
                   failure_stage=None if status is StepStatus.VERIFIED else "VERIFICATION")


def save_file(ctx: Context, values: dict) -> Outcome:
    path = values.get("path")
    conditions: list[Condition] = []
    window = _foreground(ctx, conditions)
    if path is not None:
        _check(conditions, "the parent folder exists", ctx.platform.is_dir(_parent(path)), _parent(path))
    if not all(c.satisfied for c in conditions):
        return _blocked(conditions, "a precondition failed")
    before = ctx.platform.modified(path) if path else None
    ctx.platform.send_keys(("ctrl", "s"))
    outcome = Outcome(StepStatus.INCONCLUSIVE, f"ctrl+s sent to {_window_text(window)}",
                      "no path was given, so the save cannot be checked", conditions, executed=True,
                      attempts=[Attempt("ctrl+s", "sent")])
    if path:
        after = ctx.poll(lambda: ctx.platform.modified(path) not in (None, before), CLOSE_TIMEOUT)
        if after:
            outcome.status, outcome.detail = StepStatus.VERIFIED, f"{path} exists and was written by the save"
        elif not ctx.platform.exists(path):
            outcome.status, outcome.detail, outcome.failure_stage = StepStatus.FAILED, f"{path} does not exist", "VERIFICATION"
        else:
            outcome.detail = f"{path} exists but was not modified by the save (it may have had no changes)"
    return outcome


def _transfer(ctx: Context, source: str, destination: str, move: bool) -> Outcome:
    platform = ctx.platform
    conditions: list[Condition] = []
    ok = _check(conditions, "the source file exists", platform.exists(source) and not platform.is_dir(source), source)
    ok = _check(conditions, "the destination's folder exists", platform.is_dir(_parent(destination)),
                _parent(destination)) and ok
    ok = _check(conditions, "nothing exists at the destination (never overwritten)", not platform.exists(destination),
                destination) and ok
    if not ok:
        return _blocked(conditions, "a precondition failed")
    digest = _digest(platform.read_bytes(source))
    (platform.move_new if move else platform.copy_new)(source, destination)
    arrived = platform.exists(destination) and _digest(platform.read_bytes(destination)) == digest
    left = not platform.exists(source)
    good = arrived and (left if move else True)
    observed = (f"{destination} exists with the same SHA-256" if arrived else f"{destination} is missing or differs") \
        + ("" if not move else ("; the source is gone" if left else "; the source is still there"))
    return Outcome(StepStatus.VERIFIED if good else StepStatus.FAILED, observed,
                   "done, and checked by content hash" if good else "the result does not match",
                   conditions, executed=True, attempts=[Attempt("move" if move else "copy", "done")],
                   failure_stage=None if good else "VERIFICATION")


def copy_file(ctx: Context, values: dict) -> Outcome:
    return _transfer(ctx, values["source"], values["destination"], move=False)


def move_file(ctx: Context, values: dict) -> Outcome:
    return _transfer(ctx, values["source"], values["destination"], move=True)


def rename_file(ctx: Context, values: dict) -> Outcome:
    source = values["source"]
    return _transfer(ctx, source, str(PureWindowsPath(source).with_name(values["new_name"])), move=True)


def create_folder(ctx: Context, values: dict) -> Outcome:
    path = values["path"]
    conditions: list[Condition] = []
    ok = _check(conditions, "the parent folder exists", ctx.platform.is_dir(_parent(path)), _parent(path))
    ok = _check(conditions, "nothing exists at the path", not ctx.platform.exists(path), path) and ok
    if not ok:
        return _blocked(conditions, "a precondition failed")
    ctx.platform.make_dir(path)
    made = ctx.platform.is_dir(path)
    return Outcome(StepStatus.VERIFIED if made else StepStatus.FAILED,
                   f"{path} is a folder" if made else f"{path} is not a folder", "checked on disk" if made else "not created",
                   conditions, executed=True, attempts=[Attempt("make the folder", "done")],
                   failure_stage=None if made else "VERIFICATION")


def _input(ctx: Context, what: str, perform: Callable[[], None], note: str) -> Outcome:
    conditions: list[Condition] = []
    window = _foreground(ctx, conditions)
    if window is None:
        return _blocked(conditions, "no window would receive the input")
    perform()
    return Outcome(StepStatus.INCONCLUSIVE, f"{what} sent to {_window_text(window)}", note, conditions,
                   executed=True, attempts=[Attempt(what, "sent")])


_UNREADABLE = "its effect cannot be read back without UI inspection, so it is not reported as verified"
_UNREADABLE_FIELD = ("the focused control is not a standard edit field whose text can be read back (or it is a "
                     "password field), so the text is not reported as verified")


def _lines(text: str) -> str:
    """Line breaks as one character: an edit field stores the Enter key as CR LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _entered(ctx: Context, what: str, perform: Callable[[], None], expected: Callable[[], str | None]) -> Outcome:
    """Text sent to the focused field, then read back from it (the field's text is never reported)."""
    conditions: list[Condition] = []
    window = _foreground(ctx, conditions)
    if window is None:
        return _blocked(conditions, "no window would receive the input")
    wanted = _lines(expected() or "")
    before = ctx.platform.focused_text()
    perform()
    outcome = Outcome(StepStatus.INCONCLUSIVE, f"{what} sent to {_window_text(window)}", _UNREADABLE_FIELD,
                      conditions, executed=True, attempts=[Attempt(what, "sent")])
    if before is None:
        return outcome
    if not wanted:
        outcome.detail = "there was no text to look for, so the effect is not reported as verified"
        return outcome
    had = _lines(before.text).count(wanted)

    def arrived() -> bool:
        now = ctx.platform.focused_text()
        return now is not None and now.handle == before.handle and _lines(now.text).count(wanted) > had

    found = ctx.poll(arrived, INPUT_TIMEOUT)
    now = ctx.platform.focused_text()
    field = f"the focused {before.class_name} field"
    if found:
        outcome.status = StepStatus.VERIFIED
        outcome.observed = f"{field} was read back and now holds the {len(wanted)} characters"
        outcome.detail = "the text was read back from the focused edit field"
    elif now is None or now.handle != before.handle:
        outcome.observed = f"{what} sent; the keyboard focus moved away from {field}"
        outcome.detail = "the focus moved to another control, so the field could not be checked"
    elif wanted in _lines(now.text):
        outcome.observed = f"{field} holds the text, but it held it before as well"
        outcome.detail = "the text was already in the field, so this action's effect cannot be told apart"
    else:
        outcome.status, outcome.failure_stage = StepStatus.FAILED, "VERIFICATION"
        outcome.observed = f"{field} was read back and does not hold the {len(wanted)} characters"
        outcome.detail = "the field was read back after the input and the text is not in it"
    return outcome


def type_text(ctx: Context, values: dict) -> Outcome:
    text = values["text"]
    return _entered(ctx, f"{len(text)} characters", lambda: ctx.platform.send_text(text), lambda: text)


def press_key(ctx: Context, values: dict) -> Outcome:
    return _input(ctx, f"the key {values['key']}", lambda: ctx.platform.send_keys((values["key"],)), _UNREADABLE)


def hotkey(ctx: Context, values: dict) -> Outcome:
    keys = values["keys"]
    return _input(ctx, "the keys " + "+".join(keys), lambda: ctx.platform.send_keys(keys), _UNREADABLE)


def _pointer(button: str, count: int):
    def act(ctx: Context, values: dict) -> Outcome:
        width, height = ctx.platform.screen_size()
        x, y = values["x"], values["y"]
        conditions: list[Condition] = []
        if not _check(conditions, "the point is on the screen", 0 <= x < width and 0 <= y < height,
                      f"({x}, {y}) on a {width}x{height} screen"):
            return _blocked(conditions, "the point is off the screen")
        ctx.platform.click(x, y, button, count)
        return Outcome(StepStatus.INCONCLUSIVE, f"{button} click x{count} at ({x}, {y})", _UNREADABLE, conditions,
                       executed=True, attempts=[Attempt(f"{button} click x{count}", "sent")])
    return act


def scroll(ctx: Context, values: dict) -> Outcome:
    return _input(ctx, f"scroll {values['direction']} {values['amount']}",
                  lambda: ctx.platform.scroll(values["direction"], values["amount"]), _UNREADABLE)


def copy_selection(ctx: Context, values: dict) -> Outcome:
    conditions: list[Condition] = []
    window = _foreground(ctx, conditions)
    if window is None:
        return _blocked(conditions, "no window has a selection to copy")
    before = ctx.platform.clipboard_text()
    ctx.platform.send_keys(("ctrl", "c"))
    after = ctx.poll(lambda: ctx.platform.clipboard_text() != before and ctx.platform.clipboard_text(), CLOSE_TIMEOUT)
    outcome = Outcome(StepStatus.INCONCLUSIVE, "the clipboard did not change",
                      "nothing new was copied, or the same text was copied again", conditions, executed=True,
                      attempts=[Attempt("ctrl+c", "sent")])
    if after:
        outcome.status = StepStatus.VERIFIED
        outcome.observed = f"the clipboard now holds {len(ctx.platform.clipboard_text() or '')} characters"
        outcome.detail = "the clipboard changed"
    return outcome


def paste(ctx: Context, values: dict) -> Outcome:
    return _entered(ctx, "ctrl+v", lambda: ctx.platform.send_keys(("ctrl", "v")), ctx.platform.clipboard_text)


def take_screenshot(ctx: Context, values: dict) -> Outcome:
    path = values["path"]
    conditions: list[Condition] = []
    ok = _check(conditions, "the parent folder exists", ctx.platform.is_dir(_parent(path)), _parent(path))
    ok = _check(conditions, "nothing exists at the path (never overwritten)", not ctx.platform.exists(path), path) and ok
    if not ok:
        return _blocked(conditions, "a precondition failed")
    size = ctx.platform.screenshot(path)
    data = ctx.platform.read_bytes(path) if ctx.platform.exists(path) else b""
    found = png.dimensions(data)
    good = found is not None and found == tuple(size)
    return Outcome(StepStatus.VERIFIED if good else StepStatus.FAILED,
                   f"{path} is a {found[0]}x{found[1]} PNG" if found else f"{path} is not a PNG image",
                   "the image file exists with the screen's size" if good else "the image is missing or the wrong size",
                   conditions, executed=True, attempts=[Attempt("capture the screen", f"{size[0]}x{size[1]}")],
                   failure_stage=None if good else "VERIFICATION")


# ------------------------------------------------------------------ the catalogue

_APP = Param("application", "application")
_PATH = Param("path", "path")
_XY = (Param("x", "int", low=0, high=100_000), Param("y", "int", low=0, high=100_000))
_WINDOW_PRESENT = "a window of the application is present (new, or reported as already open)"
_INPUT = ("a window has the keyboard focus",)

CATALOGUE: dict[str, ActionDefinition] = {d.name: d for d in (
    ActionDefinition("OPEN_APPLICATION", (_APP,), RiskLevel.LOW,
                     ("the application is registered", "a registered launch method is available"),
                     _WINDOW_PRESENT, "inspect top-level windows for the registry's process image and title",
                     open_application),
    ActionDefinition("CLOSE_APPLICATION", (_APP, Param("window", "int", required=False, low=1, high=2**63)),
                     RiskLevel.MEDIUM, ("exactly one window of the application is open, or one is named",),
                     "the window is no longer open", "inspect top-level windows", close_application),
    ActionDefinition("OPEN_FILE", (_PATH,), RiskLevel.LOW, ("the file exists",),
                     "a window showing the file appears", "inspect top-level windows for the file's name", open_file),
    ActionDefinition("CREATE_FILE", (_PATH, Param("content", "text", required=False, default="")), RiskLevel.LOW,
                     ("the parent folder exists", "nothing exists at the path"),
                     "the file exists with exactly the content written", "read the file back", create_file),
    ActionDefinition("SAVE_FILE", (Param("path", "path", required=False),), RiskLevel.MEDIUM,
                     ("a window has the keyboard focus", "the parent folder exists (with a path)"),
                     "the file at the path exists and was written by the save",
                     "compare the file's modification time before and after", save_file),
    ActionDefinition("MOVE_FILE", (Param("source", "path"), Param("destination", "path")), RiskLevel.MEDIUM,
                     ("the source exists", "the destination's folder exists", "nothing exists at the destination"),
                     "the destination holds the source's content and the source is gone",
                     "compare SHA-256 and check the source", move_file),
    ActionDefinition("COPY_FILE", (Param("source", "path"), Param("destination", "path")), RiskLevel.LOW,
                     ("the source exists", "the destination's folder exists", "nothing exists at the destination"),
                     "the destination holds the source's content", "compare SHA-256", copy_file),
    ActionDefinition("RENAME_FILE", (Param("source", "path"), Param("new_name", "name")), RiskLevel.MEDIUM,
                     ("the source exists", "nothing exists under the new name"),
                     "the file exists under the new name and not the old", "compare SHA-256 and check the old name",
                     rename_file),
    ActionDefinition("CREATE_FOLDER", (_PATH,), RiskLevel.LOW, ("the parent folder exists", "nothing exists at the path"),
                     "the folder exists", "check the path is a folder", create_folder),
    ActionDefinition("TYPE_TEXT", (Param("text", "text"),), RiskLevel.LOW, _INPUT,
                     "the text is typed into the focused window",
                     "read the focused edit field back when it is a standard, non-password one", type_text),
    ActionDefinition("PRESS_KEY", (Param("key", "key"),), RiskLevel.MEDIUM, _INPUT,
                     "the key is pressed in the focused window", "not observable without UI inspection", press_key),
    ActionDefinition("HOTKEY", (Param("keys", "keys"),), RiskLevel.MEDIUM, _INPUT,
                     "the combination is pressed in the focused window", "not observable without UI inspection", hotkey),
    ActionDefinition("CLICK", _XY, RiskLevel.MEDIUM, ("the point is on the screen",),
                     "a left click at the point", "not observable without UI inspection", _pointer("left", 1)),
    ActionDefinition("DOUBLE_CLICK", _XY, RiskLevel.MEDIUM, ("the point is on the screen",),
                     "a double click at the point", "not observable without UI inspection", _pointer("left", 2)),
    ActionDefinition("RIGHT_CLICK", _XY, RiskLevel.MEDIUM, ("the point is on the screen",),
                     "a right click at the point", "not observable without UI inspection", _pointer("right", 1)),
    ActionDefinition("SCROLL", (Param("direction", "choice", choices=("up", "down")),
                                Param("amount", "int", required=False, default="3", low=1, high=50)),
                     RiskLevel.MEDIUM, _INPUT, "the focused window scrolls", "not observable without UI inspection", scroll),
    ActionDefinition("COPY", (), RiskLevel.MEDIUM, _INPUT, "the selection is on the clipboard",
                     "compare the clipboard before and after", copy_selection),
    ActionDefinition("PASTE", (), RiskLevel.MEDIUM, _INPUT, "the clipboard is pasted into the focused window",
                     "read the focused edit field back for the clipboard's text, when it can be read", paste),
    ActionDefinition("TAKE_SCREENSHOT", (Param("path", "path", choices=(".png",)),), RiskLevel.LOW,
                     ("the parent folder exists", "nothing exists at the path"),
                     "a PNG of the screen's size exists at the path", "read the image header back", take_screenshot),
)}
