"""RUDRA's desktop window (ADR 0057): a compact assistant that expands to the full interface.

Master specification section 139: RUDRA starts as a small assistant window - a question
or quick command, status, short responses - and becomes the full interface only when the
user asks for it. Section 140: the window is an interface to the architecture and must
not become the architecture. Everything the window runs is a command line, run by
`commands.run` through the command line's own entry point on a worker thread. This module
only lays out the forms, shows the results and puts writes and actions to the user first.
"""

from __future__ import annotations

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
from app.ui.gui.commands import Approval, CommandResult, FormError
from app.version import VERSION

MARK_SIZES = (32, 48, 64, 96, 128, 256)
POLL_MS = 40


@dataclass(frozen=True)
class VoiceResult:
    """One dictation attempt: the recognized text, or why there is none.

    `error` is `""` when the user cancelled (nothing is shown for that - it was their own
    choice), `None` when `text` is the whole story, and a message otherwise.
    """

    text: str
    error: str | None


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
        if autostart:
            root.after(250, lambda: self.submit(commands.status("start")))
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
        self.compact.show_result(result)
        self.full.show_result(result)
        self.pending_import_name = None
        if result.ok:
            self.set_state(f"READY · {result.argv[0] if result.argv else 'start'} done", theme.OK)
        else:
            self.set_state(f"EXIT {result.exit_code} · {result.meaning}", theme.WARN)
        if self.on_result is not None:
            self.on_result(result)

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

    def listen(self, done: Callable[[VoiceResult], None], *, seconds: int = 12) -> Callable[[], None]:
        """Capture one spoken utterance and hand its text (or a friendly reason it has none)
        to `done`, on this thread. Returns a `stop()` the caller may invoke to cancel early;
        calling it after `done` has already run does nothing.

        Nothing is carried out and nothing is kept: this is dictation into a text field, the
        same question or command the user could have typed (master specification: a user can
        edit the recognized words before anything is sent to RUDRA).
        """
        from app.voice import SpeechCancelled, SpeechUnavailable, listen as voice_listen

        cancel = threading.Event()

        def work() -> VoiceResult:
            try:
                transcript = voice_listen(seconds, cancel=cancel)
            except SpeechCancelled:
                return VoiceResult(text="", error="")
            except SpeechUnavailable as exc:
                return VoiceResult(text="", error=str(exc))
            if not transcript.text:
                return VoiceResult(text="", error="RUDRA did not hear anything. Try again and speak soon after "
                                                   "pressing Speak.")
            return VoiceResult(text=transcript.text, error=None)

        def finished(result: object, error: BaseException | None) -> None:
            if error is not None:
                done(VoiceResult(text="", error=f"RUDRA could not use the microphone: {error}"))
                return
            assert isinstance(result, VoiceResult)
            done(result)

        self.run_task("listening", work, finished)
        return cancel.set

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
        if not compact:
            across = ttk.Scrollbar(self.frame, orient="horizontal", command=self.text.xview)
            self.text.configure(xscrollcommand=across.set)
            across.pack(side="bottom", fill="x")
        down.pack(side="right", fill="y")
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
        self._extract_result: CommandResult | None = None
        self._extract_name: str | None = None
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
        self._extract_result = None
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
        self.write([(f"› {commands.display_command(argv)}\n\n", "cmd"), ("Running…", "meta")])

    def show_result(self, result: CommandResult) -> None:
        if commands.is_answer_command(result.argv):
            document = answerview.parse_answer(result.stdout)
            if document is not None and document.parts:
                self.show_answer(document)
                return
        if commands.is_extract_command(result.argv):
            self.show_extract_summary(result)
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

    def show_extract_summary(self, result: CommandResult) -> None:
        """"Added to your knowledge base.", not a dump of `extract`'s own technical report -
        that stays one click away behind Details, exactly as RUDRA produced it."""
        self.answer = None
        self._extract_result = result
        self._extract_name = self.window.pending_import_name  # read only: both views need it
        self._extract_open = False
        self._render_extract()

    def _render_extract(self) -> None:
        result = self._extract_result
        if result is None:
            return
        summary = commands.extract_summary(result)
        self._clear_embedded()
        self.text.configure(state="normal", wrap="word")
        self.text.delete("1.0", "end")
        if self._extract_name:
            self.text.insert("end", self._extract_name + "\n", "a_request")
        self.text.insert("end", summary.headline + "\n", "a_line" if summary.ok else "a_warn")
        extra = []
        if summary.pages:
            extra.append(summary.pages)
        if summary.ocr_count:
            extra.append(f"{summary.ocr_count} page(s) read by OCR - that text is marked uncertain")
        if summary.issue_total:
            extra.append(f"{summary.issue_total} extraction warning(s)")
        if extra:
            self.text.insert("end", " · ".join(extra) + "\n", "a_extra")
        button = ttk.Button(self.text, text="Hide details" if self._extract_open else "Details",
                            command=self.toggle_extract_details)
        self.embedded.append(button)
        self.text.window_create("end", window=button, padx=self.window.px(2), pady=self.window.px(6))
        self.text.insert("end", "\n", "a_line")
        if self._extract_open:
            self.text.insert("end", f"› {commands.display_command(result.argv)}\n\n", "cmd")
            for line in result.stdout.splitlines(keepends=True):
                self.text.insert("end", line, "answer" if commands.is_answer_line(line) else "out")
            if result.stderr:
                self.text.insert("end", ("\n" if result.stdout and not result.stdout.endswith("\n") else "")
                                 + result.stderr, "log" if result.ok else "err")
            self.text.insert("end", f"\n{'ok' if result.ok else f'exit {result.exit_code}'} · {result.meaning} · "
                             f"{result.seconds:.2f} s\n", "meta")
        self.text.configure(state="disabled")
        self.text.see("1.0")

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
            for line in part.lines:
                self._answer_line(line)
            if part.has_conflict:
                self.text.insert("end", "Sources disagree on this. View Sources shows each claim.\n", "a_warn")
            for line in part.uncertain:
                self.text.insert("end", f"Uncertain: {line}\n", "a_warn")
            self._ai_block(part, document.request)
            for title, values in part.extras:
                self.text.insert("end", theme.spaced(title) + "\n", "a_title")
                for value in values:
                    self.text.insert("end", value + "\n", "a_extra")
            if not part.details.empty:
                self._sources_button(part)
                if part.number in self.expanded:
                    self._sources(part)
        self.text.configure(state="disabled")
        self.text.yview_moveto(view)

    def _answer_line(self, line: str) -> None:
        found = mathrender.formula_parts(line)
        if found is None:
            self.text.insert("end", line + "\n", "a_line")
            return
        label, formula = found
        if label:
            self.text.insert("end", label, "a_line")
        self.text.window_create("end", window=self.formula(formula), align="center", padx=self.window.px(4),
                                pady=self.window.px(2))
        self.text.insert("end", "\n", "a_line")

    def formula(self, source: str, *, size: float = 17) -> tk.Canvas:
        """A typeset formula, drawn on a canvas that sits in the text like a word."""
        if self._metrics is None:
            self._metrics = mathrender.TkMetrics(self.text)
        # A long equation breaks into lines that fit the answer area, as a textbook sets it.
        available = self.text.winfo_width() - self.window.px(40)
        box = mathrender.layout(mathrender.parse(source), self.window.px(size), self._metrics,
                                max_width=available if available > self.window.px(200) else None)
        margin = self.window.px(3)
        # Whole pixels, rounded up: the layout measures in fractions, and Tk 9 keeps a
        # fractional size as given, where Tk 8.6 rounded it.
        canvas = tk.Canvas(self.text, width=math.ceil(box.width + 2 * margin),
                           height=math.ceil(box.ascent + box.descent + 2 * margin),
                           bg=theme.OUTPUT_BG, highlightthickness=0, borderwidth=0, cursor="arrow")
        mathrender.draw_on_canvas(canvas, box, self._metrics, margin, margin + box.ascent, theme.TEXT)
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
            self.text.insert("end", value + "\n", "s_text")
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
    edit, send - no dependency beyond Windows' own speech engine, already used by `voice`).

    One click starts listening; the button's own label becomes the way to stop early. The
    recognized words replace the text wherever `insert` puts them - never sent by themselves.
    """

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
        self.button.configure(text="Listening… (click to stop)")
        self._stop = self.window.listen(self._done)

    def _done(self, result: VoiceResult) -> None:
        self._stop = None
        self.button.configure(text=self.idle)
        if result.text:
            self.insert(result.text)
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
        holder = tk.Frame(panes, bg=theme.BG)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        panes.add(holder, minsize=px(170), height=px(215))

        out = tk.Frame(panes, bg=theme.BG)
        panes.add(out, minsize=px(140))
        head = tk.Frame(out, bg=theme.BG)
        head.pack(fill="x", pady=(0, px(6)))
        tk.Label(head, text=theme.spaced("Output"), bg=theme.BG, fg=theme.FAINT, font=fonts.code).pack(side="left")
        ttk.Button(head, text="Copy", command=self.copy).pack(side="right")
        self.output = OutputView(window, out, compact=False)
        self.output.frame.pack(fill="both", expand=True)
        self.output.show_text("Output of each command appears here, exactly as the command line prints it.",
                              tag="meta")

        self.pages: dict[str, Page] = {}
        self.nav: dict[str, NavItem] = {}
        for page_class in PAGES:
            page = page_class(self, holder)
            page.frame.grid(row=0, column=0, sticky="nsew")
            self.pages[page.key] = page
            if page_class.hidden:
                continue
            item = NavItem(self, nav, f"{len(self.nav) + 1:02d}", page.title, page.key)
            item.frame.pack(fill="x")
            self.nav[page.key] = item
        self._project_panel(sidebar)
        self.selected = ""
        self.select("status")

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
        tk.Label(panel, text=theme.spaced("Project"), bg=theme.SURFACE, fg=theme.FAINT,
                 font=fonts.code).pack(anchor="w")
        self.project = tk.Label(panel, bg=theme.SURFACE, fg=theme.MUTED, font=fonts.small, justify="left",
                                anchor="w", wraplength=px(212))
        self.project.pack(fill="x", pady=(px(4), px(8)))
        buttons = tk.Frame(panel, bg=theme.SURFACE)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Change…", command=self.change_project).pack(side="left")
        if sys.platform == "win32":
            ttk.Button(buttons, text="Open", command=lambda: os.startfile(self.window.project_root)).pack(
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
        page.frame.tkraise()
        for name, item in self.nav.items():
            item.set_active(name == key)
        code = f"{list(self.nav).index(key) + 1:02d}" if key in self.nav else "·"
        self.page_code.configure(text=f"{code}  /  {theme.spaced(page.title)}")
        self.page_title.configure(text=page.heading)
        self.page_text.configure(text=page.description)
        self.selected = key
        page.focus()

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

    def note(self, parent: tk.Misc, text: str) -> tk.Label:
        return tk.Label(parent, text=text, bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.label,
                        justify="left", anchor="w", wraplength=self.window.px(800))

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
    heading = "Status"
    description = ("The startup report RUDRA prints each time it starts, and the command line's other status "
                   "views. None of them writes to the knowledge database.")

    def build(self) -> None:
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(anchor="w", pady=(self.window.px(8), 0))
        for text, command in (("Startup report", "start"), ("Environment", "env"), ("Paths", "paths"),
                              ("Configuration", "config"), ("Version", "version")):
            control = self.button(row, text, lambda c=command: self.run(c), accent=command == "start")
            control.pack(side="left", padx=(0, self.window.px(8)))
            self.first = self.first or control

    def run(self, command: str) -> bool:
        return self.window.run_form(lambda: commands.status(command))


class AskPage(Page):
    key, title = "ask", "Ask"
    heading = "Ask RUDRA"
    description = ("Ask a question, ask RUDRA to calculate something, or tell it what to do - in plain English, "
                   "typed or spoken. RUDRA answers from your own documents and its own deterministic engines; "
                   "it never guesses, and it says so when it does not understand or does not have enough to go on.")

    def build(self) -> None:
        px = self.window.px
        self.label(self.frame, "Question or request").pack(anchor="w", pady=(px(8), px(4)))
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(fill="x")
        self.field, self.question = self.entry(row, width=64)
        self.field.pack(side="left", fill="x", expand=True)
        self.field.bind("<Return>", lambda _event: self.run())
        self.mic = MicButton(self.window, row, self._dictated)
        self.mic.button.pack(side="left", padx=(px(6), 0))
        self.button(row, "Ask", self.run, accent=True).pack(side="left", padx=(px(6), 0))
        self.first = self.field
        self.note(self.frame, "For example: What is resistance?  ·  Which equations are associated with "
                              "resistance?  ·  Calculate I given I = V / R, V = 10 V and R = 5 Ω."
                  ).pack(fill="x", pady=(px(10), 0))
        options = tk.Frame(self.frame, bg=theme.BG)
        options.pack(fill="x", pady=(px(14), 0))
        self.label(options, "Advanced").pack(anchor="w")
        ttk.Checkbutton(options, text="Act on this computer (otherwise an action request runs on the simulated "
                                      "computer)", variable=self.window.act).pack(anchor="w", pady=(px(4), 0))
        ttk.Checkbutton(options, text="Confirm medium-risk steps (--confirm)",
                        variable=self.window.confirm_medium).pack(anchor="w", pady=(px(2), 0))
        self.note(options, "Actions always pass RUDRA's permission check; high-risk actions are not enabled."
                  ).pack(fill="x", pady=(px(4), 0))

    def _dictated(self, text: str) -> None:
        self.question.set(text)
        self.field.focus_set()
        self.field.icursor("end")

    def run(self) -> bool:
        return self.window.run_form(lambda: commands.ask(self.question.get(), act=self.window.act.get(),
                                                         confirm=self.window.confirm_medium.get()))


class LookupPage(Page):
    key, title = "lookup", "Lookup"
    heading = "Look up knowledge"
    description = ("A concept by its exact name (lookup), any stored item by its identifier (query), or a keyword "
                   "in the derived index (query --keyword). Every result carries its source.")

    def build(self) -> None:
        px = self.window.px
        self.mode = tk.StringVar(master=self.window.root, value="name")
        modes = tk.Frame(self.frame, bg=theme.BG)
        modes.pack(anchor="w", pady=(px(8), px(8)))
        for value, text in (("name", "Concept name"), ("identifier", "Identifier"), ("keyword", "Keyword")):
            ttk.Radiobutton(modes, text=text, value=value, variable=self.mode).pack(side="left", padx=(0, px(16)))
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(fill="x")
        self.field, self.value = self.entry(row, width=50)
        self.field.pack(side="left", fill="x", expand=True)
        self.field.bind("<Return>", lambda _event: self.run())
        self.button(row, "Look up", self.run, accent=True).pack(side="left", padx=(px(8), 0))
        self.first = self.field
        index = tk.Frame(self.frame, bg=theme.BG)
        index.pack(fill="x", pady=(px(14), 0))
        self.button(index, "Build keyword index", self.build_index).pack(side="left")
        self.note(index, "  Keyword search reads data\\indexes\\index.db, rebuilt from the database by this "
                         "button; it never changes the knowledge.").pack(side="left", fill="x")

    def run(self) -> bool:
        return self.window.run_form(lambda: commands.lookup(self.mode.get(), self.value.get()))

    def build_index(self) -> bool:
        return self.window.run_form(lambda: ["index"])


class ProvenancePage(Page):
    key, title = "provenance", "Provenance"
    heading = "Where did this come from?"
    description = ("Any stored item's sources: document, page, quote, extraction run - with the quote and the "
                   "preserved file checked again. When there is none, RUDRA says 'Provenance unavailable.' and "
                   "invents no citation.")

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
        self.note(self.frame, "Identifiers look like K-00000001 (knowledge), CPT-00000001 (concept), "
                              "REL-00000001 (relationship) or DOC-00000001 (document); Lookup shows them."
                  ).pack(fill="x", pady=(px(12), 0))

    def run(self) -> bool:
        return self.window.run_form(lambda: commands.provenance(self.identifier.get()))


class ImportPage(Page):
    key, title = "import", "Add document"
    heading = "Add a document to your knowledge"
    description = ("Choose a PDF, Word, PowerPoint, Excel, EPUB, HTML, Markdown, text, CSV, RTF file, or a scanned "
                   "image. RUDRA reads it, keeps a copy, and adds what it finds - with the exact page and quote "
                   "behind every statement - so you can ask about it right away.")

    def build(self) -> None:
        px = self.window.px
        self.label(self.frame, "Document").pack(anchor="w", pady=(px(8), px(4)))
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(fill="x")
        self.field, self.pdf = self.entry(row, width=60)
        self.field.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self.browse).pack(side="left", padx=(px(8), 0))
        self.first = self.field
        self.button(self.frame, "Add document", self.run, accent=True).pack(anchor="w", pady=(px(14), 0))
        self.note(self.frame, "Scanned pages and images are read with Windows' own OCR engine, on this computer. "
                              "Text read that way is marked and treated as uncertain, never as a sure statement."
                  ).pack(fill="x", pady=(px(12), 0))
        advanced = tk.Frame(self.frame, bg=theme.BG)
        advanced.pack(fill="x", pady=(px(18), 0))
        self.label(advanced, "Advanced").pack(anchor="w")
        self.label(advanced, "Manual of application (optional)").pack(anchor="w", pady=(px(8), px(4)))
        manual_field, self.manual = self.entry(advanced, width=30)
        manual_field.pack(anchor="w")
        self.button(advanced, "Set up or upgrade the knowledge database only", self.database).pack(
            anchor="w", pady=(px(10), 0))
        self.note(advanced, "Adding a document already does this automatically the first time; use this only to "
                            "prepare the database before adding anything.").pack(fill="x", pady=(px(4), 0))

    def browse(self) -> None:
        from app.documents.formats import SUPPORTED_EXTENSIONS

        patterns = " ".join(f"*{extension}" for extension in SUPPORTED_EXTENSIONS)
        chosen = filedialog.askopenfilename(parent=self.window.root, title="Choose a document",
                                            filetypes=(("Supported documents", patterns), ("PDF documents", "*.pdf"),
                                                       ("All files", "*.*")))
        if chosen:
            self.pdf.set(str(Path(chosen)))

    def run(self) -> bool:
        self.window.pending_import_name = Path(self.pdf.get()).name if self.pdf.get().strip() else None
        return self.window.run_form(lambda: commands.extract(self.pdf.get(), self.manual.get()), approve=True)

    def database(self) -> bool:
        return self.window.run_form(lambda: ["db"], approve=True)


class CommandPage(Page):
    key, title = "command", "Command"
    heading = "Any command"
    description = ("Everything the command line can do: type what would follow 'python -m app'. It runs "
                   "exactly as it would there; a command that writes, reaches the Internet or acts on this "
                   "computer is put to you first.")

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
    heading = "Help and documents"
    description = ("The command reference and RUDRA's own documents. docs/LIMITATIONS.md says, subsystem by "
                   "subsystem, what is implemented, partially implemented or not implemented. Updates, voice and "
                   "other maintenance are on the Settings page.")

    def build(self) -> None:
        px = self.window.px
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(anchor="w", pady=(px(8), 0))
        reference = self.button(row, "Command reference", self.reference, accent=True)
        reference.pack(side="left")
        self.first = reference
        for title in commands.DOCUMENTS:
            ttk.Button(row, text=title, command=lambda t=title: self.document(t)).pack(side="left", padx=(px(8), 0))
        self.note(self.frame, f"RUDRA {VERSION}. The window runs the command line's own commands and "
                              "shows their output unchanged; from a terminal the same commands run as "
                              "python -m app <command> (RUDRA-CLI.exe when packaged).").pack(
            fill="x", pady=(px(14), 0))

    def reference(self) -> bool:
        return self.window.run_form(lambda: list(commands.HELP_ARGUMENTS))

    def document(self, title: str) -> None:
        self.view.output.write([(f"{commands.DOCUMENTS[title]}\n\n", "cmd"), (commands.document(title), "out")])


class BackupPage(Page):
    key, title = "backup", "Backup"
    hidden = True  # Settings offers Export/Import with one click each; this page's own
    # methods do the work either way, so nothing here is duplicated.
    heading = "Back up and restore your knowledge"
    description = ("Export writes your whole knowledge base - the knowledge database and the documents it came "
                   "from - to one .rudrabackup file you can keep on a USB drive or another disk. Import restores "
                   "such a file here or on another computer, after checking it completely.")

    def build(self) -> None:
        px = self.window.px
        row = tk.Frame(self.frame, bg=theme.BG)
        row.pack(anchor="w", pady=(px(8), 0))
        export = self.button(row, "Export Knowledge Base…", self.export, accent=True)
        export.pack(side="left")
        self.button(row, "Import Knowledge Base…", self.restore).pack(side="left", padx=(px(8), 0))
        self.first = export
        self.note(self.frame, "Importing replaces the knowledge base in this installation. RUDRA asks first, and "
                              "keeps the current one in its backups folder rather than deleting it. A backup made "
                              "by a newer version of RUDRA is refused until RUDRA is updated.").pack(
            fill="x", pady=(px(12), 0))
        # Dialogs, replaceable for tests: (title, initial name) -> path, and approval questions.
        self.ask_save: Callable[[str], str] = lambda initial: filedialog.asksaveasfilename(
            parent=self.window.root, title="Export Knowledge Base", initialfile=initial, defaultextension=SUFFIX,
            filetypes=(("RUDRA backup", f"*{SUFFIX}"), ("All files", "*.*")))
        self.ask_open: Callable[[], str] = lambda: filedialog.askopenfilename(
            parent=self.window.root, title="Import Knowledge Base",
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

        def work() -> ExportReport:
            return export_knowledge(self.window.layout(), destination, app_version=VERSION, overwrite=True)

        def done(report: object, error: BaseException | None) -> None:
            if error is not None:
                self.show(f"Export failed. Nothing was changed.\n\n{error}", "err")
                return
            assert isinstance(report, ExportReport)
            self.show(f"Knowledge base exported.\n\nFile       : {report.path}\nSize       : "
                      f"{report.size / 1024 ** 2:.2f} MB\nDocuments  : {report.documents}\n"
                      f"Knowledge  : {report.counts.get('knowledge_object', 0)} item(s), "
                      f"{report.counts.get('concept', 0)} concept(s)\n\nCopy this file to a USB drive or another "
                      "disk. Import it with Backup > Import Knowledge Base on any computer with RUDRA.")

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
                question = (f"{summary}\n\nThis installation already has a knowledge base. Importing REPLACES it "
                            "with the backup. The current knowledge base is moved to the backups folder, not "
                            "deleted.\n\nReplace the current knowledge base?")
            else:
                question = f"{summary}\n\nRestore this backup?"
            if not self.ask_yes("Import Knowledge Base", question):
                self.show("Import cancelled. Nothing was changed.", "meta")
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
                "Knowledge base restored and verified.", "",
                f"Integrity   : {checks.get('integrity')}",
                f"Schema      : version {checks.get('schema_version')}"
                + (f" (migrated from {report.migrated_from})" if report.migrated_from else ""),
                f"Knowledge   : {checks.get('knowledge_objects')} item(s), {checks.get('concepts')} concept(s), "
                f"{checks.get('provenance_links')} source link(s)",
                f"Documents   : {checks.get('files_verified')} file(s) verified against their fingerprints",
            ]
            if report.previous_saved_to is not None:
                lines.append(f"Previous    : kept in {report.previous_saved_to}")
            lines += ["", "Keyword search: rebuild the index on the Lookup page (Build keyword index)."]
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
    """The home for maintenance (master specification): updates, voice, backup, restore,
    optional AI and uninstalling, in one place - not scattered as separate top-level pages.

    Backup and AI keep their own, fully working pages (`BackupPage`, `AiPage`); this page
    either calls their methods directly (Export/Import, one click each) or opens them with
    `view.select` for the few things that need more than one field (choosing an AI provider
    and model). Nothing here is a second implementation of either.
    """

    key, title = "settings", "Settings"
    heading = "Settings"
    description = ("Updates, voice input, backing up and restoring your knowledge, optional AI assistance, and "
                   "uninstalling RUDRA. Diagnostics and every other command are under Advanced in the sidebar.")

    def build(self) -> None:
        px = self.window.px

        def section(text: str, *, first: bool = False) -> None:
            if not first:
                tk.Frame(self.frame, bg=theme.LINE, height=1).pack(fill="x", pady=(px(16), px(8)))
            self.label(self.frame, text).pack(anchor="w")

        section("Application", first=True)
        general = tk.Frame(self.frame, bg=theme.BG)
        general.pack(fill="x", pady=(px(8), 0))
        check = self.button(general, "Check for updates", self.check_updates, accent=True)
        check.pack(side="left")
        self.first = check
        self.automatic = tk.BooleanVar(master=self.window.root, value=self._automatic_updates())
        ttk.Checkbutton(general, text="Check automatically (at most once a day)", variable=self.automatic,
                        command=self.set_automatic_updates).pack(side="left", padx=(px(12), 0))

        section("Voice / input")
        voice = tk.Frame(self.frame, bg=theme.BG)
        voice.pack(fill="x", pady=(px(8), 0))
        self.mic = MicButton(self.window, voice, self._heard, idle="Test microphone")
        self.mic.button.pack(side="left")
        self.mic_status = tk.Label(voice, bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.label, anchor="w")
        self.mic_status.pack(side="left", padx=(px(10), 0))
        self.note(self.frame, "The same microphone button appears beside Ask's question field, to dictate instead "
                              "of typing.").pack(fill="x", pady=(px(4), 0))

        section("Data")
        data = tk.Frame(self.frame, bg=theme.BG)
        data.pack(fill="x", pady=(px(8), 0))
        self.button(data, "Back up your knowledge", self.backup, accent=True).pack(side="left")
        self.button(data, "Restore from a backup", self.restore).pack(side="left", padx=(px(8), 0))
        self.note(self.frame, "A backup is one file with everything you've added to RUDRA - keep it on a USB "
                              "drive or another disk. Restoring asks first and keeps what is currently here "
                              "(in the backups folder) rather than deleting it.").pack(fill="x", pady=(px(4), 0))

        section("Optional AI assistance")
        self.ai_status = tk.Label(self.frame, bg=theme.BG, fg=theme.MUTED, font=self.window.fonts.body, anchor="w")
        self.ai_status.pack(fill="x", pady=(px(8), 0))
        self.button(self.frame, "Manage AI assistance…", self.open_ai).pack(anchor="w", pady=(px(8), 0))

        section("Application management")
        ttk.Button(self.frame, text="Uninstall RUDRA…", command=self.uninstall).pack(anchor="w", pady=(px(8), 0))
        self.note(self.frame, "This runs Windows' own uninstaller for RUDRA - the same one Settings > Apps would "
                              "use. Your knowledge base lives in your user profile and is kept either way."
                  ).pack(fill="x", pady=(px(4), 0))

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


PAGES = (StatusPage, AskPage, LookupPage, ProvenancePage, ImportPage, SettingsPage, BackupPage, AiPage,
         CommandPage, HelpPage)
