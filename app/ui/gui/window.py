"""RUDRA's desktop window (ADR 0057): a compact assistant that expands to the full interface.

Master specification section 139: RUDRA starts as a small assistant window - a question
or quick command, status, short responses - and becomes the full interface only when the
user asks for it. Section 140: the window is an interface to the architecture and must
not become the architecture. Everything the window runs is a command line, run by
`commands.run` through the command line's own entry point on a worker thread. This module
only lays out the forms, shows the results and puts writes and actions to the user first.
"""

from __future__ import annotations

import contextlib
import ctypes
import math
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.core.config import load_config
from app.core.paths import PathLayout
from app.storage.archive import (
    SUFFIX,
    BackupError,
    ExportReport,
    RestoreReport,
    export_knowledge,
    inspect_backup,
    knowledge_base_exists,
    restore_knowledge,
)
from app.providers import PROVIDERS, AiSettings, ProviderError, explain, interpret_question, list_models, send
from app.providers import credentials as ai_credentials
from app.providers import settings as ai_settings
from app.updates import UpdateChecker, UpdateInfo, UpdateStatus

from app.ui.gui import answerview, commands, mathrender, theme
from app.ui.gui import worked as worked_solution
from app.ui.gui.commands import Approval, CommandResult, FormError
from app.version import VERSION

MARK_SIZES = (32, 48, 64, 96, 128, 256)
POLL_MS = 40


@dataclass(frozen=True)
class VoiceResult:
    """One dictation attempt: the recognized text, or why there is none.

    `error` is `""` when the user cancelled (nothing is shown for that - it was their own
    choice), `None` when `text` is the whole story, and a message otherwise. `uncertain`
    means the recognizer was unsure of its own words: they are shown for checking.
    """

    text: str
    error: str | None
    uncertain: bool = False


@dataclass(frozen=True)
class BatchImportResult:
    """One independent result per selected document plus the shared index/inventory."""

    documents: tuple[tuple[Path, CommandResult], ...]
    shared: tuple[CommandResult, ...]


def window_icons(root: tk.Tk, ico: Path) -> tuple[int, ...]:
    """Windows: give the window the R icon at the system's small and large sizes.

    Tk's `wm iconbitmap -default` and `wm iconphoto` leave this Tk's window without a
    usable icon, so the title bar, taskbar and Alt+Tab would show a generic one. The icons
    are loaded from the .ico and set with WM_SETICON; the handles are returned so the
    caller keeps them for the window's life. Empty when not on Windows or on failure.
    """
    if sys.platform != "win32":
        return ()
    root.update_idletasks()
    try:
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_uint]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_void_p
        hwnd = int(root.frame(), 16)
    except (AttributeError, OSError, ValueError, tk.TclError):
        return ()
    handles = []
    # ICON_SMALL at SM_CXSMICON, ICON_BIG at SM_CXICON; IMAGE_ICON, LR_LOADFROMFILE.
    for kind, metric in ((0, 49), (1, 11)):
        size = user32.GetSystemMetrics(metric)
        handle = user32.LoadImageW(None, str(ico), 1, size, size, 0x10)
        if not handle:
            return ()
        user32.SendMessageW(hwnd, 0x80, kind, handle)  # WM_SETICON
        handles.append(handle)
    small = user32.SendMessageW(hwnd, 0x7F, 0, None)  # WM_GETICON, read back
    big = user32.SendMessageW(hwnd, 0x7F, 1, None)
    return tuple(handles) if small and big else ()


def dark_title_bar(root: tk.Tk) -> bool:
    """Windows 11: a dark title bar in the window's own colours (best effort; False elsewhere)."""
    if sys.platform != "win32":
        return False
    root.update_idletasks()
    try:
        hwnd = int(root.frame(), 16)
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    except (AttributeError, OSError, ValueError, tk.TclError):
        return False

    def colorref(color: str) -> int:
        red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        return blue << 16 | green << 8 | red

    applied = False
    # DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR
    for attribute, value in ((20, 1), (34, colorref(theme.LINE)), (35, colorref(theme.SURFACE)),
                             (36, colorref(theme.TEXT))):
        data = ctypes.c_int(value)
        applied |= set_attribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0
    return applied


def work_area(root: tk.Misc) -> tuple[int, int, int, int]:
    """The desktop without the taskbar, in screen pixels: left, top, right, bottom."""
    if sys.platform == "win32":
        rect = (ctypes.c_long * 4)()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect[0], rect[1], rect[2], rect[3]
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - 48


def _work(argv: tuple[str, ...], project_root: Path, results: queue.Queue[CommandResult]) -> None:
    """The worker thread: run one command line, hand the result to the window's thread.

    It is given nothing of the window's: Tk may only be used from the thread that
    created it, and a worker holding the window could end up releasing its Tk objects.
    """
    try:
        result = commands.run(argv, project_root)
    except Exception:  # noqa: BLE001 - the command line never raises; if it did, show it
        result = CommandResult(argv, 70, "", traceback.format_exc(), 0.0)
    results.put(result)


class RudraWindow:
    """The one top-level window, its two layouts and the command runner they share."""

    def __init__(self, root: tk.Tk, project_root: Path, *, full: bool = False, autostart: bool = True):
        self.root = root
        self.project_root = Path(project_root)
        self.fonts = theme.apply(root)
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        self.results: queue.Queue[CommandResult] = queue.Queue()
        self.busy = False
        self.last: CommandResult | None = None
        self.on_result: Callable[[CommandResult], None] | None = None
        self._on_done: Callable[[CommandResult], None] | None = None
        self.confirm: Callable[[Approval], bool] = self._confirm
        self.act = tk.BooleanVar(master=root, value=False)
        self.confirm_medium = tk.BooleanVar(master=root, value=False)
        self.run_controls: list[ttk.Widget] = []
        self.images: dict[int, tk.PhotoImage] = {}
        self.tasks: queue.Queue[tuple[Callable, object, BaseException | None]] = queue.Queue()
        self.updates: queue.Queue[tuple[UpdateInfo | None, bool]] = queue.Queue()
        self.update_info: UpdateInfo | None = None
        self.pending_note: str | None = None
        self.pending_import_name: str | None = None
        self.open_url: Callable[[str], object] = webbrowser.open
        self.open_path: Callable[[Path], object] = lambda path: os.startfile(path)  # noqa: S606 - the user asked
        self._pulse_on = False
        self._polls = 0
        root.title("RUDRA")
        self.icon_set = self._set_icon()
        self.dark_title = dark_title_bar(root)
        root.report_callback_exception = self._callback_failed
        self.compact = CompactView(self)
        self.full = FullView(self)
        self.full_shown: bool | None = None
        self.show(full=full)
        self.set_state("READY", theme.OK)
        #: Something other than the welcome text has been shown in the output, so a late
        #: welcome must not replace it.
        self.output_touched = False
        if autostart:
            root.after(250, self._start)
            root.after(4000, self.check_for_updates)

    # -------------------------------------------------------------- sizes and images

    def px(self, logical: float) -> int:
        return round(logical * self.scale)

    def mark(self, logical: int) -> tk.PhotoImage | None:
        """The R mark, from the PNG nearest above `logical` pixels at this display's scale."""
        wanted = self.px(logical)
        size = min((s for s in MARK_SIZES if s >= wanted), default=MARK_SIZES[-1])
        if size not in self.images:
            try:
                self.images[size] = tk.PhotoImage(master=self.root, file=str(commands.asset(f"rudra-{size}.png")))
            except tk.TclError:
                return None
        return self.images[size]

    def _set_icon(self) -> bool:
        """The R icon on the title bar, taskbar and Alt+Tab; True once the window holds it."""
        if sys.platform == "win32":
            self.icon_handles = window_icons(self.root, commands.asset("rudra.ico"))
            return bool(self.icon_handles)
        try:
            photos = [image for image in (self.mark(256), self.mark(48), self.mark(32)) if image]
            self.root.iconphoto(True, *photos)
            return bool(photos)
        except tk.TclError:
            return False

    # -------------------------------------------------------------- layouts

    def show(self, *, full: bool) -> None:
        """Switch between the compact assistant and the full interface."""
        if self.full_shown is full:
            return
        self.full_shown = full
        (self.compact.frame if full else self.full.frame).pack_forget()
        (self.full.frame if full else self.compact.frame).pack(fill="both", expand=True)
        left, top, right, bottom = work_area(self.root)
        width = min(self.px(1200 if full else 440), right - left)
        height = min(self.px(780 if full else 470), bottom - top)
        self.root.minsize(self.px(900 if full else 380), self.px(600 if full else 380))
        if full:
            x, y = left + (right - left - width) // 2, top + (bottom - top - height) // 2
        else:
            x, y = right - width - self.px(24), bottom - height - self.px(24)
        self.root.geometry(f"{width}x{height}+{max(left, x)}+{max(top, y)}")
        (self.full if full else self.compact).focus()

    def set_project(self, root: Path) -> None:
        self.project_root = Path(root)
        self.full.show_project()
        self.compact.show_project()

    # -------------------------------------------------------------- starting up

    def _start(self) -> None:
        """Start RUDRA in the background; the person is told what it holds, not shown a report."""
        self.submit(commands.status("start"), on_done=self._started)

    def _started(self, result: CommandResult) -> None:
        if not result.ok:
            self.show_note(commands.startup_summary(result), tag="warn")
            self.set_state("NOT READY", theme.WARN)
            return
        self.attention = commands.startup_attention(result)
        self.refresh_welcome()

    def refresh_welcome(self) -> None:
        """Say what the knowledge base holds, in the output, until something else is shown."""
        def done(result: CommandResult) -> None:
            if self.output_touched:
                return
            summary = commands.welcome_facts(result)
            self.compact.output.show_welcome(summary, self.attention)
            self.full.output.show_welcome(summary, self.attention)

        if not self.busy:
            self.submit(commands.inventory(), on_done=done)

    attention: tuple[str, ...] = ()
    #: What the window is busy with (for diagnostics): the command line, or the task's name.
    running: tuple[str, ...] = ()

    # -------------------------------------------------------------- running commands

    def run_form(self, build: Callable[[], list[str]], *, approve: bool = False) -> bool:
        """Build a command line from a form and run it. A form error is shown, not raised."""
        try:
            argv = build()
        except FormError as exc:
            self.show_note(str(exc), tag="warn")
            self.set_state("NOT RUN", theme.WARN)
            return False
        return self.submit(argv, approval=commands.approval(argv, self.project_root) if approve else None)

    def submit(self, argv: list[str], *, approval: Approval | None = None,
               on_done: Callable[[CommandResult], None] | None = None) -> bool:
        """Run one command line on a worker thread; False when busy or not approved.

        With `on_done`, the result goes to that callback instead of the output views (the
        answer view uses it to trace a source without replacing the answer).
        """
        if self.busy:
            return False
        if approval is not None and not self.confirm(approval):
            self.show_note("Cancelled. Nothing was run.", tag="meta")
            self.set_state("CANCELLED", theme.MUTED)
            return False
        self.busy = True
        self._on_done = on_done
        for control in self.run_controls:
            control.state(["disabled"])
        self.set_state(f"RUNNING · {argv[0] if argv else 'start'}", theme.ACCENT)
        self.running = tuple(argv)
        if on_done is None:
            self.compact.show_running(argv)
            self.full.show_running(argv)
        threading.Thread(target=_work, args=(tuple(argv), self.project_root, self.results), daemon=True).start()
        self.root.after(POLL_MS, self._poll)
        return True

    def _poll(self) -> None:
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            self._polls += 1
            if self._polls % 10 == 0:
                self._pulse_on = not self._pulse_on
                self._set_dot(theme.ACCENT_SOFT if self._pulse_on else theme.ACCENT_DEEP)
            self.root.after(POLL_MS, self._poll)
            return
        self.busy = False
        self.last = result
        for control in self.run_controls:
            control.state(["!disabled"])
        on_done, self._on_done = self._on_done, None
        if on_done is not None:
            self.set_state("READY", theme.OK)
            on_done(result)
            return
        self.output_touched = True
        self.compact.show_result(result)
        self.full.show_result(result)
        self.pending_import_name = None
        if commands.is_friendly_command(result.argv):
            text, good = commands.finished_state(result)
            self.set_state(text, theme.OK if good else theme.WARN)
        elif result.ok:
            self.set_state(f"READY · {result.argv[0] if result.argv else 'start'} done", theme.OK)
        else:
            self.set_state(f"EXIT {result.exit_code} · {result.meaning}", theme.WARN)
        if self.on_result is not None:
            self.on_result(result)

    def submit_sequence(self, argvs: list[list[str]], *, approval: Approval | None = None, label: str,
                        on_done: Callable[[list[CommandResult]], None], show: list[str] | None = None) -> bool:
        """Run one or more command lines in order, on one worker thread, as a single
        operation (one approval, one busy state). Stops at the first command that does
        not exit 0; `on_done` always receives every result obtained, in order.

        Unlike `submit`, no output view is updated automatically: a sequence's own
        meaning (Add Document: `extract` then `index`, so every query mode is ready
        immediately) is for the caller to render as one result. `window.on_result`, when
        set, still fires once, with the first command's result - what a plain `submit`
        of that command alone would have reported.
        """
        if self.busy:
            return False
        if approval is not None and not self.confirm(approval):
            self.show_note("Cancelled. Nothing was run.", tag="meta")
            self.set_state("CANCELLED", theme.MUTED)
            return False

        if show is not None:
            self.compact.show_running(show)
            self.full.show_running(show)

        def work() -> list[CommandResult]:
            results: list[CommandResult] = []
            for argv in argvs:
                result = commands.run(argv, self.project_root)
                results.append(result)
                if not result.ok:
                    break
            return results

        def done(results: object, error: BaseException | None) -> None:
            if error is not None:
                self.show_note("RUDRA hit an unexpected error partway through.\n\n" + str(error), tag="err")
                return
            assert isinstance(results, list)
            self.last = results[-1]
            on_done(results)
            if self.on_result is not None:
                self.on_result(results[0])

        return self.run_task(label, work, done)

    # -------------------------------------------------------------- work that is not a command

    def run_task(self, label: str, work: Callable[[], object], done: Callable[[object, BaseException | None], None]
                 ) -> bool:
        """Run `work` on a worker thread while no command runs; `done(result, error)` on this thread."""
        if self.busy:
            return False
        self.busy = True
        for control in self.run_controls:
            control.state(["disabled"])
        self.set_state(f"RUNNING · {label}", theme.ACCENT)
        self.running = (label,)

        def worker() -> None:
            try:
                self.tasks.put((done, work(), None))
            except Exception as exc:  # noqa: BLE001 - handed to the window, which shows it
                self.tasks.put((done, None, exc))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(POLL_MS, self._poll_task)
        return True

    def _poll_task(self) -> None:
        try:
            done, result, error = self.tasks.get_nowait()
        except queue.Empty:
            self.root.after(POLL_MS, self._poll_task)
            return
        self.busy = False
        for control in self.run_controls:
            control.state(["!disabled"])
        self.set_state("READY" if error is None else "NOT DONE", theme.OK if error is None else theme.WARN)
        done(result, error)

    def open_source(self, stored_name: str) -> Path | None:
        """Open a document's preserved copy for reading - as a temporary copy, so nothing done in
        the viewer can change the file RUDRA's provenance checks rely on."""
        source = self.layout().documents_dir / Path(stored_name).name
        if not source.is_file():
            self.show_note(f"RUDRA's copy of this document is not present ({source.name}).", tag="warn")
            return None
        import contextlib
        import shutil
        import stat
        import tempfile

        folder = Path(tempfile.gettempdir()) / "RUDRA-source-view"
        folder.mkdir(parents=True, exist_ok=True)
        copy = folder / source.name
        if copy.exists():
            copy.chmod(stat.S_IWRITE | stat.S_IREAD)  # an earlier view left it read-only
        shutil.copyfile(source, copy)
        with contextlib.suppress(OSError):  # read-only is a courtesy to the viewer, not a safeguard
            copy.chmod(stat.S_IREAD)
        self.open_path(copy)
        return copy

    # -------------------------------------------------------------- optional AI assistance

    def ai(self) -> AiSettings:
        return ai_settings.load_settings(self.project_root / "config")

    def ai_active(self) -> bool:
        settings = self.ai()
        return settings.active and settings.provider in PROVIDERS

    def ai_sender(self):
        """(system, user) -> reply, with the user's own key read from the Credential Manager."""
        settings = self.ai()
        provider = PROVIDERS[settings.provider]
        key = ai_credentials.read_key(provider.key)
        if not key:
            raise ProviderError(f"No API key is stored for {provider.name}. Add it on the AI page.")
        return lambda system, user: self.ai_transport_send(provider, key, settings.model, system, user)

    def ai_transport_send(self, provider, key, model, system, user) -> str:
        return send(provider, key, model, system, user)

    def ai_explain(self, view: "OutputView", part: answerview.AnswerPart, question: str) -> bool:
        """Word an explanation of one answer's statements with the user's chosen provider."""
        if not self.ai_active():
            return False
        statements = part.statements()
        provider = PROVIDERS[self.ai().provider].name

        def work():
            return explain(question, statements, self.ai_sender())

        def done(result, error) -> None:
            if error is not None:
                view.explanations[part.number] = str(error)
            else:
                view.explanations[part.number] = result
            view.explained_by = provider
            view.rerender()

        return self.run_task(f"asking {provider}", work, done)

    def ai_interpret(self, question: str) -> bool:
        """Let the provider turn an unrecognised question into one of RUDRA's own; then ask RUDRA."""
        if not self.ai_active():
            return False
        provider = PROVIDERS[self.ai().provider].name

        def work():
            return interpret_question(question, self.ai_sender())

        def done(result, error) -> None:
            if error is not None:
                self.show_note(f"{provider} could not be used: {error}\nNothing was sent to RUDRA's knowledge; "
                               "ask again in other words.", tag="warn")
                return
            if not result.ok:
                self.show_note(f"{provider} could not turn this into a question for your documents: {result.reason}",
                               tag="warn")
                return
            self.pending_note = f"Interpreted by {provider} as: {result.local_question}"
            self.submit(commands.ask(result.local_question, act=False))

        return self.run_task(f"asking {provider}", work, done)

    # -------------------------------------------------------------- voice input (dictation)

    def listen(self, done: Callable[[VoiceResult], None], *, seconds: int = 12,
               on_phase: Callable[[str], None] | None = None) -> Callable[[], None]:
        """Capture one spoken utterance and hand its text (or a friendly reason it has none)
        to `done`, on this thread. Returns a `stop()` the caller may invoke: the first call,
        while RUDRA is still listening, ends the listening and recognizes what was said; a call
        after that cancels and discards. Calling it after `done` has already run does nothing.
        `on_phase("listening" | "recognizing")` is called on this thread as the work moves on.

        Nothing is carried out and nothing is kept: this is dictation into a text field, the
        same question or command the user could have typed (master specification: a user can
        edit the recognized words before anything is sent to RUDRA).
        """
        from app.voice import SpeechCancelled, SpeechUnavailable, listen as voice_listen, words as voice_words

        hints = voice_words.load(self.project_root / "config")
        cancel, finish = threading.Event(), threading.Event()
        state = {"phase": "listening", "finished": False}

        def work() -> VoiceResult:
            try:
                transcript = voice_listen(seconds, cancel=cancel, finish=finish, hints=hints,
                                          on_phase=lambda phase: state.update(phase=phase))
            except SpeechCancelled:
                return VoiceResult(text="", error="")
            except SpeechUnavailable as exc:
                return VoiceResult(text="", error=str(exc))
            if not transcript.text:
                return VoiceResult(text="", error="RUDRA did not hear anything. Try again and speak soon after "
                                                   "pressing Speak.")
            return VoiceResult(text=transcript.text, error=None, uncertain=transcript.uncertain)

        def watch() -> None:
            if not state["finished"]:
                if on_phase is not None:
                    on_phase(str(state["phase"]))
                self.root.after(150, watch)

        def finished(result: object, error: BaseException | None) -> None:
            state["finished"] = True
            if error is not None:
                done(VoiceResult(text="", error=f"RUDRA could not use the microphone: {error}"))
                return
            assert isinstance(result, VoiceResult)
            done(result)

        def stop() -> None:
            if state["phase"] == "listening" and not finish.is_set():
                finish.set()
            else:
                cancel.set()

        if self.run_task("listening", work, finished):
            watch()
        return stop

    def layout(self) -> PathLayout:
        """This project's folders, as the command line resolves them."""
        loaded = load_config(project_root=self.project_root)
        return PathLayout.from_config(self.project_root, loaded.config)

    # -------------------------------------------------------------- updates

    def update_checker(self) -> UpdateChecker:
        return UpdateChecker(self.project_root / "config" / "updates.json")

    def check_for_updates(self, *, manual: bool = False) -> None:
        """Ask GitHub about a newer release on a background thread; offline, nothing happens."""
        checker = self.update_checker()
        if not manual:
            cached = checker.cached()
            if cached is not None and cached.available:
                self.show_update(cached)
            if not checker.due():
                return

        def worker() -> None:
            try:
                info = checker.check(manual=manual)
            except Exception:  # noqa: BLE001 - an update check must never disturb the window
                info = None
            self.updates.put((info, manual))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(200, self._poll_updates)

    def _poll_updates(self) -> None:
        try:
            info, manual = self.updates.get_nowait()
        except queue.Empty:
            self.root.after(200, self._poll_updates)
            return
        if info is not None and info.available:
            self.show_update(info)
        if manual:
            self.report_update(info)

    def show_update(self, info: UpdateInfo) -> None:
        self.update_info = info
        self.compact.banner.show(info)
        self.full.banner.show(info)

    def report_update(self, info: UpdateInfo | None) -> None:
        if info is not None and info.available:
            if messagebox.askyesno("Update available", f"{info.message()}\n\nOpen the download page?",
                                   parent=self.root):
                self.open_url(info.url)
            return
        message = (info or UpdateInfo(UpdateStatus.UNAVAILABLE, VERSION)).message()
        messagebox.showinfo("Check for updates", message, parent=self.root)

    # -------------------------------------------------------------- state and messages

    def set_state(self, text: str, color: str) -> None:
        self.compact.set_state(text, color)
        self.full.set_state(text, color)

    def _set_dot(self, color: str) -> None:
        self.compact.dot.configure(fg=color)
        self.full.dot.configure(fg=color)

    def show_note(self, text: str, *, tag: str = "out") -> None:
        self.compact.output.show_text(text, tag=tag)
        self.full.output.show_text(text, tag=tag)

    def _confirm(self, approval: Approval) -> bool:
        return messagebox.askokcancel(approval.title, approval.message, icon="warning", parent=self.root)

    def _callback_failed(self, kind, value, trace) -> None:
        text = "".join(traceback.format_exception(kind, value, trace))
        self.show_note("The window hit an unexpected error; nothing was changed by it.\n\n" + text, tag="err")
        self.set_state("WINDOW ERROR", theme.ERROR)


class OutputView:
    """A read-only, monospaced view of one command's output."""

    def __init__(self, window: RudraWindow, parent: tk.Misc, *, compact: bool):
        self.window = window
        self.frame = tk.Frame(parent, bg=theme.OUTPUT_BG, highlightthickness=1, highlightbackground=theme.LINE)
        mono = (window.fonts.mono[0], window.fonts.mono[1] - (1 if compact else 0))
        self.text = tk.Text(
            self.frame, bg=theme.OUTPUT_BG, fg=theme.TEXT, font=mono, relief="flat", borderwidth=0,
            wrap="word" if compact else "none", padx=window.px(12), pady=window.px(10), height=8,
            insertbackground=theme.ACCENT_SOFT, selectbackground=theme.ACCENT_DEEP, highlightthickness=0,
            state="disabled", cursor="arrow",
        )
        down = ttk.Scrollbar(self.frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=down.set)
        down.pack(side="right", fill="y")
        if not compact:
            across = ttk.Scrollbar(self.frame, orient="horizontal", command=self.text.xview)

            def sideways(first: str, last: str) -> None:
                """Shown only when a line is wider than the view (a raw report); not for words that wrap."""
                across.set(first, last)
                if float(first) <= 0.0 and float(last) >= 1.0:
                    across.pack_forget()
                elif not across.winfo_ismapped():
                    across.pack(side="bottom", fill="x", before=down)

            self.text.configure(xscrollcommand=sideways)
        self.text.pack(side="left", fill="both", expand=True)
        for tag, color in (("cmd", theme.ACCENT_SOFT), ("out", theme.TEXT), ("log", theme.FAINT),
                           ("err", theme.ERROR), ("warn", theme.WARN), ("meta", theme.MUTED)):
            self.text.tag_configure(tag, foreground=color)
        self.text.tag_configure("answer", foreground=theme.ACCENT_SOFT, font=(mono[0], mono[1], "bold"))
        body = window.fonts.body
        self.text.tag_configure("a_request", foreground=theme.MUTED, font=body, spacing3=window.px(6))
        self.text.tag_configure("a_line", foreground=theme.TEXT, font=(body[0], body[1] + 2), spacing1=window.px(3),
                                spacing3=window.px(3))
        self.text.tag_configure("a_title", foreground=theme.FAINT, font=window.fonts.code, spacing1=window.px(8))
        self.text.tag_configure("a_extra", foreground=theme.MUTED, font=body, lmargin1=window.px(14),
                                lmargin2=window.px(14))
        self.text.tag_configure("a_warn", foreground=theme.WARN, font=body, spacing1=window.px(4))
        self.text.tag_configure("s_label", foreground=theme.FAINT, font=window.fonts.code, spacing1=window.px(4))
        self.text.tag_configure("s_text", foreground=theme.MUTED, font=window.fonts.small, lmargin1=window.px(14),
                                lmargin2=window.px(14))
        # A worked solution: the step's label, its typeset lines (indented), the note after a
        # formula, and the closing check.
        self.text.tag_configure("w_step", foreground=theme.MUTED, font=window.fonts.label, spacing1=window.px(8),
                                lmargin1=window.px(2), lmargin2=window.px(2))
        self.text.tag_configure("w_math", foreground=theme.TEXT, font=(body[0], body[1] + 2), lmargin1=window.px(16),
                                lmargin2=window.px(16), spacing1=window.px(1), spacing3=window.px(1))
        self.text.tag_configure("w_note", foreground=theme.FAINT, font=window.fonts.small)
        self.text.tag_configure("w_ok", foreground=theme.OK, font=body, spacing1=window.px(8))
        self.text.tag_configure("w_bad", foreground=theme.ERROR, font=body, spacing1=window.px(8))
        self.compact = compact
        self.embedded: list[tk.Widget] = []
        self.answer: answerview.AnswerDocument | None = None
        self.expanded: set[int] = set()
        self.traces: dict[str, str] = {}
        self.source_buttons: dict[int, ttk.Button] = {}
        self.source_open_buttons: list[ttk.Button] = []
        self.ai_buttons: dict[tuple[str, int], ttk.Button] = {}
        self.explanations: dict[int, object] = {}
        self.explained_by = ""
        self.note_line: str | None = None
        self._metrics: mathrender.TkMetrics | None = None
        self._extract_results: list[CommandResult] = []
        self._extract_name: str | None = None
        self._extract_batch: list[tuple[str, CommandResult]] = []
        self._extract_shared: list[CommandResult] = []
        self._extract_open = False

    def _clear_embedded(self) -> None:
        for widget in self.embedded:
            widget.destroy()
        self.embedded.clear()
        self.source_buttons.clear()
        self.source_open_buttons.clear()
        self.ai_buttons.clear()

    def write(self, parts: list[tuple[str, str]]) -> None:
        self.answer = None
        self._extract_results = []
        self._extract_batch = []
        self._extract_shared = []
        self._clear_embedded()
        self.text.configure(wrap="word" if self.compact else "none")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for text, tag in parts:
            if text:
                self.text.insert("end", text, tag)
        self.text.configure(state="disabled")
        self.text.see("1.0")

    def show_text(self, text: str, *, tag: str = "out") -> None:
        self.write([(text, tag)])

    def show_running(self, argv: list[str]) -> None:
        if commands.is_friendly_command(argv):
            self.write([(commands.working_text(argv), "meta")])
        else:
            self.write([(f"› {commands.display_command(argv)}\n\n", "cmd"), ("Running…", "meta")])

    def show_welcome(self, facts: dict, attention: tuple[str, ...]) -> None:
        """The first thing a person sees: whether there is knowledge yet, and what to do next."""
        self.answer = None
        self._extract_results = []
        self._extract_batch = []
        self._extract_shared = []
        self._clear_embedded()
        self.text.configure(state="normal", wrap="word")
        self.text.delete("1.0", "end")
        documents, counts = facts.get("documents", 0), facts.get("counts") or {}
        if not documents:
            self.text.insert("end", "Welcome to RUDRA.\n", "a_line")
            self.text.insert("end", "Your knowledge base is empty. Add a document - a PDF, Word file, slides, a "
                                    "spreadsheet or a scanned page - and RUDRA reads it and keeps what it states, with "
                                    "the page it came from. Then ask it questions in plain English, or ask it to "
                                    "calculate.\n", "a_extra")
            self._welcome_button("Add a document", "import")
        else:
            self.text.insert("end", "Your knowledge base is ready.\n", "a_line")
            names = (("CONCEPT", "concept"), ("DEFINITION", "definition"), ("EQUATION", "equation"),
                     ("VARIABLE", "variable"), ("RULE", "rule"), ("EXAMPLE", "example"))
            parts = [f"{documents} document{'s' if documents != 1 else ''}"]
            parts += [f"{counts[key]} {label}{'s' if counts[key] != 1 else ''}" for key, label in names
                      if counts.get(key)]
            self.text.insert("end", " · ".join(parts) + "\n", "a_extra")
            if counts.get("EQUATION"):
                self.text.insert("end", f"{facts.get('usable', 0)} of the {counts['EQUATION']} equations can be "
                                        "calculated with.\n", "a_extra")
            self.text.insert("end", "Ask a question above, or look through what RUDRA holds.\n", "a_extra")
            self._welcome_button("See what RUDRA knows", "knowledge")
        for line in attention:
            self.text.insert("end", line + "\n", "a_warn")
        self.text.configure(state="disabled")
        self.text.see("1.0")

    def _welcome_button(self, label: str, page: str) -> None:
        def go() -> None:
            self.window.show(full=True)
            self.window.full.select(page)

        button = ttk.Button(self.text, text=label, style="Accent.TButton", command=go)
        self.embedded.append(button)
        self.text.window_create("end", window=button, padx=self.window.px(2), pady=self.window.px(8))
        self.text.insert("end", "\n", "a_line")

    def show_result(self, result: CommandResult) -> None:
        if commands.is_answer_command(result.argv):
            document = answerview.parse_answer(result.stdout)
            if document is not None and document.parts:
                self.show_answer(document)
                return
        if commands.is_extract_command(result.argv):
            self.show_extract_summary([result])
            return
        self.write([
            (f"› {commands.display_command(result.argv)}\n\n", "cmd"),
            *((line, "answer" if commands.is_answer_line(line) else "out")
              for line in result.stdout.splitlines(keepends=True)),
            (("\n" if result.stdout and not result.stdout.endswith("\n") else "") + result.stderr,
             "log" if result.ok else "err"),
            (f"\n{'ok' if result.ok else f'exit {result.exit_code}'} · {result.meaning} · "
             f"{result.seconds:.2f} s\n", "meta"),
        ])

    def text_content(self) -> str:
        return self.text.get("1.0", "end-1c")

    # -------------------------------------------------------------- adding a document

    def show_extract_summary(self, results: list[CommandResult]) -> None:
        """"Added to your knowledge base.", not a dump of `extract`'s (and `index`'s) own
        technical report - that stays one click away behind Details, exactly as RUDRA
        produced it. `results` is `extract` alone, or `extract` then `index` (Add Document:
        every query mode ready immediately, not only the ones that never needed an index)."""
        self.answer = None
        self._extract_results = results
        self._extract_name = self.window.pending_import_name  # read only: both views need it
        self._extract_batch = []
        self._extract_shared = []
        self._extract_open = False
        self._render_extract()

    def show_extract_batch(self, documents: list[tuple[str, CommandResult]],
                           shared: list[CommandResult]) -> None:
        """Show a readable result for every attempted document, with shared work once."""
        self.answer = None
        self._extract_batch = documents
        self._extract_shared = shared
        self._extract_results = [result for _name, result in documents] + shared
        self._extract_name = None
        self._extract_open = False
        self._render_extract()

    def _render_extract(self) -> None:
        if self._extract_batch:
            self._render_extract_batch()
            return
        results = self._extract_results
        if not results:
            return
        summary = commands.extract_summary(results)
        self._clear_embedded()
        self.text.configure(state="normal", wrap="word")
        self.text.delete("1.0", "end")
        if self._extract_name:
            self.text.insert("end", self._extract_name + "\n", "a_request")
        self.text.insert("end", summary.headline + "\n", "a_line" if summary.ok else "a_warn")
        extra = []
        if summary.pages:
            extra.append(summary.pages + " read")
        if summary.ocr_count:
            extra.append(f"{summary.ocr_count} page(s) read by OCR - that text is marked uncertain")
        if extra:
            self.text.insert("end", " · ".join(extra) + "\n", "a_extra")
        for line in summary.advice:
            self.text.insert("end", line.rstrip(".") + ".\n", "a_extra")
        if summary.empty:
            self.text.insert("end", "RUDRA keeps definitions, equations, variables, properties, rules and similar "
                                    "statements; this document has none in a form it recognises. You can still ask "
                                    "RUDRA to search its text (for example: Search for a word).\n", "a_extra")
        if summary.ok and summary.stored:
            self.text.insert("end", "Stored: " + ", ".join(summary.stored) + "\n", "a_extra")
        if summary.linked:
            self.text.insert("end", "Linked: " + summary.linked + "\n", "a_extra")
        for line in summary.not_stored:
            self.text.insert("end", "Not stored: " + line + "\n", "a_warn")
        if summary.ok and summary.ready:
            if not summary.empty:
                self.text.insert("end", "You can ask about it now.\n", "a_extra")
        elif summary.ok:
            self.text.insert("end", "It will be searchable the next time you ask.\n", "a_extra")
        row = tk.Frame(self.text, bg=theme.OUTPUT_BG)
        if summary.ok and summary.document_id:
            see = ttk.Button(row, text="See what was stored", style="Accent.TButton",
                             command=lambda: self._see_stored(summary.document_id))
            see.pack(side="left", padx=(0, self.window.px(6)))
        button = ttk.Button(row, text="Hide details" if self._extract_open else "Details",
                            command=self.toggle_extract_details)
        button.pack(side="left")
        self.embedded.append(row)
        self.text.window_create("end", window=row, padx=self.window.px(2), pady=self.window.px(6))
        self.text.insert("end", "\n", "a_line")
        if self._extract_open:
            for result in results:
                if result.argv and result.argv[0] == "inventory":
                    continue
                self.text.insert("end", f"› {commands.display_command(result.argv)}\n\n", "cmd")
                for line in result.stdout.splitlines(keepends=True):
                    self.text.insert("end", line, "answer" if commands.is_answer_line(line) else "out")
                if result.stderr:
                    self.text.insert("end", ("\n" if result.stdout and not result.stdout.endswith("\n") else "")
                                     + result.stderr, "log" if result.ok else "err")
                self.text.insert("end", f"\n{'ok' if result.ok else f'exit {result.exit_code}'} · {result.meaning} "
                                 f"· {result.seconds:.2f} s\n\n", "meta")
        self.text.configure(state="disabled")
        self.text.see("1.0")

    def _render_extract_batch(self) -> None:
        successes = sum(result.ok for _name, result in self._extract_batch)
        total = len(self._extract_batch)
        self._clear_embedded()
        self.text.configure(state="normal", wrap="word")
        self.text.delete("1.0", "end")
        if successes == total:
            headline = f"Added {total} document{'s' if total != 1 else ''} to your knowledge base."
            headline_tag = "a_line"
        else:
            headline = f"Added {successes} of {total} documents. Check the ones marked below."
            headline_tag = "a_warn"
        self.text.insert("end", headline + "\n", headline_tag)
        index = next((result for result in self._extract_shared if result.argv and result.argv[0] == "index"), None)
        listing = next((result for result in self._extract_shared if result.argv and result.argv[0] == "inventory"), None)
        for name, extracted in self._extract_batch:
            results = [extracted, *([index] if index is not None else []),
                       *([listing] if listing is not None else [])]
            summary = commands.extract_summary(results)
            self.text.insert("end", "\n" + name + "\n", "a_request")
            self.text.insert("end", summary.headline + "\n", "a_line" if summary.ok else "a_warn")
            extra = []
            if summary.pages:
                extra.append(summary.pages + " read")
            if summary.ocr_count:
                extra.append(f"{summary.ocr_count} page(s) read by OCR - that text is marked uncertain")
            if extra:
                self.text.insert("end", " · ".join(extra) + "\n", "a_extra")
            for line in summary.advice:
                self.text.insert("end", line.rstrip(".") + ".\n", "a_extra")
            if summary.ok and summary.stored:
                self.text.insert("end", "Stored: " + ", ".join(summary.stored) + "\n", "a_extra")
            if summary.linked:
                self.text.insert("end", "Linked: " + summary.linked + "\n", "a_extra")
            for line in summary.not_stored:
                self.text.insert("end", "Not stored: " + line + "\n", "a_warn")
            if summary.ok and summary.empty:
                self.text.insert("end", "RUDRA found no statements it could store as knowledge; you can still "
                                        "search this document's text.\n", "a_extra")
            elif summary.ok and summary.ready:
                self.text.insert("end", "You can ask about it now.\n", "a_extra")
            elif summary.ok:
                self.text.insert("end", "It will be searchable the next time you ask.\n", "a_extra")
            if summary.ok and summary.document_id:
                row = tk.Frame(self.text, bg=theme.OUTPUT_BG)
                button = ttk.Button(row, text="See what was stored", style="Accent.TButton",
                                     command=lambda doc_id=summary.document_id, doc_name=name:
                                     self._see_stored(doc_id, doc_name))
                button.pack(side="left")
                self.embedded.append(row)
                self.text.window_create("end", window=row, padx=self.window.px(2), pady=self.window.px(4))
                self.text.insert("end", "\n", "a_line")
        detail = ttk.Button(self.text, text="Hide details" if self._extract_open else "Details",
                            command=self.toggle_extract_details)
        self.embedded.append(detail)
        self.text.window_create("end", window=detail, padx=self.window.px(2), pady=self.window.px(6))
        self.text.insert("end", "\n", "a_line")
        if self._extract_open:
            for name, result in self._extract_batch:
                self._write_extract_detail(name, result)
            for result in self._extract_shared:
                if result.argv and result.argv[0] == "inventory":
                    continue
                self._write_extract_detail(None, result)
        self.text.configure(state="disabled")
        self.text.see("1.0")

    def _write_extract_detail(self, name: str | None, result: CommandResult) -> None:
        if name:
            self.text.insert("end", name + "\n", "a_request")
        self.text.insert("end", f"› {commands.display_command(result.argv)}\n\n", "cmd")
        for line in result.stdout.splitlines(keepends=True):
            self.text.insert("end", line, "answer" if commands.is_answer_line(line) else "out")
        if result.stderr:
            self.text.insert("end", ("\n" if result.stdout and not result.stdout.endswith("\n") else "")
                             + result.stderr, "log" if result.ok else "err")
        self.text.insert("end", f"\n{'ok' if result.ok else f'exit {result.exit_code}'} · {result.meaning} "
                         f"· {result.seconds:.2f} s\n\n", "meta")

    def _see_stored(self, document_id: str, name: str | None = None) -> None:
        self.window.show(full=True)
        page = self.window.full.pages["knowledge"]
        page.show_document(document_id, name or self._extract_name or document_id)
        self.window.full.select("knowledge")

    def toggle_extract_details(self) -> None:
        self._extract_open = not self._extract_open
        self._render_extract()

    # -------------------------------------------------------------- answers

    def show_answer(self, document: answerview.AnswerDocument) -> None:
        """An answer by itself; each part's sources stay behind its View Sources button."""
        self.write([])
        self.answer = document
        self.expanded = set()
        self.traces = {}
        self.explanations = {}
        self.note_line, self.window.pending_note = self.window.pending_note, None
        self._render_answer()

    def rerender(self) -> None:
        self._render_answer()

    def _render_answer(self) -> None:
        document = self.answer
        if document is None:
            return
        view = self.text.yview()[0]
        self._clear_embedded()
        self.text.configure(state="normal", wrap="word")
        self.text.delete("1.0", "end")
        if document.request:
            self.text.insert("end", document.request + "\n", "a_request")
        if self.note_line:
            self.text.insert("end", self.note_line + "\n", "a_title")
        for index, part in enumerate(document.parts):
            if index:
                self.text.insert("end", "\n", "a_line")
            if part.worked is not None:
                self._worked(part.worked)
            else:
                for line in part.lines:
                    self._answer_line(line)
            if part.conflict is not None:
                self._conflict(part.conflict)
            if part.has_conflict:
                self.text.insert("end", "Sources disagree on this. View Sources shows each claim.\n", "a_warn")
            for line in part.uncertain:
                self.text.insert("end", f"Uncertain: {line}\n", "a_warn")
            self._ai_block(part, document.request)
            for title, values in part.shown_extras():
                self.text.insert("end", theme.spaced(title) + "\n", "a_title")
                for value in values:
                    self._rich_line(value, "a_extra", size=15)
            if not part.details.empty:
                self._sources_button(part)
                if part.number in self.expanded:
                    self._sources(part)
        self.text.configure(state="disabled")
        self.text.yview_moveto(view)

    def _answer_line(self, line: str) -> None:
        self._rich_line(line, "a_line", size=17)

    def _rich_line(self, line: str, tag: str, *, size: float = 17) -> None:
        """One line of text; a formula in it - an equation, or LaTeX written in the sentence -
        is typeset where it stands, the words around it left as they are."""
        pieces = mathrender.segments(line)
        if pieces is None:
            self.text.insert("end", line + "\n", tag)
            return
        for kind, piece in pieces:
            if kind == "text":
                if piece:
                    self.text.insert("end", piece, tag)
            else:
                self.text.window_create("end", window=self.formula(piece, size=size), align="center",
                                        padx=self.window.px(4), pady=self.window.px(2))
        self.text.insert("end", "\n", tag)

    def _math_line(self, markup: str, *, size: float = 17, color: str = theme.TEXT) -> None:
        """A typeset formula on a line of its own, indented (a step of a worked solution)."""
        self.text.insert("end", " ", "w_math")
        self.text.window_create("end", window=self.formula(markup, size=size, color=color), align="center",
                                padx=self.window.px(2), pady=self.window.px(2))
        self.text.insert("end", "\n", "w_math")

    def _worked(self, worked: "worked_solution.Worked") -> None:
        """A calculation as a textbook sets it: the result, what was given, then each step as
        an equation, the equation with its values put in, and the value it gave."""
        result = worked.result
        self.text.insert("end", theme.spaced("Result") + (f"   {result.name}" if result.name else "") + "\n",
                         "a_title")
        card = self.formula(result.markup(), size=26, color=theme.ACCENT_SOFT, card=True)
        self.text.window_create("end", window=card, align="center", padx=self.window.px(2), pady=self.window.px(4))
        self.text.insert("end", "\n", "a_line")
        if result.exact:
            self.text.insert("end", f"rounded to 6 significant figures; the exact value is {result.exact}\n",
                             "w_note")
        if worked.givens:
            self.text.insert("end", theme.spaced("Given") + "\n", "a_title")
            for given in worked.givens:
                self.text.insert("end", " ", "w_math")
                self.text.window_create(
                    "end", window=self.formula(worked_solution.substitution_markup(
                        f"{given.symbol} = {given.value}"), size=16), align="center",
                    padx=self.window.px(2), pady=self.window.px(1))
                if given.name:
                    self.text.insert("end", f"  {given.name}", "w_note")
                self.text.insert("end", "\n", "w_math")
        if worked.steps:
            self.text.insert("end", theme.spaced("Working") + "\n", "a_title")
        for step in worked.steps:
            self.text.insert("end", f"Step {step.number}" + (f" · {step.name}" if step.name else "") + "\n", "w_step")
            self._math_line(step.equation)
            if step.stored:
                self.text.insert("end", "turned around from the stored equation  ", "w_note")
                self.text.window_create("end", window=self.formula(step.stored, size=14, color=theme.MUTED),
                                        align="center", padx=self.window.px(2))
                self.text.insert("end", "\n", "w_note")
            self._math_line(step.working)
        if worked.check:
            self.text.insert("end", ("✓ " if worked.check_ok else "✗ ") + worked.check + "\n",
                             "w_ok" if worked.check_ok else "w_bad")

    def _conflict(self, conflict: "worked_solution.Conflict") -> None:
        """Stored equations that disagree, side by side: each route's equations and the value they
        gave. None is chosen - RUDRA does not know which of them the documents mean."""
        self.text.insert("end", theme.spaced("Conflict") + "\n", "a_title")
        for route in conflict.routes:
            self.text.insert("end", f"Route {route.letter}\n", "w_step")
            self._math_line(conflict.route_markup(route), size=16)
        self.text.insert("end", "No value was chosen.\n", "w_note")

    def formula(self, source: str, *, size: float = 17, color: str = theme.TEXT, card: bool = False) -> tk.Canvas:
        """A typeset formula, drawn on a canvas that sits in the text like a word."""
        if self._metrics is None:
            self._metrics = mathrender.TkMetrics(self.text)
        # A long equation breaks into lines that fit the answer area, as a textbook sets it.
        available = self.text.winfo_width() - self.window.px(60 if card else 40)
        box = mathrender.layout(mathrender.parse(source), self.window.px(size), self._metrics,
                                max_width=available if available > self.window.px(200) else None)
        margin = self.window.px(10 if card else 3)
        # Whole pixels, rounded up: the layout measures in fractions, and Tk 9 keeps a
        # fractional size as given, where Tk 8.6 rounded it.
        canvas = tk.Canvas(self.text, width=math.ceil(box.width + 2 * margin),
                           height=math.ceil(box.ascent + box.descent + 2 * margin),
                           bg=theme.RAISED if card else theme.OUTPUT_BG,
                           highlightthickness=1 if card else 0, highlightbackground=theme.ACCENT_DEEP,
                           borderwidth=0, cursor="arrow")
        mathrender.draw_on_canvas(canvas, box, self._metrics, margin, margin + box.ascent, color)
        canvas.formula_source = source  # the stored text, unchanged: what the picture shows
        self.embedded.append(canvas)
        return canvas

    def _ai_block(self, part: answerview.AnswerPart, question: str) -> None:
        """Optional AI actions and results. Nothing appears unless the user enabled a provider."""
        explanation = self.explanations.get(part.number)
        if explanation is not None:
            self.text.insert("end", theme.spaced("Explanation") + f"  worded by {self.explained_by} from the "
                             "statements above - not a source\n", "a_title")
            if isinstance(explanation, str):
                self.text.insert("end", explanation + "\n", "a_warn")
            elif explanation.insufficient:
                self.text.insert("end", "The statements from your documents are not enough to explain this; "
                                 "nothing was added.\n", "a_warn")
            else:
                for sentence in explanation.sentences:
                    marks = "".join(f"[{n}]" for n in sentence.citations)
                    self.text.insert("end", f"{sentence.text} {marks}\n", "a_extra")
                if explanation.removed:
                    self.text.insert("end", f"{explanation.removed} sentence(s) of the reply were removed: no "
                                     "statement from your documents supported them.\n", "a_warn")
        if not self.window.ai_active():
            return
        provider = PROVIDERS[self.window.ai().provider].name
        if part.intent == "UNRECOGNIZED":
            kind, label, action = "interpret", f"Interpret with {provider}", lambda: self.window.ai_interpret(question)
        elif part.status == "ANSWERED" and part.lines and explanation is None:
            kind, label = "explain", f"Explain with {provider}"
            action = lambda p=part: self.window.ai_explain(self, p, question)  # noqa: E731
        else:
            return
        button = ttk.Button(self.text, text=label, command=action)
        self.embedded.append(button)
        self.ai_buttons[(kind, part.number)] = button
        self.text.window_create("end", window=button, padx=self.window.px(2), pady=self.window.px(4))
        self.text.insert("end", "\n", "a_line")

    def _sources_button(self, part: answerview.AnswerPart) -> None:
        opened = part.number in self.expanded
        button = ttk.Button(self.text, text="Hide Sources" if opened else "View Sources",
                            command=lambda number=part.number: self.toggle_sources(number))
        self.embedded.append(button)
        self.source_buttons[part.number] = button
        self.text.window_create("end", window=button, padx=self.window.px(2), pady=self.window.px(6))
        self.text.insert("end", "\n", "a_line")

    def _sources(self, part: answerview.AnswerPart) -> None:
        for label, value in answerview.detail_lines(part.details, commands.display_command):
            if label:
                self.text.insert("end", theme.spaced(label) + "\n", "s_label")
            self._source_value(value)
        opened: set[str] = set()
        for identifier in part.details.knowledge_ids():
            trace = self.traces.get(identifier)
            self.text.insert("end", f"Provenance of {identifier}\n", "s_label")
            self.text.insert("end", (trace if trace is not None else "Tracing…") + "\n", "s_text")
            for document_id, stored, page in answerview.source_files(trace or ""):
                if document_id in opened:
                    continue
                opened.add(document_id)
                where = f"{document_id}" + (f", page {page}" if page else "")
                button = ttk.Button(self.text, text=f"Open source document ({where})",
                                    command=lambda name=stored: self.window.open_source(name))
                self.embedded.append(button)
                self.source_open_buttons.append(button)
                self.text.window_create("end", window=button, padx=self.window.px(14), pady=self.window.px(3))
                self.text.insert("end", "\n", "s_text")

    def _source_value(self, value: str) -> None:
        """One line of the sources view. A stored equation is shown typeset; a quotation from a
        document is shown exactly as it reads there, and when it is written in LaTeX the
        typeset form follows it - the quotation is the evidence, the picture only helps read it."""
        stored = answerview.stored_equation(value)
        if stored is not None:
            head, formula = stored
            self.text.insert("end", head, "s_text")
            self.text.window_create("end", window=self.formula(formula, size=14, color=theme.MUTED),
                                    align="center", padx=self.window.px(2))
            self.text.insert("end", "\n", "s_text")
            return
        self.text.insert("end", value + "\n", "s_text")
        quoted = answerview.quoted_formula(value)
        if quoted is not None:
            self.text.insert("end", " ", "s_text")
            self.text.window_create("end", window=self.formula(quoted, size=14, color=theme.MUTED), align="center",
                                    padx=self.window.px(2))
            self.text.insert("end", "\n", "s_text")

    def toggle_sources(self, number: int) -> None:
        """Show or hide one part's sources; the first opening traces its knowledge items."""
        if number in self.expanded:
            self.expanded.discard(number)
        else:
            self.expanded.add(number)
        self._render_answer()
        if number in self.expanded:
            self._trace_next()

    def _trace_next(self) -> None:
        """Trace the next opened knowledge item with the `provenance` command (read-only)."""
        if self.answer is None:
            return
        wanted = [identifier for part in self.answer.parts if part.number in self.expanded
                  for identifier in part.details.knowledge_ids() if identifier not in self.traces]
        if not wanted:
            return
        identifier = wanted[0]
        document = self.answer

        def done(result: CommandResult) -> None:
            if self.answer is not document:
                return
            text = result.stdout.strip() if result.ok else (result.stdout + result.stderr).strip()
            self.traces[identifier] = text or f"Provenance unavailable ({result.meaning})."
            self._render_answer()
            self._trace_next()

        if not self.window.submit(commands.provenance(identifier), on_done=done):
            self.window.root.after(200, self._trace_next)


class UpdateBanner:
    """A quiet strip that says a newer release exists, with a link to its download page."""

    def __init__(self, window: RudraWindow, parent: tk.Misc, *, anchor: tk.Widget, before: bool):
        self.window = window
        px = window.px
        self.anchor, self.before = anchor, before
        self.frame = tk.Frame(parent, bg=theme.ACCENT_DEEP)
        self.label = tk.Label(self.frame, bg=theme.ACCENT_DEEP, fg="#f0f9ff", font=window.fonts.label, anchor="w")
        self.label.pack(side="left", fill="x", expand=True, padx=(px(12), px(6)), pady=px(6))
        ttk.Button(self.frame, text="Dismiss", command=self.hide).pack(side="right", padx=(0, px(10)))
        ttk.Button(self.frame, text="Download", command=self.download).pack(side="right", padx=(0, px(6)))
        self.shown = False

    def show(self, info: UpdateInfo) -> None:
        self.label.configure(text=info.message())
        if not self.shown:
            options = {"before": self.anchor} if self.before else {"after": self.anchor}
            self.frame.pack(fill="x", **options)
            self.shown = True

    def hide(self) -> None:
        self.frame.pack_forget()
        self.shown = False

    def download(self) -> None:
        info = self.window.update_info
        if info is not None:
            self.window.open_url(info.url)


class MicButton:
    """Speak a question or command instead of typing it (master specification: click, speak,
    edit, send). RUDRA recognizes speech offline, on this computer, with a Whisper model.

    One click starts listening and the button says so; listening ends by itself when the
    speaker stops, or on a second click, and then RUDRA works out the words - the button
    says that too, and a further click cancels. The recognized words replace the text
    wherever `insert` puts them - never sent by themselves.
    """

    #: Short, because the button sits in the assistant's one row with the input and Send.
    LABELS = {"listening": "Listening…", "recognizing": "Working…"}
    #: What the status line says meanwhile: how to finish, and how to cancel.
    STATES = {"listening": "LISTENING · CLICK WHEN DONE", "recognizing": "WORKING OUT THE WORDS · CLICK TO CANCEL"}

    def __init__(self, window: RudraWindow, parent: tk.Misc, insert: Callable[[str], None], *,
                 idle: str = "Speak"):
        self.window = window
        self.insert = insert
        self.idle = idle
        self._stop: Callable[[], None] | None = None
        self.button = ttk.Button(parent, text=idle, command=self._clicked)

    def _clicked(self) -> None:
        if self._stop is not None:
            self._stop()
            return
        if self.window.busy:
            return
        self.button.configure(text=self.LABELS["listening"])
        self._stop = self.window.listen(self._done, on_phase=self._phase)
        self.window.set_state(self.STATES["listening"], theme.ACCENT)  # after the task's own "RUNNING" state

    def _phase(self, phase: str) -> None:
        if self._stop is not None:
            self.button.configure(text=self.LABELS.get(phase, self.LABELS["listening"]))
            self.window.set_state(self.STATES.get(phase, self.STATES["listening"]), theme.ACCENT)

    def _done(self, result: VoiceResult) -> None:
        self._stop = None
        self.button.configure(text=self.idle)
        if result.text:
            self.insert(result.text)
            if result.uncertain:
                self.window.set_state("CHECK THE WORDS", theme.WARN)
        elif result.error:
            self.window.show_note(result.error, tag="warn")


def _status_row(window: RudraWindow, parent: tk.Misc, bg: str) -> tuple[tk.Label, tk.Label]:
    dot = tk.Label(parent, text="●", bg=bg, fg=theme.OK, font=window.fonts.small)
    dot.pack(side="left")
    state = tk.Label(parent, text="READY", bg=bg, fg=theme.MUTED, font=window.fonts.code)
    state.pack(side="left", padx=(window.px(4), 0))
    return dot, state


def _hairline(window: RudraWindow, parent: tk.Misc, bg: str = theme.BG) -> tk.Frame:
    """A one-pixel rule with a short accent segment at its start."""
    line = tk.Frame(parent, bg=theme.LINE, height=1)
    tk.Frame(line, bg=theme.ACCENT, height=1, width=window.px(56)).place(x=0, y=0)
    return line


class CompactView:
    """Section 139's assistant: one input (a question, or /command), status, the response."""

    def __init__(self, window: RudraWindow):
        self.window = window
        px, fonts = window.px, window.fonts
        self.frame = tk.Frame(window.root, bg=theme.BG)

        header = tk.Frame(self.frame, bg=theme.SURFACE)
        header.pack(fill="x")
        mark = window.mark(40)
        if mark:
            tk.Label(header, image=mark, bg=theme.SURFACE).pack(side="left", padx=(px(14), px(10)), pady=px(12))
        names = tk.Frame(header, bg=theme.SURFACE)
        names.pack(side="left", pady=px(12))
        tk.Label(names, text=theme.spaced("Rudra"), bg=theme.SURFACE, fg=theme.TEXT,
                 font=(fonts.wordmark[0], 15)).pack(anchor="w")
        tk.Label(names, text=theme.spaced("Assistant"), bg=theme.SURFACE, fg=theme.FAINT,
                 font=fonts.code).pack(anchor="w")
        expand = ttk.Button(header, text="Full window  ⤢", command=lambda: window.show(full=True))
        expand.pack(side="right", padx=px(14))
        rule = _hairline(window, self.frame)
        rule.pack(fill="x")
        self.banner = UpdateBanner(window, self.frame, anchor=rule, before=False)

        status = tk.Frame(self.frame, bg=theme.BG)
        status.pack(fill="x", padx=px(14), pady=(px(10), px(6)))
        self.dot, self.state = _status_row(window, status, theme.BG)
        self.project = tk.Label(status, bg=theme.BG, fg=theme.FAINT, font=fonts.small, anchor="e")
        self.project.pack(side="right")
        self.show_project()

        self.output = OutputView(window, self.frame, compact=True)
        self.output.frame.pack(fill="both", expand=True, padx=px(14))
        self.output.show_text("RUDRA is starting…", tag="meta")

        entry_row = tk.Frame(self.frame, bg=theme.BG)
        entry_row.pack(fill="x", padx=px(14), pady=(px(10), px(4)))
        self.entry = ttk.Entry(entry_row, font=fonts.body)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _event: self.send())
        self.mic = MicButton(window, entry_row, self._dictated)
        self.mic.button.pack(side="left", padx=(px(6), 0))
        send = ttk.Button(entry_row, text="Send", style="Accent.TButton", command=self.send)
        send.pack(side="left", padx=(px(6), 0))
        window.run_controls.append(send)

        options = tk.Frame(self.frame, bg=theme.BG)
        options.pack(fill="x", padx=px(14), pady=(0, px(12)))
        ttk.Checkbutton(options, text="Act on this computer", variable=window.act).pack(side="left")
        tk.Label(options, text="a question  ·  /command  ·  /help", bg=theme.BG, fg=theme.FAINT,
                 font=fonts.small).pack(side="right")

    def focus(self) -> None:
        self.entry.focus_set()

    def show_project(self) -> None:
        self.project.configure(text=f"project  {_short(self.window.project_root, 34)}")

    def _dictated(self, text: str) -> None:
        """Recognized speech replaces the entry's text; the user can still edit it before Send."""
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self.entry.focus_set()
        self.entry.icursor("end")

    def send(self) -> bool:
        text = self.entry.get()
        slash = text.strip().startswith("/")
        started = self.window.run_form(
            lambda: commands.assistant(text, act=self.window.act.get(), confirm=self.window.confirm_medium.get()),
            approve=slash)
        if started:
            self.entry.delete(0, "end")
        return started

    def set_state(self, text: str, color: str) -> None:
        self.dot.configure(fg=color)
        self.state.configure(text=text, fg=theme.MUTED if color == theme.OK else color)

    def show_running(self, argv: list[str]) -> None:
        self.output.show_running(argv)

    def show_result(self, result: CommandResult) -> None:
        if result.argv == ("start",):
            self.output.write([(commands.startup_summary(result), "out"),
                               ("\n\nAsk a question, or type /help for the commands.", "meta")])
        else:
            self.output.show_result(result)


def _short(path: Path, limit: int) -> str:
    text = str(path)
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


class NavItem:
    """One entry of the sidebar: a code, a name and an accent bar when selected."""

    def __init__(self, view: FullView, parent: tk.Misc, code: str, title: str, key: str):
        px, fonts = view.window.px, view.window.fonts
        self.frame = tk.Frame(parent, bg=theme.SURFACE, cursor="hand2")
        self.bar = tk.Frame(self.frame, bg=theme.SURFACE, width=px(3))
        self.bar.pack(side="left", fill="y")
        self.code = tk.Label(self.frame, text=code, bg=theme.SURFACE, fg=theme.FAINT, font=fonts.code,
                             padx=px(12), pady=px(8))
        self.code.pack(side="left")
        self.title = tk.Label(self.frame, text=title, bg=theme.SURFACE, fg=theme.MUTED, font=fonts.body, anchor="w")
        self.title.pack(side="left", fill="x", expand=True)
        self.active = False
        for widget in (self.frame, self.code, self.title):
            widget.bind("<Button-1>", lambda _event: view.select(key))
            widget.bind("<Enter>", lambda _event: self._paint(hover=True))
            widget.bind("<Leave>", lambda _event: self._paint(hover=False))

    def set_active(self, active: bool) -> None:
        self.active = active
        self._paint(hover=False)

    def _paint(self, *, hover: bool) -> None:
        bg = theme.RAISED if self.active else (theme.HOVER if hover else theme.SURFACE)
        for widget in (self.frame, self.code, self.title):
            widget.configure(bg=bg)
        self.bar.configure(bg=theme.ACCENT if self.active else bg)
        self.code.configure(fg=theme.ACCENT if self.active else theme.FAINT)
        self.title.configure(fg=theme.TEXT if self.active or hover else theme.MUTED)


class FullView:
    """The full interface: sidebar, the selected page, the output and a status bar."""

    def __init__(self, window: RudraWindow):
        self.window = window
        px, fonts = window.px, window.fonts
        self.frame = tk.Frame(window.root, bg=theme.BG)

        bar = tk.Frame(self.frame, bg=theme.SURFACE, height=px(28))
        bar.pack(side="bottom", fill="x")
        tk.Frame(self.frame, bg=theme.LINE, height=1).pack(side="bottom", fill="x")
        inner = tk.Frame(bar, bg=theme.SURFACE)
        inner.pack(fill="both", expand=True, padx=px(12), pady=px(5))
        self.dot, self.state = _status_row(window, inner, theme.SURFACE)
        tk.Label(inner, text=f"RUDRA {VERSION}", bg=theme.SURFACE, fg=theme.FAINT,
                 font=fonts.small).pack(side="right")

        sidebar = tk.Frame(self.frame, bg=theme.SURFACE, width=px(248))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Frame(self.frame, bg=theme.LINE, width=1).pack(side="left", fill="y")
        self._brand(sidebar)

        nav = tk.Frame(sidebar, bg=theme.SURFACE)
        nav.pack(fill="x", pady=(px(6), 0))
        main = tk.Frame(self.frame, bg=theme.BG)
        main.pack(side="left", fill="both", expand=True)

        header = tk.Frame(main, bg=theme.BG)
        header.pack(fill="x", padx=px(28), pady=(px(22), px(10)))
        self.banner = UpdateBanner(window, main, anchor=header, before=True)
        top = tk.Frame(header, bg=theme.BG)
        top.pack(fill="x")
        self.page_code = tk.Label(top, bg=theme.BG, fg=theme.ACCENT, font=fonts.code)
        self.page_code.pack(side="left")
        ttk.Button(top, text="Compact  ◱", command=lambda: window.show(full=False)).pack(side="right")
        self.page_title = tk.Label(header, bg=theme.BG, fg=theme.TEXT, font=fonts.title, anchor="w")
        self.page_title.pack(fill="x", pady=(px(2), px(4)))
        self.page_text = tk.Label(header, bg=theme.BG, fg=theme.MUTED, font=fonts.body, anchor="w",
                                  justify="left", wraplength=px(820))
        self.page_text.pack(fill="x")
        _hairline(window, main).pack(fill="x", padx=px(28))

        panes = tk.PanedWindow(main, orient="vertical", bg=theme.BG, sashwidth=px(8), sashrelief="flat",
                               borderwidth=0, showhandle=False)
        panes.pack(fill="both", expand=True, padx=px(28), pady=(px(14), px(16)))
        self.panes = panes
        holder = tk.Frame(panes, bg=theme.BG)
        panes.add(holder, minsize=px(150), height=px(215))
        # The page is as tall as it needs to be; when the window is too short for it, it scrolls.
        self.canvas = tk.Canvas(holder, bg=theme.BG, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=theme.BG)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self._body_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _event: self._resized())
        self.canvas.bind("<Configure>", lambda event: (self.canvas.itemconfigure(self._body_id, width=event.width),
                                                       self._resized()))
        holder.bind_all("<MouseWheel>", self._wheel, add="+")

        out = tk.Frame(panes, bg=theme.BG)
        panes.add(out, minsize=px(140))
        self.output_frame = out
        head = tk.Frame(out, bg=theme.BG)
        head.pack(fill="x", pady=(0, px(6)))
        self.output_label = tk.Label(head, text=theme.spaced("Result"), bg=theme.BG, fg=theme.FAINT, font=fonts.code)
        self.output_label.pack(side="left")
        ttk.Button(head, text="Copy", command=self.copy).pack(side="right")
        self.output = OutputView(window, out, compact=False)
        self.output.frame.pack(fill="both", expand=True)
        self.output.show_text("RUDRA is starting…", tag="meta")

        self.pages: dict[str, Page] = {}
        self.nav: dict[str, NavItem] = {}
        advanced_shown = False
        for page_class in PAGES:
            page = page_class(self, self.body)
            page.frame.grid(row=0, column=0, sticky="nsew")
            page.frame.grid_remove()
            self.pages[page.key] = page
            if page_class.hidden:
                continue
            if page_class.advanced and not advanced_shown:
                advanced_shown = True
                tk.Label(nav, text=theme.spaced("Advanced"), bg=theme.SURFACE, fg=theme.FAINT, font=fonts.code,
                         anchor="w").pack(fill="x", padx=px(14), pady=(px(16), px(2)))
            code = "·" if page_class.advanced else f"{len([n for n in self.nav if not self.pages[n].advanced]) + 1:02d}"
            item = NavItem(self, nav, code, page.title, page.key)
            item.frame.pack(fill="x")
            self.nav[page.key] = item
        self._project_panel(sidebar)
        self.selected = ""
        self.select("ask")
        panes.bind("<Map>", lambda _event: self.fit())

    # -------------------------------------------------------------- the scrolling page

    def _resized(self) -> None:
        """Keep the scrollable area the size of the page, and show the scrollbar only when it is needed."""
        needed = self.body.winfo_reqheight()
        self.canvas.itemconfigure(self._body_id, height=max(needed, self.canvas.winfo_height()))
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), max(needed, self.canvas.winfo_height())))
        if needed > self.canvas.winfo_height() > 1:
            if not self.scrollbar.winfo_ismapped():
                self.scrollbar.pack(side="right", fill="y")
        else:
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel(self, event) -> None:
        if not self.scrollbar.winfo_ismapped():
            return
        widget = self.window.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self.canvas:
                self.canvas.yview_scroll(-1 * (event.delta // 120), "units")
                return
            widget = widget.master

    def _output_shown(self) -> bool:
        """Whether the result area is part of the window now (a pane is a Tcl object, not a string)."""
        return str(self.output_frame) in [str(pane) for pane in self.panes.panes()]

    def fit(self) -> None:
        """Give the page the height it needs; the result below gets the rest."""
        px = self.window.px
        total = self.panes.winfo_height()
        if total <= 1:
            return
        self.body.update_idletasks()
        if self._output_shown():
            wanted = self.body.winfo_reqheight() + px(6)
            top = max(px(120), min(wanted, total - px(190)))
            with contextlib.suppress(tk.TclError):  # the pane may not be laid out yet; the next call fits it
                self.panes.sash_place(0, 0, top)
        self._resized()

    def _brand(self, sidebar: tk.Frame) -> None:
        """The sidebar's head: the mark over a faint grid, framed by corner brackets."""
        px, fonts = self.window.px, self.window.fonts
        width, height = px(248), px(200)
        canvas = tk.Canvas(sidebar, width=width, height=height, bg=theme.SURFACE, highlightthickness=0)
        canvas.pack(fill="x")
        step = px(14)
        for x in range(step, width, step):
            for y in range(step, height, step):
                canvas.create_rectangle(x, y, x + 1, y + 1, outline="", fill=theme.GRID_DOT)
        inset, arm = px(12), px(14)
        for cx, cy, dx, dy in ((inset, inset, 1, 1), (width - inset, inset, -1, 1),
                               (inset, height - inset, 1, -1), (width - inset, height - inset, -1, -1)):
            canvas.create_line(cx, cy, cx + dx * arm, cy, fill=theme.LINE_BRIGHT)
            canvas.create_line(cx, cy, cx, cy + dy * arm, fill=theme.LINE_BRIGHT)
        mark = self.window.mark(84)
        if mark:
            canvas.create_image(width // 2, px(66), image=mark)
        canvas.create_text(width // 2, px(132), text=theme.spaced("Rudra"), fill=theme.TEXT,
                           font=fonts.wordmark)
        canvas.create_text(width // 2, px(158), text="ROBOTIC UNIFIED DESIGN\nRESEARCH AGENT", fill=theme.MUTED,
                           font=fonts.small, justify="center")
        canvas.create_text(width // 2, px(184), text=f"VERSION {VERSION}", fill=theme.FAINT,
                           font=fonts.code)

    def _project_panel(self, sidebar: tk.Frame) -> None:
        px, fonts = self.window.px, self.window.fonts
        panel = tk.Frame(sidebar, bg=theme.SURFACE)
        panel.pack(side="bottom", fill="x", padx=px(16), pady=px(16))
        tk.Label(panel, text=theme.spaced("Your data"), bg=theme.SURFACE, fg=theme.FAINT,
                 font=fonts.code).pack(anchor="w")
        self.project = tk.Label(panel, bg=theme.SURFACE, fg=theme.MUTED, font=fonts.small, justify="left",
                                anchor="w", wraplength=px(212))
        self.project.pack(fill="x", pady=(px(4), px(8)))
        buttons = tk.Frame(panel, bg=theme.SURFACE)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Change…", command=self.change_project).pack(side="left")
        if sys.platform == "win32":
            ttk.Button(buttons, text="Open folder", command=lambda: os.startfile(self.window.project_root)).pack(
                side="left", padx=(px(6), 0))
        self.show_project()

    def show_project(self) -> None:
        self.project.configure(text=str(self.window.project_root))

    def change_project(self) -> None:
        chosen = filedialog.askdirectory(parent=self.window.root, initialdir=str(self.window.project_root),
                                         mustexist=True, title="Choose RUDRA's project folder")
        if chosen:
            self.window.set_project(Path(chosen))
            self.window.show_note(f"Project folder: {Path(chosen)}\nCommands now run with --project-root "
                                  "set to it. Startup report shows its state.", tag="meta")

    def select(self, key: str) -> None:
        page = self.pages[key]
        for other in self.pages.values():
            other.frame.grid_remove()
        page.frame.grid(row=0, column=0, sticky="nsew")
        for name, item in self.nav.items():
            item.set_active(name == key)
        main = [name for name in self.nav if not self.pages[name].advanced]
        if key in main:
            self.page_code.configure(text=f"{main.index(key) + 1:02d}  /  {theme.spaced(page.title)}")
        else:
            self.page_code.configure(text=f"{theme.spaced('Advanced')}  /  {theme.spaced(page.title)}")
        self.page_title.configure(text=page.heading)
        self.page_text.configure(text=page.description)
        self.selected = key
        shown = self._output_shown()
        if page.shows_output and not shown:
            self.panes.add(self.output_frame, minsize=self.window.px(140))
        elif not page.shows_output and shown:
            self.panes.forget(self.output_frame)
        page.focus()
        self.canvas.yview_moveto(0)
        self.window.root.after_idle(self.fit)

    def focus(self) -> None:
        self.pages[self.selected].focus()

    def set_state(self, text: str, color: str) -> None:
        self.dot.configure(fg=color)
        self.state.configure(text=text, fg=theme.MUTED if color == theme.OK else color)

    def show_running(self, argv: list[str]) -> None:
        self.output.show_running(argv)

    def show_result(self, result: CommandResult) -> None:
        self.output.show_result(result)

    def copy(self) -> None:
        self.window.root.clipboard_clear()
        self.window.root.clipboard_append(self.output.text_content())


# ------------------------------------------------------------------ pages


class Page:
    """One page of the full interface: a short explanation and a form that builds a command line."""

    key = ""
    title = ""
    heading = ""
    description = ""
    #: True for a page kept fully working (and reachable by `FullView.select`) but not given
    #: its own sidebar entry, because Settings now gives the same capability a simpler home.
    hidden = False
    #: Kept for the person who wants the detail underneath; listed under "Advanced" in the sidebar.
    advanced = False
    #: False for a page that fills the window by itself and needs no result area under it.
    shows_output = True

    def __init__(self, view: FullView, parent: tk.Misc):
        self.view = view
        self.window = view.window
        self.frame = tk.Frame(parent, bg=theme.BG)
        self.first: tk.Widget | None = None
        self.build()

    def build(self) -> None:
        raise NotImplementedError

    def focus(self) -> None:
        if self.first is not None:
            self.first.focus_set()

    # helpers

    def label(self, parent: tk.Misc, text: str) -> tk.Label:
        return tk.Label(parent, text=theme.spaced(text), bg=theme.BG, fg=theme.FAINT, font=self.window.fonts.code)

    def note(self, parent: tk.Misc, text: str, *, wrap: int = 800) -> tk.Label:
        return tk.Label(parent, text=text, bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.label,
                        justify="left", anchor="w", wraplength=self.window.px(wrap))

    def entry(self, parent: tk.Misc, value: str = "", *, width: int = 60) -> tuple[ttk.Entry, tk.StringVar]:
        variable = tk.StringVar(master=self.window.root, value=value)
        field = ttk.Entry(parent, textvariable=variable, width=width, font=self.window.fonts.body)
        return field, variable

    def text(self, parent: tk.Misc, value: str, *, lines: int) -> tk.Text:
        field = tk.Text(parent, height=lines, width=40, bg=theme.RAISED, fg=theme.TEXT, font=self.window.fonts.mono,
                        relief="flat", insertbackground=theme.ACCENT_SOFT, selectbackground=theme.ACCENT_DEEP,
                        highlightthickness=1, highlightbackground=theme.LINE_BRIGHT, highlightcolor=theme.ACCENT,
                        padx=self.window.px(8), pady=self.window.px(6), wrap="none", undo=True)
        field.insert("1.0", value)
        return field

    def button(self, parent: tk.Misc, text: str, command: Callable[[], object], *, accent: bool = False) -> ttk.Button:
        control = ttk.Button(parent, text=text, command=command, style="Accent.TButton" if accent else "TButton")
        self.window.run_controls.append(control)
        return control


class StatusPage(Page):
    key, title = "status", "Status"
    advanced = True
    heading = "Status"
    description = ("What RUDRA found on this computer when it started, and where it keeps things. Nothing here "
                   "changes your knowledge.")

    def build(self) -> None:
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(anchor="w", pady=(self.window.px(8), 0))
        for text, command in (("Startup report", "start"), ("This computer", "env"), ("Folders", "paths"),
                              ("Settings file", "config"), ("Version", "version")):
            control = self.button(row, text, lambda c=command: self.run(c), accent=command == "start")
            control.pack(side="left", padx=(0, self.window.px(8)))
            self.first = self.first or control

    def run(self, command: str) -> bool:
        return self.window.run_form(lambda: commands.status(command))


class AskPage(Page):
    key, title = "ask", "Ask"
    heading = "Ask RUDRA"
    description = ("Ask in plain English - what something is, how things relate, or a calculation. RUDRA answers "
                   "from your own documents and shows where each answer came from. It does not guess: when your "
                   "documents do not say, it tells you.")

    def build(self) -> None:
        px = self.window.px
        self.label(self.frame, "Your question").pack(anchor="w", pady=(px(8), px(4)))
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(fill="x")
        self.field, self.question = self.entry(row, width=64)
        self.field.pack(side="left", fill="x", expand=True)
        self.field.bind("<Return>", lambda _event: self.run())
        self.mic = MicButton(self.window, row, self._dictated)
        self.mic.button.pack(side="left", padx=(px(6), 0))
        self.button(row, "Ask", self.run, accent=True).pack(side="left", padx=(px(6), 0))
        self.first = self.field
        self.note(self.frame, "For example:  What is voltage?  ·  Which equations are given for resistance?  ·  "
                              "Calculate the current when V = 10 V and R = 5 Ω  ·  Find the output voltage given "
                              "Vin = 12 V, R1 = 10 kΩ and R2 = 20 kΩ."
                  ).pack(fill="x", pady=(px(10), 0))
        self.options_open = False
        self.options_toggle = tk.Label(self.frame, text="More options  ▸", bg=theme.BG, fg=theme.FAINT,
                                       font=self.window.fonts.label, cursor="hand2", anchor="w")
        self.options_toggle.pack(anchor="w", pady=(px(10), 0))
        self.options_toggle.bind("<Button-1>", lambda _event: self.toggle_options())
        self.options = tk.Frame(self.frame, bg=theme.BG)
        ttk.Checkbutton(self.options, text="Let RUDRA carry out requests to do things on this computer (open an "
                                           "application, create a file ...)", variable=self.window.act
                        ).pack(anchor="w", pady=(px(4), 0))
        ttk.Checkbutton(self.options, text="Go ahead with actions that change something, after I have asked",
                        variable=self.window.confirm_medium).pack(anchor="w", pady=(px(2), 0))
        self.note(self.options, "Without the first box a request to do something is only rehearsed on a simulated "
                                "computer. Every action is checked first, and risky ones are never enabled."
                  ).pack(fill="x", pady=(px(4), 0))

    def toggle_options(self) -> None:
        self.options_open = not self.options_open
        if self.options_open:
            self.options.pack(fill="x", after=self.options_toggle)
        else:
            self.options.pack_forget()
        self.options_toggle.configure(text="More options  ▾" if self.options_open else "More options  ▸")
        self.view.fit()

    def _dictated(self, text: str) -> None:
        self.question.set(text)
        self.field.focus_set()
        self.field.icursor("end")

    def run(self) -> bool:
        return self.window.run_form(lambda: commands.ask(self.question.get(), act=self.window.act.get(),
                                                         confirm=self.window.confirm_medium.get()))


class KnowledgePage(Page):
    """What RUDRA knows: each document's result and every item, with where it came from."""

    key, title = "knowledge", "Knowledge"
    shows_output = False
    heading = "What RUDRA knows"
    description = ("Everything stored from your documents, with the page it came from: concepts, definitions, "
                   "equations, variables and more - and what was found but not stored, and why. Pick a kind, "
                   "then an item to see its source.")

    ALL = "Documents"

    def build(self) -> None:
        from app.inventory import KINDS

        px = self.window.px
        top = tk.Frame(self.frame, bg=theme.BG)
        top.pack(fill="x", pady=(px(6), px(6)))
        self.label(top, "Show").pack(side="left")
        self.kind_names = {self.ALL: ""} | {label: kind for kind, label in KINDS.items()}
        self.kind = tk.StringVar(master=self.window.root, value=self.ALL)
        chooser = ttk.Combobox(top, textvariable=self.kind, values=list(self.kind_names), state="readonly", width=18)
        chooser.pack(side="left", padx=(px(10), px(14)))
        chooser.bind("<<ComboboxSelected>>", lambda _event: self.load())
        self.document = tk.StringVar(master=self.window.root, value="All documents")
        self.documents = ttk.Combobox(top, textvariable=self.document, values=["All documents"], state="readonly",
                                      width=34)
        self.documents.pack(side="left")
        self.documents.bind("<<ComboboxSelected>>", lambda _event: self.load())
        self.button(top, "Refresh", self.load).pack(side="right")
        self.summary = tk.Label(self.frame, bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.label, anchor="w",
                                justify="left", wraplength=px(820))
        self.summary.pack(fill="x", pady=(px(2), px(6)))

        style = ttk.Style(self.window.root)
        style.configure("Knowledge.Treeview", background=theme.OUTPUT_BG, fieldbackground=theme.OUTPUT_BG,
                        foreground=theme.TEXT, bordercolor=theme.LINE, borderwidth=0, rowheight=px(24),
                        font=self.window.fonts.body)
        style.configure("Knowledge.Treeview.Heading", background=theme.RAISED, foreground=theme.MUTED,
                        font=self.window.fonts.label, relief="flat")
        style.map("Knowledge.Treeview", background=[("selected", theme.ACCENT_DEEP)],
                  foreground=[("selected", "#f0f9ff")])
        listing = tk.Frame(self.frame, bg=theme.OUTPUT_BG, highlightthickness=1, highlightbackground=theme.LINE)
        listing.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(listing, columns=("kind", "source", "sure"), style="Knowledge.Treeview",
                                 selectmode="browse", height=9)
        self.tree.heading("#0", text="Item", anchor="w")
        self.tree.heading("kind", text="Kind", anchor="w")
        self.tree.heading("source", text="From", anchor="w")
        self.tree.heading("sure", text="Certainty", anchor="w")
        self.tree.column("#0", width=px(380), stretch=True)
        self.tree.column("kind", width=px(100), stretch=False)
        self.tree.column("source", width=px(190), stretch=False)
        self.tree.column("sure", width=px(140), stretch=False)
        bar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.show_item())
        self.detail = tk.Label(self.frame, bg=theme.BG, fg=theme.TEXT, font=self.window.fonts.body, anchor="nw",
                               justify="left", wraplength=px(820))
        self.detail.pack(fill="x", pady=(px(8), 0))
        self.items: dict[str, dict] = {}
        self._documents: dict[str, str] = {"All documents": ""}
        self._timer: str | None = None
        self.first = chooser

    def focus(self) -> None:
        self.load()
        super().focus()

    def show_document(self, document_id: str, name: str) -> None:
        """Open this page on one document's own summary (after adding it)."""
        self.kind.set(self.ALL)
        self._documents[name] = document_id
        self.document.set(name)

    def load(self) -> bool:
        """Read the inventory for the chosen kind (and document) and list it."""
        if self._timer is not None:
            self.window.root.after_cancel(self._timer)
            self._timer = None
        if self.window.busy:
            self._timer = self.window.root.after(300, self.load)  # try again when the window is free
            return False
        kind = self.kind_names.get(self.kind.get(), "")
        document = self._documents.get(self.document.get(), "")
        return self.window.submit(commands.inventory(kind, document), on_done=lambda result: self._show(result, kind))

    def _show(self, result: CommandResult, kind: str) -> None:
        import json

        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.detail.configure(text="")
        if not result.ok:
            self.summary.configure(text="There is no knowledge yet. Add a document first.", fg=theme.MUTED)
            return
        data = json.loads(result.stdout)
        documents = data.get("documents", [])
        self._documents = {"All documents": ""} | {d["name"]: d["id"] for d in documents}
        self.documents.configure(values=list(self._documents))
        if self.document.get() not in self._documents:
            self.document.set("All documents")
        if not kind:
            chosen = self._documents.get(self.document.get(), "")
            self._list_documents([d for d in documents if not chosen or d["id"] == chosen], data)
            return
        self.tree.heading("#0", text="Item")
        self.tree.heading("kind", text="Kind")
        self.tree.heading("source", text="From")
        self.tree.heading("sure", text="Certainty")
        self._columns(kind=100, source=190, sure=100)
        singular = self.kind.get()[:-1] if self.kind.get().endswith("s") else self.kind.get()
        for item in data.get("items", []):
            where = item.get("document") or ""
            if item.get("page"):
                where += f"  p.{item['page']}"
            sure = {"UNCERTAIN": "uncertain", "NOT_STORED": "not stored", "WARNING": "warning"}.get(
                item.get("certainty", ""), "" if item["kind"] in ("CONCEPT", "RELATIONSHIP") else "as printed")
            row = self.tree.insert("", "end", text=item["title"],
                                   values=("Problem" if item["kind"] == "PROBLEM" else singular, where, sure))
            self.items[row] = item
        count = len(data.get("items", []))
        text = f"{count} shown." if count else f"No {self.kind.get().lower()} are stored."
        if data.get("truncated"):
            text += " The list was cut short - choose one document."
        self.summary.configure(text=text, fg=theme.MUTED)

    def _columns(self, *, kind: int, source: int, sure: int) -> None:
        px = self.window.px
        self.tree.column("kind", width=px(kind), stretch=False)
        self.tree.column("source", width=px(source), stretch=False)
        self.tree.column("sure", width=px(sure), stretch=False)

    def _list_documents(self, documents: list[dict], data: dict) -> None:
        """The per-document summaries: what was stored, linked and not stored."""
        self.tree.heading("#0", text="Document")
        self.tree.heading("kind", text="Type")
        self.tree.heading("source", text="Size")
        self.tree.heading("sure", text="Result")
        self._columns(kind=64, source=96, sure=150)  # a document's lines of text get the room
        totals = data.get("totals") or {}
        counts = totals.get("counts") or {}
        calc = data.get("calculation") or {}
        words = [f"{counts[k]} {label}" for k, label in (
            ("CONCEPT", "concepts"), ("DEFINITION", "definitions"), ("EQUATION", "equations"),
            ("VARIABLE", "variables"), ("RULE", "rules"), ("EXAMPLE", "examples")) if counts.get(k)]
        text = ", ".join(words) if words else "Nothing is stored yet - add a document."
        if calc.get("equations"):
            text += f"  ·  {calc['usable']} of {calc['equations']} equations can be calculated with"
        if totals.get("uncertain"):
            text += f"  ·  {totals['uncertain']} items are marked uncertain"
        self.summary.configure(text=text, fg=theme.MUTED)
        for document in documents:
            pages = (f"{document['pages']} page{'s' if document['pages'] != 1 else ''}" if document.get("pages")
                     else document["source_type"])
            row = self.tree.insert("", "end", text=document["name"], open=True, values=(
                document["source_type"], pages, document["status_words"].split(",")[0]))
            self.items[row] = {"kind": "DOCUMENT", "document": document}
            stored = [self._counted(kind, n) for kind, n in document.get("stored", {}).items()]
            for start in range(0, len(stored), 4):
                self.tree.insert(row, "end", text=("Stored: " if start == 0 else "") + ", ".join(stored[start:start + 4]),
                                 values=("", "", ""))
            if document.get("linked"):
                self.tree.insert(row, "end", text=f"{document['linked']} item(s) were already known from other "
                                                  "evidence (linked, not duplicated)", values=("", "", ""))
            for group in document.get("problems", []):
                label = "Not stored" if group["not_stored"] else "Warning"
                child = self.tree.insert(row, "end", text=f"{label}: {group['count']} {group['short']}",
                                         values=("", "", ""))
                self.items[child] = {"kind": "GROUP", "group": group}

    @staticmethod
    def _counted(kind: str, count: int) -> str:
        word = {"PROPERTY": ("property", "properties"), "CONCEPT": ("concept", "concepts"),
                "DEFINITION": ("definition", "definitions"), "EQUATION": ("equation", "equations"),
                "VARIABLE": ("variable", "variables"), "UNIT": ("unit", "units"), "RULE": ("rule", "rules"),
                "EXAMPLE": ("example", "examples"), "PROCEDURE": ("procedure", "procedures"),
                "RELATIONSHIP": ("relationship", "relationships")}.get(kind, (kind.lower(), kind.lower() + "s"))
        return f"{count} {word[0] if count == 1 else word[1]}"

    def show_item(self) -> None:
        selected = self.tree.selection()
        item = self.items.get(selected[0]) if selected else None
        if item is None:
            self.detail.configure(text="")
            return
        if item["kind"] == "DOCUMENT":
            document = item["document"]
            self.detail.configure(text=f"{document['name']} - {document['status_words']}."
                                       + ("" if document.get("file_present") else
                                          "  (RUDRA's copy of this file has been deleted; the knowledge stays.)"))
            return
        if item["kind"] == "GROUP":
            group = item["group"]
            examples = "\n".join(f"  {'p.' + str(page) + ': ' if page else ''}{text}"
                                 for page, text in group.get("examples", []))
            self.detail.configure(text=group["meaning"] + ("\nFor example:\n" + examples if examples else ""))
            return
        lines = [item.get("statement") or item["title"]]
        if item.get("printed") and item["printed"] != lines[0]:
            lines.append("As printed: " + item["printed"])
        if item.get("about"):
            lines.append("About: " + ", ".join(item["about"]))
        if item.get("document"):
            lines.append(f"From {item['document']}" + (f", page {item['page']}" if item.get("page") else "")
                         + (f" (stated in {item['sources']} places)" if item.get("sources", 1) > 1 else ""))
        if item.get("quote") and item["quote"] != lines[0]:
            lines.append(f"Quoted text: {item['quote']}")
        if item.get("doubt"):
            lines.append("Uncertain: " + item["doubt"] + ".")
        if item.get("calculation") == "usable":
            lines.append("RUDRA can calculate with this equation.")
        elif item.get("calculation"):
            lines.append("For calculation: " + item["calculation"] + ".")
        self.detail.configure(text="\n".join(lines))


class LookupPage(Page):
    key, title = "lookup", "Lookup"
    advanced = True
    heading = "Look something up"
    description = ("Find a concept by its exact name, an item by its identifier (like K-00000001), or any stored "
                   "text by a word. Every result shows where it came from.")

    def build(self) -> None:
        px = self.window.px
        self.mode = tk.StringVar(master=self.window.root, value="name")
        modes = tk.Frame(self.frame, bg=theme.BG)
        modes.pack(anchor="w", pady=(px(8), px(8)))
        for value, text in (("name", "Concept name"), ("identifier", "Identifier"), ("keyword", "Word in the text")):
            ttk.Radiobutton(modes, text=text, value=value, variable=self.mode).pack(side="left", padx=(0, px(16)))
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(fill="x")
        self.field, self.value = self.entry(row, width=50)
        self.field.pack(side="left", fill="x", expand=True)
        self.field.bind("<Return>", lambda _event: self.run())
        self.button(row, "Look up", self.run, accent=True).pack(side="left", padx=(px(8), 0))
        self.first = self.field

    def run(self) -> bool:
        if self.mode.get() != "keyword":
            return self.window.run_form(lambda: commands.lookup(self.mode.get(), self.value.get()))
        try:
            search = commands.lookup("keyword", self.value.get())
        except FormError as exc:
            self.window.show_note(str(exc), tag="warn")
            self.window.set_state("NOT RUN", theme.WARN)
            return False

        def shown(results: list[CommandResult]) -> None:  # the search index was brought up to date first
            self.window.output_touched = True
            self.window.compact.output.show_result(results[-1])
            self.window.full.output.show_result(results[-1])
            self.window.set_state("READY · search done" if results[-1].ok else
                                  f"EXIT {results[-1].exit_code} · {results[-1].meaning}",
                                  theme.OK if results[-1].ok else theme.WARN)

        return self.window.submit_sequence([commands.index(), search], label="search", on_done=shown)


class ProvenancePage(Page):
    key, title = "provenance", "Provenance"
    advanced = True
    heading = "Where did this come from?"
    description = ("Any stored item's sources: the document, the page, the quote and how it was extracted - with the "
                   "quote and the preserved file checked again. When there is none, RUDRA says so and invents no "
                   "citation.")

    def build(self) -> None:
        px = self.window.px
        self.label(self.frame, "Identifier").pack(anchor="w", pady=(px(8), px(4)))
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(fill="x")
        self.field, self.identifier = self.entry(row, width=30)
        self.field.pack(side="left")
        self.field.bind("<Return>", lambda _event: self.run())
        self.button(row, "Trace", self.run, accent=True).pack(side="left", padx=(px(8), 0))
        self.first = self.field
        self.note(self.frame, "An identifier looks like K-00000001 (a piece of knowledge), CPT-00000001 (a concept), "
                              "REL-00000001 (a relationship) or DOC-00000001 (a document). The Knowledge page "
                              "and every answer's sources show them."
                  ).pack(fill="x", pady=(px(12), 0))

    def run(self) -> bool:
        return self.window.run_form(lambda: commands.provenance(self.identifier.get()))


class ImportPage(Page):
    key, title = "import", "Add document"
    heading = "Add a document"
    description = ("Choose one or more PDF, Word, PowerPoint or Excel files, e-books, web pages, text files or "
                   "scanned images. RUDRA reads each one, keeps a private copy, and stores what it states - with "
                   "the page and the exact quote behind every item - so you can ask about it straight away.")

    def build(self) -> None:
        px = self.window.px
        self.pdf = tk.StringVar(master=self.window.root, value="")  # accepts a typed path for keyboard users
        self.selected_documents: list[Path] = []
        self.label(self.frame, "Documents").pack(anchor="w", pady=(px(8), px(4)))
        listing = tk.Frame(self.frame, bg=theme.BG)
        listing.pack(fill="x")
        self.file_list = tk.Listbox(listing, height=5, selectmode="extended", exportselection=False,
                                    bg=theme.RAISED, fg=theme.TEXT, selectbackground=theme.ACCENT_DEEP,
                                    selectforeground=theme.TEXT, font=self.window.fonts.body,
                                    relief="flat", highlightthickness=1, highlightbackground=theme.LINE_BRIGHT)
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=scrollbar.set)
        self.file_list.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="left", fill="y")
        self.file_list.bind("<Delete>", lambda _event: self.remove_selected())
        self.selection_status = self.note(self.frame, "No documents selected.")
        self.selection_status.pack(anchor="w", pady=(px(4), 0))
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(anchor="w", pady=(px(8), 0))
        self.button(row, "Browse…", self.browse).pack(side="left")
        self.button(row, "Remove selected", self.remove_selected).pack(side="left", padx=(px(8), 0))
        self.button(row, "Clear list", self.clear_selection).pack(side="left", padx=(px(8), 0))
        self.first = self.file_list
        self.button(self.frame, "Add document(s)", self.run, accent=True).pack(anchor="w", pady=(px(12), 0))
        self.note(self.frame, "Your original file is never changed. Scanned pages and images are read with "
                              "Windows' own text recognition, on this computer; what it reads is marked uncertain."
                  ).pack(fill="x", pady=(px(10), 0))
        self.manual_open = False
        self.manual_toggle = tk.Label(self.frame, text="Is this the manual of a program?  ▸", bg=theme.BG,
                                      fg=theme.FAINT, font=self.window.fonts.label, cursor="hand2", anchor="w")
        self.manual_toggle.pack(anchor="w", pady=(px(10), 0))
        self.manual_toggle.bind("<Button-1>", lambda _event: self.toggle_manual())
        self.manual_box = tk.Frame(self.frame, bg=theme.BG)
        self.label(self.manual_box, "Name of the program (for example MATLAB)").pack(anchor="w", pady=(px(4), px(4)))
        manual_field, self.manual = self.entry(self.manual_box, width=30)
        manual_field.pack(anchor="w")
        self.note(self.manual_box, "RUDRA then also learns the program's menus, commands and shortcuts from it."
                  ).pack(fill="x", pady=(px(4), 0))

    def toggle_manual(self) -> None:
        self.manual_open = not self.manual_open
        if self.manual_open:
            self.manual_box.pack(fill="x", after=self.manual_toggle)
        else:
            self.manual_box.pack_forget()
        self.manual_toggle.configure(text="Is this the manual of a program?  " + ("▾" if self.manual_open else "▸"))
        self.view.fit()

    def browse(self) -> None:
        from app.documents.formats import SUPPORTED_EXTENSIONS

        patterns = " ".join(f"*{extension}" for extension in SUPPORTED_EXTENSIONS)
        chosen = filedialog.askopenfilenames(parent=self.window.root, title="Choose documents",
                                             filetypes=(("Supported documents", patterns),
                                                        ("PDF documents", "*.pdf"), ("All files", "*.*")))
        if chosen:
            self.add_files(chosen)

    def add_files(self, paths) -> None:
        """Append selected paths once each, preserving the chooser's order."""
        known = {str(path.resolve()).casefold() for path in self.selected_documents}
        for value in paths:
            path = Path(value).expanduser()
            key = str(path.resolve()).casefold()
            if key not in known:
                self.selected_documents.append(path)
                known.add(key)
        self._render_selection()

    def remove_selected(self) -> None:
        for index in reversed(self.file_list.curselection()):
            del self.selected_documents[index]
        self._render_selection()

    def clear_selection(self) -> None:
        self.selected_documents.clear()
        self.pdf.set("")
        self._render_selection()

    def _render_selection(self) -> None:
        self.file_list.delete(0, "end")
        for path in self.selected_documents:
            self.file_list.insert("end", str(path))
        count = len(self.selected_documents)
        self.selection_status.configure(
            text=(f"{count} document{'s' if count != 1 else ''} selected."
                  if count else "No documents selected.")
        )

    def run(self) -> bool:
        """Adding a document is one operation from here: read it and store what it states
        (`extract`), make it searchable (`index`), then list what was found
        (`inventory`) so the person is told what was stored and what was not."""
        paths = list(self.selected_documents)
        if not paths and self.pdf.get().strip():  # a path may be typed instead of chosen
            paths = [Path(self.pdf.get().strip())]
        if not paths:
            self.window.show_note("Choose one or more documents first.", tag="warn")
            self.window.set_state("NOT RUN", theme.WARN)
            return False
        manual = self.manual.get() if self.manual_open else ""
        if len(paths) > 1:
            return self._run_batch(paths, manual)
        try:
            extract_argv = commands.extract(str(paths[0]), manual)
        except FormError as exc:
            self.window.show_note(str(exc), tag="warn")
            self.window.set_state("NOT RUN", theme.WARN)
            return False
        self.window.pending_import_name = paths[0].name
        return self.window.submit_sequence([extract_argv, commands.index(), commands.inventory()], label="extract",
                                           on_done=self._added, show=extract_argv)

    def _run_batch(self, paths: list[Path], manual: str) -> bool:
        try:
            argvs = [(path, commands.extract(str(path), manual)) for path in paths]
        except FormError as exc:
            self.window.show_note(str(exc), tag="warn")
            self.window.set_state("NOT RUN", theme.WARN)
            return False

        def work() -> BatchImportResult:
            documents: list[tuple[Path, CommandResult]] = []
            for path, argv in argvs:
                documents.append((path, commands.run(argv, self.window.project_root)))
            shared: list[CommandResult] = []
            if any(result.ok for _path, result in documents):
                indexed = commands.run(commands.index(), self.window.project_root)
                shared.append(indexed)
                if indexed.ok:
                    shared.append(commands.run(commands.inventory(), self.window.project_root))
            return BatchImportResult(tuple(documents), tuple(shared))

        def done(result: object, error: BaseException | None) -> None:
            if error is not None:
                self.window.show_note("RUDRA hit an unexpected error while adding the documents.\n\n" + str(error),
                                      tag="err")
                return
            assert isinstance(result, BatchImportResult)
            failed_paths = [path for path, extracted in result.documents if not extracted.ok]
            self.selected_documents = failed_paths
            self._render_selection()
            name_counts: dict[str, int] = {}
            for path, _extracted in result.documents:
                name_counts[path.name.casefold()] = name_counts.get(path.name.casefold(), 0) + 1
            reports = [(str(path) if name_counts[path.name.casefold()] > 1 else path.name, extracted)
                       for path, extracted in result.documents]
            self.window.compact.output.show_extract_batch(reports, list(result.shared))
            self.window.full.output.show_extract_batch(reports, list(result.shared))
            self.window.last = (result.shared[-1] if result.shared else result.documents[-1][1])
            successes = sum(extracted.ok for _path, extracted in result.documents)
            total = len(result.documents)
            if successes != total:
                self.window.set_state(f"PARTIAL · added {successes} of {total}", theme.WARN)
            elif result.shared and not result.shared[0].ok:
                self.window.set_state("ADDED · search index needs attention", theme.WARN)
            else:
                self.window.set_state(f"READY · added {total} documents", theme.OK)
            self.window.output_touched = True

        return self.window.run_task("adding documents", work, done)

    def _added(self, results: list[CommandResult]) -> None:
        self.window.output_touched = True
        self.window.compact.output.show_extract_summary(results)
        self.window.full.output.show_extract_summary(results)
        self.window.pending_import_name = None
        text, good = commands.finished_state(results[0])
        self.window.set_state(text, theme.OK if good else theme.WARN)


class CommandPage(Page):
    key, title = "command", "Command"
    advanced = True
    heading = "Commands"
    description = ("For people who want the detail: everything RUDRA's command line can do, typed here exactly as "
                   "it would be in a terminal. A command that changes your knowledge, reaches the Internet or acts "
                   "on this computer is put to you first.")

    def build(self) -> None:
        px = self.window.px
        self.label(self.frame, "Command").pack(anchor="w", pady=(px(8), px(4)))
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(fill="x")
        self.field, self.line = self.entry(row, 'interpret "What is resistance?"', width=70)
        self.field.pack(side="left", fill="x", expand=True)
        self.field.bind("<Return>", lambda _event: self.run())
        self.button(row, "Run", self.run, accent=True).pack(side="left", padx=(px(8), 0))
        self.first = self.field
        self.note(self.frame, "Commands: " + "  ·  ".join(commands.command_names())
                  + "\nType help for every command and flag.").pack(fill="x", pady=(px(12), 0))

    def run(self) -> bool:
        return self.window.run_form(lambda: commands.typed(self.line.get()), approve=True)


class HelpPage(Page):
    key, title = "help", "Help"
    advanced = True
    heading = "Help"
    description = ("How to use RUDRA, and what it can and cannot do. Updates, voice input, backup and the "
                   "uninstaller are on the Settings page.")

    def build(self) -> None:
        px = self.window.px
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(anchor="w", pady=(px(8), 0))
        reference = self.button(row, "Command reference", self.reference, accent=True)
        reference.pack(side="left")
        self.first = reference
        for title in commands.DOCUMENTS:
            ttk.Button(row, text=title, command=lambda t=title: self.document(t)).pack(side="left", padx=(px(8), 0))
        self.note(self.frame, f"RUDRA {VERSION}. From a terminal the same commands run as RUDRA-CLI.exe <command>."
                  ).pack(fill="x", pady=(px(14), 0))

    def reference(self) -> bool:
        return self.window.run_form(lambda: list(commands.HELP_ARGUMENTS))

    def document(self, title: str) -> None:
        self.view.output.write([(f"{commands.DOCUMENTS[title]}\n\n", "cmd"), (commands.document(title), "out")])


class BackupPage(Page):
    key, title = "backup", "Backup"
    hidden = True  # Settings offers Export/Import with one click each; this page's own
    # methods do the work either way, so nothing here is duplicated.
    heading = "Back up and restore your knowledge"
    description = ("A backup holds your whole knowledge base - everything stored and the documents it came from - "
                   "in one .rudrabackup file you can keep on a USB drive or another disk. Restoring puts such a "
                   "file back here or on another computer, after checking it completely.")

    def build(self) -> None:
        px = self.window.px
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(anchor="w", pady=(px(8), 0))
        export = self.button(row, "Back up your knowledge…", self.export, accent=True)
        export.pack(side="left")
        self.button(row, "Restore from a backup…", self.restore).pack(side="left", padx=(px(8), 0))
        self.first = export
        self.note(self.frame, "Restoring replaces the knowledge base in this installation. RUDRA asks first, and "
                              "keeps the current one in its backups folder rather than deleting it. A backup made "
                              "by a newer version of RUDRA is refused until RUDRA is updated.").pack(
            fill="x", pady=(px(12), 0))
        # Dialogs, replaceable for tests: (title, initial name) -> path, and approval questions.
        self.ask_save: Callable[[str], str] = lambda initial: filedialog.asksaveasfilename(
            parent=self.window.root, title="Back up your knowledge", initialfile=initial, defaultextension=SUFFIX,
            filetypes=(("RUDRA backup", f"*{SUFFIX}"), ("All files", "*.*")))
        self.ask_open: Callable[[], str] = lambda: filedialog.askopenfilename(
            parent=self.window.root, title="Restore from a backup",
            filetypes=(("RUDRA backup", f"*{SUFFIX}"), ("All files", "*.*")))
        self.ask_yes: Callable[[str, str], bool] = lambda title, message: messagebox.askyesno(
            title, message, icon="warning", parent=self.window.root)

    def show(self, text: str, tag: str = "out") -> None:
        self.window.show_note(text, tag=tag)

    def export(self) -> bool:
        chosen = self.ask_save(f"RUDRA-knowledge-{date.today():%Y-%m-%d}{SUFFIX}")
        if not chosen:
            return False
        destination = Path(chosen)

        def work() -> tuple[ExportReport, object]:
            report = export_knowledge(self.window.layout(), destination, app_version=VERSION, overwrite=True)
            return report, inspect_backup(report.path)  # read the finished file back before saying it worked

        def done(found: object, error: BaseException | None) -> None:
            if error is not None:
                self.show(f"The backup could not be made. Nothing was changed.\n\n{error}", "err")
                return
            report, readback = found  # type: ignore[misc]
            assert isinstance(report, ExportReport)
            same = readback.counts.get("knowledge_object", 0) == report.counts.get("knowledge_object", 0)
            self.show(f"Your knowledge is backed up.\n\nFile       : {report.path}\nSize       : "
                      f"{report.size / 1024 ** 2:.2f} MB\nDocuments  : {report.documents}\n"
                      f"Knowledge  : {report.counts.get('knowledge_object', 0)} item(s), "
                      f"{report.counts.get('concept', 0)} concept(s)\nChecked     : the file was read back and "
                      + ("holds the same knowledge." if same else "DOES NOT MATCH - make the backup again.")
                      + "\n\nCopy this file to a USB drive or another disk. Restore it with Settings > "
                        "Restore from a backup on any computer with RUDRA.", "out" if same else "err")

        return self.window.run_task("export", work, done)

    def restore(self) -> bool:
        chosen = self.ask_open()
        if not chosen:
            return False
        source = Path(chosen)

        def checked(contents: object, error: BaseException | None) -> None:
            if error is not None:
                self.show(f"This backup cannot be restored. Nothing was changed.\n\n{error}", "err")
                return
            layout = self.window.layout()
            existing = knowledge_base_exists(layout)
            summary = (f"Backup made {contents.created_at} by RUDRA {contents.app_version or 'unknown'}\n"
                       f"{contents.counts.get('knowledge_object', 0)} knowledge item(s), "
                       f"{contents.document_count} document(s).")
            if existing:
                question = (f"{summary}\n\nThis installation already has a knowledge base. Restoring REPLACES it "
                            "with the backup. The current knowledge base is moved to the backups folder, not "
                            "deleted.\n\nReplace the current knowledge base?")
            else:
                question = f"{summary}\n\nRestore this backup?"
            if not self.ask_yes("Restore from a backup", question):
                self.show("Restore cancelled. Nothing was changed.", "meta")
                return

            def work() -> RestoreReport:
                return restore_knowledge(layout, source, replace_existing=existing)

            self.window.run_task("import", work, restored)

        def restored(report: object, error: BaseException | None) -> None:
            if error is not None:
                self.show(f"The backup was not restored.\n\n{error}", "err")
                return
            assert isinstance(report, RestoreReport)
            checks = report.verified
            lines = [
                "Your knowledge is restored and checked.", "",
                f"Integrity   : {checks.get('integrity')}",
                f"Schema      : version {checks.get('schema_version')}"
                + (f" (migrated from {report.migrated_from})" if report.migrated_from else ""),
                f"Knowledge   : {checks.get('knowledge_objects')} item(s), {checks.get('concepts')} concept(s), "
                f"{checks.get('provenance_links')} source link(s)",
                f"Documents   : {checks.get('files_verified')} file(s) verified against their fingerprints",
            ]
            if report.previous_saved_to is not None:
                lines.append(f"Previous    : kept in {report.previous_saved_to}")
            lines += ["", "Your knowledge is back. Ask a question, or open the Knowledge page to see it."]
            self.show("\n".join(lines))

        return self.window.run_task("checking backup", lambda: inspect_backup(source), checked)


class AiPage(Page):
    key, title = "ai", "AI (optional)"
    hidden = True  # reached from Settings > Optional AI assistance; fully working either way.
    heading = "Optional AI assistance"
    description = ("RUDRA answers from your own documents and works without AI. If you choose, an external "
                   "provider can help with language: interpreting a question RUDRA's own grammar does not "
                   "recognise, and wording an explanation of the statements RUDRA found. It is never a source "
                   "of knowledge, and it is off until you turn it on here.")

    def build(self) -> None:
        px = self.window.px
        self.status = tk.Label(self.frame, bg=theme.BG, fg=theme.TEXT, font=self.window.fonts.body, anchor="w",
                               justify="left")
        self.status.pack(fill="x", pady=(px(6), px(8)))
        grid = tk.Frame(self.frame, bg=theme.BG)
        grid.pack(fill="x")
        self.label(grid, "Provider").grid(row=0, column=0, sticky="w", pady=px(3))
        names = [p.name for p in PROVIDERS.values()]
        self.provider = tk.StringVar(master=self.window.root, value=names[0])
        chooser = ttk.Combobox(grid, textvariable=self.provider, values=names, state="readonly", width=24)
        chooser.grid(row=0, column=1, sticky="w", padx=(px(10), px(16)))
        self.label(grid, "API key").grid(row=1, column=0, sticky="w", pady=px(3))
        self.key_field, self.api_key = self.entry(grid, width=36)
        self.key_field.configure(show="•")
        self.key_field.grid(row=1, column=1, sticky="w", padx=(px(10), px(8)))
        keys = tk.Frame(grid, bg=theme.BG)
        keys.grid(row=1, column=2, sticky="w")
        ttk.Button(keys, text="Save key", command=self.save_key).pack(side="left")
        ttk.Button(keys, text="Remove key", command=self.remove_key).pack(side="left", padx=(px(6), 0))
        self.label(grid, "Model").grid(row=2, column=0, sticky="w", pady=px(3))
        self.model = tk.StringVar(master=self.window.root, value=self.window.ai().model or "")
        self.models = ttk.Combobox(grid, textvariable=self.model, values=(), width=34)
        self.models.grid(row=2, column=1, sticky="w", padx=(px(10), px(8)))
        self.button(grid, "Load models", self.load_models).grid(row=2, column=2, sticky="w")
        buttons = tk.Frame(self.frame, bg=theme.BG)
        buttons.pack(anchor="w", pady=(px(10), 0))
        self.first = self.button(buttons, "Turn on AI assistance…", self.turn_on, accent=True)
        self.first.pack(side="left")
        ttk.Button(buttons, text="Turn off", command=self.turn_off).pack(side="left", padx=(px(8), 0))
        links = tk.Frame(self.frame, bg=theme.BG)
        links.pack(anchor="w", pady=(px(8), 0))
        ttk.Button(links, text="Get an API key…", command=lambda: self.window.open_url(self.chosen().key_page)).pack(
            side="left")
        ttk.Button(links, text="Provider's data policy…",
                   command=lambda: self.window.open_url(self.chosen().privacy_page)).pack(side="left", padx=(px(6), 0))
        self.note(self.frame, "Your API key is kept by the Windows Credential Manager, never in RUDRA's files, logs "
                              "or backups. What is sent: for 'Interpret', only the question; for 'Explain', the "
                              "question and the statements shown in the answer. No file, file name, path, "
                              "identifier or other document is sent. An Internet connection is needed; the "
                              "provider's own terms apply to what it receives.").pack(fill="x", pady=(px(12), 0))
        self.ask_yes: Callable[[str, str], bool] = lambda title, message: messagebox.askyesno(
            title, message, icon="warning", parent=self.window.root)
        self.show_status()

    def chosen(self):
        return next(p for p in PROVIDERS.values() if p.name == self.provider.get())

    def show_status(self) -> None:
        settings = self.window.ai()
        if settings.active and settings.provider in PROVIDERS:
            provider = PROVIDERS[settings.provider]
            self.provider.set(provider.name)
            key = "key stored" if ai_credentials.has_key(provider.key) else "NO KEY STORED"
            self.status.configure(text=f"On: {provider.name}, model {settings.model} ({key}).", fg=theme.OK)
        else:
            self.status.configure(text="Off. RUDRA uses only its own, local processing.", fg=theme.MUTED)

    def save_key(self) -> None:
        provider = self.chosen()
        try:
            ai_credentials.store_key(provider.key, self.api_key.get())
        except ai_credentials.CredentialError as exc:
            self.window.show_note(str(exc), tag="warn")
            return
        finally:
            self.api_key.set("")
        self.window.show_note(f"The API key for {provider.name} is stored in the Windows Credential Manager.",
                              tag="meta")
        self.show_status()

    def remove_key(self) -> None:
        provider = self.chosen()
        removed = ai_credentials.delete_key(provider.key)
        self.window.show_note(f"{'Removed' if removed else 'There was no'} API key for {provider.name}.", tag="meta")
        self.show_status()

    def load_models(self) -> bool:
        provider = self.chosen()

        def work():
            key = ai_credentials.read_key(provider.key)
            if not key:
                raise ProviderError(f"Save an API key for {provider.name} first.")
            return list_models(provider, key)

        def done(result, error) -> None:
            if error is not None:
                self.window.show_note(f"The model list could not be loaded: {error}", tag="warn")
                return
            self.models.configure(values=tuple(result))
            self.window.show_note(f"{len(result)} model(s) available to your key at {provider.name}. Choose one.",
                                  tag="meta")

        return self.window.run_task("loading models", work, done)

    def turn_on(self) -> bool:
        provider = self.chosen()
        model = self.model.get().strip()
        if not model:
            self.window.show_note("Choose a model first (Load models lists the ones your key may use).", tag="warn")
            return False
        if not ai_credentials.has_key(provider.key):
            self.window.show_note(f"Save your API key for {provider.name} first.", tag="warn")
            return False
        consent = self.ask_yes(
            "Turn on AI assistance",
            f"When you press 'Interpret with {provider.name}' or 'Explain with {provider.name}' on an answer, "
            f"RUDRA sends text to {provider.name} over the Internet:\n\n"
            "- Interpret: the question you typed.\n"
            "- Explain: the question and the statements shown in the answer.\n\n"
            "Nothing else is sent: no documents, file names, paths, identifiers, database or other personal data. "
            "Nothing is sent automatically. The provider's terms apply to what it receives, and it may charge "
            "your account.\n\nYour documents remain the only source of RUDRA's answers; the provider's wording is "
            "shown as an explanation, never as a source.\n\nTurn on AI assistance?")
        if not consent:
            self.window.show_note("AI assistance stays off.", tag="meta")
            return False
        ai_settings.enable(self.window.project_root / "config", provider.key, model, consent=True)
        self.show_status()
        self.window.show_note(f"AI assistance is on: {provider.name}, model {model}.", tag="meta")
        return True

    def turn_off(self) -> None:
        ai_settings.disable(self.window.project_root / "config")
        self.show_status()
        self.window.show_note("AI assistance is off. RUDRA uses only its own, local processing.", tag="meta")


class SettingsPage(Page):
    """The home for maintenance: updates, voice, backup, restore, optional AI and uninstalling,
    in one place - not scattered as separate top-level pages.

    Backup and AI keep their own, fully working pages (`BackupPage`, `AiPage`); this page
    either calls their methods directly (Back up / Restore, one click each) or opens them with
    `view.select` for the few things that need more than one field (choosing an AI provider
    and model). Nothing here is a second implementation of either.
    """

    key, title = "settings", "Settings"
    heading = "Settings"
    description = "Updates, voice input, backing up and restoring your knowledge, optional AI assistance and uninstalling."

    def build(self) -> None:
        px = self.window.px
        grid = tk.Frame(self.frame, bg=theme.BG)
        grid.pack(fill="x", pady=(px(8), 0))
        grid.grid_columnconfigure(1, weight=1)
        self._row = 0

        def section(name: str) -> tk.Frame:
            """A label on the left; the controls (and a note under them) on the right."""
            if self._row:
                tk.Frame(grid, bg=theme.LINE, height=1).grid(row=self._row, column=0, columnspan=2, sticky="ew",
                                                              pady=(px(6), px(6)))
                self._row += 1
            tk.Label(grid, text=theme.spaced(name), bg=theme.BG, fg=theme.FAINT, font=self.window.fonts.code,
                     anchor="nw", width=18, justify="left").grid(row=self._row, column=0, sticky="nw",
                                                                  pady=(px(5), 0))
            holder = tk.Frame(grid, bg=theme.BG)
            holder.grid(row=self._row, column=1, sticky="ew")
            self._row += 1
            return holder

        def line(holder: tk.Frame) -> tk.Frame:
            row = tk.Frame(holder, bg=theme.BG)
            row.pack(fill="x", pady=(px(2), 0))
            return row

        updates = section("Updates")
        row = line(updates)
        check = self.button(row, "Check for updates", self.check_updates, accent=True)
        check.pack(side="left")
        self.first = check
        self.automatic = tk.BooleanVar(master=self.window.root, value=self._automatic_updates())
        ttk.Checkbutton(row, text="Check automatically (at most once a day)", variable=self.automatic,
                        command=self.set_automatic_updates).pack(side="left", padx=(px(12), 0))

        voice = section("Voice")
        row = line(voice)
        self.mic = MicButton(self.window, row, self._heard, idle="Test microphone")
        self.mic.button.pack(side="left")
        self.mic_status = tk.Label(row, bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.label, anchor="w")
        self.mic_status.pack(side="left", padx=(px(10), 0))
        self.note(voice, "Click, speak, and stop talking (or click again) when you are done. Speech is recognized on "
                         "this computer by an offline model - nothing is recorded or sent anywhere. The same Speak "
                         "button sits beside the question on the Ask page.", wrap=560).pack(fill="x", pady=(px(2), 0))

        row = line(voice)
        tk.Label(row, text="Words to spell as written", bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.label
                 ).pack(side="left")
        self.words_field, self.words = self.entry(row, "", width=34)
        self.words_field.pack(side="left", padx=(px(8), 0))
        self.button(row, "Save", self.save_words).pack(side="left", padx=(px(8), 0))
        self.words_status = tk.Label(row, bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.label, anchor="w")
        self.words_status.pack(side="left", padx=(px(10), 0))
        self.note(voice, "Names and terms speech often gets wrong, separated by commas (for example: Kaushik, "
                         "Anantham). RUDRA tells the recognizer how you write them. They stay on this computer.",
                  wrap=560).pack(fill="x", pady=(px(2), 0))

        data = section("Backup")
        row = line(data)
        self.button(row, "Back up your knowledge", self.backup, accent=True).pack(side="left")
        self.button(row, "Restore from a backup", self.restore).pack(side="left", padx=(px(8), 0))
        self.note(data, "A backup is one file holding everything you have added - keep it on a USB drive or "
                        "another disk. Restoring asks first and keeps what is here now in the backups folder "
                        "instead of deleting it.", wrap=560).pack(fill="x", pady=(px(2), 0))

        ai = section("AI assistance")
        row = line(ai)
        self.ai_status = tk.Label(row, bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.body, anchor="w")
        self.ai_status.pack(side="left")
        self.button(row, "Manage…", self.open_ai).pack(side="left", padx=(px(12), 0))
        self.note(ai, "Optional. RUDRA works fully without it; it is only used if you turn it on with your own key.",
                  wrap=560).pack(fill="x", pady=(px(2), 0))

        remove = section("Uninstall")
        row = line(remove)
        ttk.Button(row, text="Uninstall RUDRA…", command=self.uninstall).pack(side="left")
        self.note(remove, "Runs Windows' own uninstaller for RUDRA. Your knowledge is kept in your user profile "
                          "either way.", wrap=560
                  ).pack(fill="x", pady=(px(2), 0))

        # Dialogs, replaceable for tests.
        self.ask_yes: Callable[[str, str], bool] = lambda title, message: messagebox.askyesno(
            title, message, icon="warning", parent=self.window.root)
        self.show_info: Callable[[str, str], None] = lambda title, message: messagebox.showinfo(
            title, message, parent=self.window.root)
        self.show_error: Callable[[str, str], None] = lambda title, message: messagebox.showerror(
            title, message, parent=self.window.root)
        self.start_uninstaller: Callable[[str], object] = subprocess.Popen
        self.quit: Callable[[], object] = self.window.root.destroy
        self.refresh()

    def focus(self) -> None:
        self.refresh()
        super().focus()

    def refresh(self) -> None:
        self.load_words()
        settings = self.window.ai()
        if settings.active and settings.provider in PROVIDERS:
            self.ai_status.configure(text=f"On: {PROVIDERS[settings.provider].name}.", fg=theme.OK)
        else:
            self.ai_status.configure(text="Off. RUDRA uses only its own, local processing.", fg=theme.MUTED)

    # ---- Application

    def _automatic_updates(self) -> bool:
        try:
            return self.window.update_checker().enabled
        except OSError:
            return True

    def set_automatic_updates(self) -> None:
        self.window.update_checker().set_enabled(self.automatic.get())

    def check_updates(self) -> None:
        self.window.check_for_updates(manual=True)

    # ---- Voice / input

    def load_words(self) -> None:
        from app.voice import words as voice_words

        self.words.set(", ".join(voice_words.load(self.window.project_root / "config")))

    def save_words(self) -> bool:
        """Keep the words the person typed (tidied) for the recognizer to spell as written."""
        from app.voice import words as voice_words

        try:
            kept = voice_words.save(self.window.project_root / "config", self.words.get())
        except OSError as exc:
            self.words_status.configure(text=f"Could not save: {exc}", fg=theme.WARN)
            return False
        self.words.set(", ".join(kept))
        self.words_status.configure(text=f"Saved {len(kept)} word{'s' if len(kept) != 1 else ''}." if kept
                                    else "No words kept.", fg=theme.OK)
        return True

    def _heard(self, text: str) -> None:
        self.mic_status.configure(text=f'Heard: "{text}"', fg=theme.OK)

    # ---- Data (delegates to BackupPage's own, fully tested methods)

    def backup(self) -> bool:
        return self.view.pages["backup"].export()

    def restore(self) -> bool:
        return self.view.pages["backup"].restore()

    # ---- Optional AI

    def open_ai(self) -> None:
        self.view.select("ai")

    # ---- Application management

    def uninstall(self) -> None:
        command = commands.find_uninstaller()
        if command is None:
            self.show_info("Uninstall RUDRA",
                           "RUDRA does not see an installation to uninstall here - this may be a copy run from "
                           "source. If RUDRA was installed with its installer, use Windows Settings > Apps.")
            return
        if not self.ask_yes("Uninstall RUDRA",
                            "This starts Windows' own uninstaller for RUDRA - the same one Settings > Apps would "
                            "use - and RUDRA will close.\n\nYour knowledge base lives in your user profile and is "
                            "not removed by uninstalling the program.\n\nContinue?"):
            return
        try:
            self.start_uninstaller(command)
        except OSError as exc:
            self.show_error("Uninstall RUDRA", f"RUDRA could not start the uninstaller.\n\n{exc}")
            return
        self.quit()


PAGES = (AskPage, ImportPage, KnowledgePage, SettingsPage, StatusPage, LookupPage, ProvenancePage, CommandPage,
         HelpPage, BackupPage, AiPage)
