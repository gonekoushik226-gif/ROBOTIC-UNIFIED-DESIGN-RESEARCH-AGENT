"""The Windows adapter behind the platform port (ADR 0047 P15-2; D-08).

Documented Windows APIs through `ctypes` and the standard library only - no dependency:

    windows      EnumWindows, IsWindowVisible, DwmGetWindowAttribute (cloaked windows are
                 not shown), GetWindowTextW, GetClassNameW, GetWindowThreadProcessId,
                 QueryFullProcessImageNameW
    foreground   GetForegroundWindow
    focused text GetGUIThreadInfo (the focused control of the foreground window's thread),
                 then WM_GETTEXTLENGTH / WM_GETTEXT through SendMessageTimeoutW - only for
                 standard edit fields (Edit, RichEdit), never for a password field
    screen       GetSystemMetrics, after making the process DPI-aware (Phase 0 found 125 %
                 scaling), so coordinates and screenshots are physical pixels
    clipboard    OpenClipboard / GetClipboardData(CF_UNICODETEXT), read-only
    launching    ShellExecute through `os.startfile`; `App Paths` and URL protocols read
                 through `winreg`
    closing      PostMessageW(WM_CLOSE): the normal close request a window's X sends;
                 a process is never terminated
    files        exclusive creation; moves that never replace; nothing deleted except
                 the source of a verified move within one volume's rename
    input        SendInput (Unicode characters, virtual keys, mouse buttons, wheel)
    screenshot   GetDC / BitBlt / GetDIBits into the standard-library PNG writer

Nothing here decides whether an operation is allowed (the permission engine) or whether
it succeeded (the action's postcondition check). No security control is bypassed
(section 101).
"""

import ctypes
import os
import shutil
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from app.actions import png
from app.actions.ports import FocusedText, LaunchOutcome, WindowInfo
from app.actions.safety import KEYS, MODIFIERS
from app.applications import LaunchKind, LaunchMethod

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.UINT]
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = wintypes.LONG
_user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
_user32.SendMessageTimeoutW.restype = wintypes.LPARAM


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", _RECT)]


_user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)]
_user32.GetGUIThreadInfo.restype = wintypes.BOOL

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_DWMWA_CLOAKED = 14
_WM_CLOSE = 0x0010
_CF_UNICODETEXT = 13
_WM_GETTEXT, _WM_GETTEXTLENGTH = 0x000D, 0x000E
_GWL_STYLE, _ES_PASSWORD = -16, 0x0020
_SMTO_BLOCK, _SMTO_ABORTIFHUNG = 0x0001, 0x0002
#: A field is read back only when it answers within this time and holds at most this much text.
_READ_TIMEOUT_MS, _MAX_FIELD_CHARS = 1000, 1_000_000
#: The standard edit fields whose text Windows hands to another process on WM_GETTEXT.
_EDIT_CLASSES = ("edit", "richedit")
_SRCCOPY, _CAPTUREBLT = 0x00CC0020, 0x40000000
_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def make_dpi_aware() -> None:
    """Per-monitor DPI awareness for this process, so pixels are physical (Windows 10+).

    A process whose awareness is already set keeps it; the call then fails harmlessly.
    """
    setter = getattr(_user32, "SetProcessDpiAwarenessContext", None)
    if setter is not None:
        setter.argtypes = [ctypes.c_void_p]
        if setter(ctypes.c_void_p(-4)):
            return
    _user32.SetProcessDPIAware()


# --------------------------------------------------------------------- inputs

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_KEYEVENTF_KEYUP, _KEYEVENTF_UNICODE = 0x0002, 0x0004
_MOUSE_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}
_MOUSEEVENTF_WHEEL, _WHEEL_DELTA = 0x0800, 120


def text_inputs(text: str) -> list[INPUT]:
    """Key-down and key-up events for each UTF-16 code unit of the text (KEYEVENTF_UNICODE)."""
    events = []
    units = text.encode("utf-16-le")
    for i in range(0, len(units), 2):
        unit = int.from_bytes(units[i:i + 2], "little")
        for flags in (_KEYEVENTF_UNICODE, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP):
            event = INPUT(type=_INPUT_KEYBOARD)
            event.u.ki = _KEYBDINPUT(0, unit, flags, 0, 0)
            events.append(event)
    return events


def key_inputs(keys: tuple[str, ...]) -> list[INPUT]:
    """Modifiers down, the key down and up, modifiers up in reverse order."""
    codes = [MODIFIERS[k] if k in MODIFIERS else KEYS[k] for k in keys]
    downs = [(code, 0) for code in codes]
    ups = [(code, _KEYEVENTF_KEYUP) for code in reversed(codes)]
    events = []
    for code, flags in downs + ups:
        event = INPUT(type=_INPUT_KEYBOARD)
        event.u.ki = _KEYBDINPUT(code, 0, flags, 0, 0)
        events.append(event)
    return events


_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


def _send(events: list[INPUT]) -> None:
    if not events:
        return
    array = (INPUT * len(events))(*events)
    sent = _user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError(f"SendInput delivered {sent} of {len(events)} events (error {ctypes.get_last_error()})")


# ------------------------------------------------------------------- the adapter


class WindowsPlatform:
    """The live Windows computer, behind the platform port. `live` is True."""

    name = "Windows (live)"
    live = True

    def __init__(self) -> None:
        make_dpi_aware()

    # --------------------------------------------------------------------- state

    def _info(self, hwnd: int) -> WindowInfo | None:
        if not hwnd or not _user32.IsWindowVisible(hwnd):
            return None
        cloaked = wintypes.DWORD(0)
        _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return None
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        title = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, title, length + 1)
        class_name = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, class_name, 256)
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return WindowInfo(int(hwnd), title.value, class_name.value, int(pid.value), _image(pid.value))

    def windows(self) -> tuple[WindowInfo, ...]:
        found: list[WindowInfo] = []

        def visit(hwnd, _lparam):
            info = self._info(hwnd)
            if info is not None:
                found.append(info)
            return True

        _user32.EnumWindows(_ENUM_PROC(visit), 0)
        return tuple(found)

    def foreground(self) -> WindowInfo | None:
        return self._info(_user32.GetForegroundWindow())

    def screen_size(self) -> tuple[int, int]:
        return int(_user32.GetSystemMetrics(0)), int(_user32.GetSystemMetrics(1))

    def clipboard_text(self) -> str | None:
        if not _user32.OpenClipboard(None):
            return None
        try:
            handle = _user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return None
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return ctypes.wstring_at(pointer)
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()

    def focused_text(self) -> FocusedText | None:
        foreground = _user32.GetForegroundWindow()
        if not foreground:
            return None
        thread = _user32.GetWindowThreadProcessId(foreground, None)
        info = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
        if not thread or not _user32.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.hwndFocus:
            return None
        return read_edit(info.hwndFocus)

    def settle(self, seconds: float) -> None:
        time.sleep(seconds)

    # ------------------------------------------------------- applications, windows

    def _target(self, method: LaunchMethod) -> str | None:
        """What `os.startfile` is given for a method, or None when it cannot be resolved."""
        if method.kind is LaunchKind.APP_PATH:
            return app_path(method.value)
        if method.kind in (LaunchKind.SYSTEM_PATH, LaunchKind.SHORTCUT):
            path = os.path.expandvars(method.value)
            return path if Path(path).is_file() else None
        if method.kind is LaunchKind.PROTOCOL:
            return method.value if protocol_registered(method.value.rstrip(":")) else None
        return None

    def can_launch(self, method: LaunchMethod) -> tuple[bool, str]:
        target = self._target(method)
        if target is None:
            return False, f"{method.kind.value} {method.value} does not resolve on this machine"
        return True, f"{method.kind.value} {method.value} resolves to {target}"

    def launch(self, method: LaunchMethod) -> LaunchOutcome:
        target = self._target(method)
        if target is None:
            return LaunchOutcome(False, "the launch method does not resolve")
        try:
            os.startfile(target)
        except OSError as exc:
            return LaunchOutcome(False, f"ShellExecute refused: {exc}")
        return LaunchOutcome(True, f"ShellExecute started {target}")

    def open_with_default(self, path: str) -> LaunchOutcome:
        try:
            os.startfile(path)
        except OSError as exc:
            return LaunchOutcome(False, f"ShellExecute refused: {exc}")
        return LaunchOutcome(True, "opened with its default application")

    def close_window(self, handle: int) -> bool:
        if not _user32.IsWindow(handle):
            return False
        return bool(_user32.PostMessageW(handle, _WM_CLOSE, 0, 0))

    # --------------------------------------------------------------------- files

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def modified(self, path: str) -> float | None:
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return None

    def write_new(self, path: str, data: bytes) -> None:
        with open(path, "xb") as handle:  # exclusive: never overwrites
            handle.write(data)

    def copy_new(self, source: str, destination: str) -> None:
        self.write_new(destination, Path(source).read_bytes())
        shutil.copystat(source, destination)

    def move_new(self, source: str, destination: str) -> None:
        if Path(destination).exists():
            raise FileExistsError(destination)
        os.rename(source, destination)  # on Windows, fails rather than replaces

    def make_dir(self, path: str) -> None:
        os.mkdir(path)

    # ------------------------------------------------------------ input, screen

    def send_text(self, text: str) -> None:
        _send(text_inputs(text))

    def send_keys(self, keys: tuple[str, ...]) -> None:
        _send(key_inputs(keys))

    def click(self, x: int, y: int, button: str, count: int) -> None:
        if not _user32.SetCursorPos(x, y):
            raise OSError(f"the pointer could not be moved to ({x}, {y})")
        down, up = _MOUSE_FLAGS[button]
        events = []
        for _ in range(count):
            for flags in (down, up):
                event = INPUT(type=_INPUT_MOUSE)
                event.u.mi = _MOUSEINPUT(0, 0, 0, flags, 0, 0)
                events.append(event)
        _send(events)

    def scroll(self, direction: str, amount: int) -> None:
        event = INPUT(type=_INPUT_MOUSE)
        delta = _WHEEL_DELTA * amount * (1 if direction == "up" else -1)
        event.u.mi = _MOUSEINPUT(0, 0, ctypes.c_uint32(delta).value, _MOUSEEVENTF_WHEEL, 0, 0)
        _send([event])

    def screenshot(self, path: str) -> tuple[int, int]:
        width, height = self.screen_size()
        rows = capture_rows(width, height)
        self.write_new(path, png.encode(width, height, rows))
        return width, height


# ------------------------------------------------------------------- helpers


def _image(pid: int) -> str:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name.casefold()
    finally:
        _kernel32.CloseHandle(handle)


def _message(hwnd: int, message: int, wparam: int, lparam: int) -> int | None:
    result = ctypes.c_size_t(0)
    if not _user32.SendMessageTimeoutW(hwnd, message, wparam, lparam, _SMTO_BLOCK | _SMTO_ABORTIFHUNG,
                                       _READ_TIMEOUT_MS, ctypes.byref(result)):
        return None
    return result.value


def read_edit(hwnd: int) -> FocusedText | None:
    """The text of a standard edit field, or None when it is not one, is a password field or does not answer.

    Windows copies an edit field's text across processes for WM_GETTEXT; a field of any
    other kind (a browser, a modern app's own text box) is not read, and neither is a
    password field.
    """
    class_name = ctypes.create_unicode_buffer(256)
    if not _user32.GetClassNameW(hwnd, class_name, 256):
        return None
    if not class_name.value.casefold().startswith(_EDIT_CLASSES):
        return None
    if _user32.GetWindowLongW(hwnd, _GWL_STYLE) & _ES_PASSWORD:
        return None
    length = _message(hwnd, _WM_GETTEXTLENGTH, 0, 0)
    if length is None or length > _MAX_FIELD_CHARS:
        return None
    buffer = ctypes.create_unicode_buffer(length + 1)
    copied = _message(hwnd, _WM_GETTEXT, length + 1, ctypes.cast(buffer, ctypes.c_void_p).value or 0)
    if copied is None:
        return None
    return FocusedText(int(hwnd), class_name.value, buffer.value)


def app_path(executable: str) -> str | None:
    """The path Windows `App Paths` registers for an executable, when the file exists."""
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"{_APP_PATHS}\{executable}") as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        path = os.path.expandvars(str(value).strip().strip('"'))
        if Path(path).is_file():
            return path
    return None


def protocol_registered(scheme: str) -> bool:
    """Whether a URI scheme is registered as a URL protocol (HKCR\\<scheme>\\URL Protocol)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme) as key:
            winreg.QueryValueEx(key, "URL Protocol")
        return True
    except OSError:
        return False


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def capture_rows(width: int, height: int) -> list[bytes]:
    """The screen as top-down RGB rows (BitBlt into a 24-bit DIB)."""
    screen = _user32.GetDC(None)
    memory = _gdi32.CreateCompatibleDC(screen)
    bitmap = _gdi32.CreateCompatibleBitmap(screen, width, height)
    previous = _gdi32.SelectObject(memory, bitmap)
    try:
        if not _gdi32.BitBlt(memory, 0, 0, width, height, screen, 0, 0, _SRCCOPY | _CAPTUREBLT):
            raise OSError("BitBlt could not copy the screen")
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width, -height, 1, 24, 0, 0, 0, 0, 0, 0)
        stride = (width * 3 + 3) & ~3
        buffer = ctypes.create_string_buffer(stride * height)
        lines = _gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        if lines != height:
            raise OSError(f"GetDIBits returned {lines} of {height} lines")
        raw = buffer.raw
        rows = []
        for y in range(height):
            bgr = raw[y * stride:y * stride + width * 3]
            rgb = bytearray(width * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = bgr[2::3], bgr[1::3], bgr[0::3]
            rows.append(bytes(rgb))
        return rows
    finally:
        _gdi32.SelectObject(memory, previous)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(memory)
        _user32.ReleaseDC(None, screen)
