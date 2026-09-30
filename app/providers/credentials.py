"""The user's API keys, kept by Windows Credential Manager - never in a RUDRA file.

Keys are stored as generic credentials for the current Windows user (target names
``RUDRA/ai/<provider>``), encrypted by Windows. RUDRA's configuration, logs, backups and
diagnostics never contain them. Reading, writing and deleting use the Win32 Credential API
(`advapi32`) through `ctypes`: no dependency.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

TARGET_PREFIX = "RUDRA/ai/"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
MAX_KEY_CHARS = 1024


class CredentialError(Exception):
    """The credential store could not be used."""


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _api():
    if sys.platform != "win32":
        raise CredentialError("The Windows Credential Manager is available on Windows only.")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    return advapi


def target(provider_key: str) -> str:
    return TARGET_PREFIX + provider_key


def store_key(provider_key: str, api_key: str, *, target_name: str | None = None) -> None:
    key = api_key.strip()
    if not key or len(key) > MAX_KEY_CHARS or any(c.isspace() for c in key):
        raise CredentialError("That does not look like an API key (it is empty, too long or contains spaces).")
    blob = key.encode("utf-16-le")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = _CREDENTIAL()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target_name or target(provider_key)
    credential.Comment = "API key used by RUDRA's optional AI assistance"
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "RUDRA"
    if not _api().CredWriteW(ctypes.byref(credential), 0):
        raise CredentialError(f"Windows could not store the key (error {ctypes.get_last_error()}).")


def read_key(provider_key: str, *, target_name: str | None = None) -> str | None:
    """The stored key, or None when none is stored."""
    api = _api()
    pointer = ctypes.POINTER(_CREDENTIAL)()
    if not api.CredReadW(target_name or target(provider_key), CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise CredentialError(f"Windows could not read the key (error {error}).")
    try:
        size = pointer.contents.CredentialBlobSize
        data = ctypes.string_at(pointer.contents.CredentialBlob, size)
        return data.decode("utf-16-le")
    finally:
        api.CredFree(pointer)


def delete_key(provider_key: str, *, target_name: str | None = None) -> bool:
    """Remove the stored key; False when there was none."""
    if _api().CredDeleteW(target_name or target(provider_key), CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == ERROR_NOT_FOUND:
        return False
    raise CredentialError(f"Windows could not delete the key (error {error}).")


def has_key(provider_key: str) -> bool:
    try:
        return read_key(provider_key) is not None
    except CredentialError:
        return False
