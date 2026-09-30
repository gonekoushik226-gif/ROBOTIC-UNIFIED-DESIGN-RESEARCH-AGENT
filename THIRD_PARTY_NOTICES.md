# Third-party notices

RUDRA's own code is released under the MIT License (`LICENSE`). The Windows
application also contains the third-party software listed below, unmodified. The full
license texts are installed with RUDRA in the `licenses` folder of the program
directory; the build copies them from the exact packages it bundles
(`windows/build.py`, `collect_licenses`).

| Component | Version bundled | License | Full text |
|---|---|---|---|
| Python runtime and standard library | 3.14 (the exact release is shown by `RUDRA-CLI.exe version`) | Python Software Foundation License Version 2 | `licenses/PYTHON-LICENSE.txt` |
| pypdf | 6.19.0 | BSD 3-Clause | `licenses/PYPDF-LICENSE.txt` |
| PyInstaller bootloader | 6.22.3 | GPL-2.0-or-later **with the PyInstaller Bootloader Exception**, which permits distributing the generated executables under any terms | `licenses/PYINSTALLER-COPYING.txt` |

## Components distributed as part of the Python runtime

The Python for Windows runtime that PyInstaller bundles contains the following
libraries. Their licenses and copyright notices are reproduced in
`licenses/PYTHON-LICENSE.txt` (the section *"Additional Conditions for this Windows
binary build"* and the notices that follow it), exactly as Python distributes them.

| Component | Files in the application | License |
|---|---|---|
| SQLite | `sqlite3.dll` | Public domain |
| Tcl/Tk 8.6 | `tcl86t.dll`, `tk86t.dll`, `_tcl_data`, `_tk_data`, `tcl8` | Tcl/Tk license (BSD-style) |
| OpenSSL 3 | `libcrypto-3.dll`, `libssl-3.dll` | Apache License 2.0 |
| libffi | `libffi-8.dll` | MIT |
| zlib-ng | `zlib1.dll` | zlib license |
| bzip2 | `_bz2.pyd` | bzip2 license (BSD-style) |
| XZ Utils / liblzma | `_lzma.pyd` | 0BSD / public domain |
| Zstandard | `_zstd.pyd` | BSD 3-Clause |
| Expat | `pyexpat.pyd` | MIT |
| mpdecimal | `_decimal.pyd` | BSD 2-Clause |

## Microsoft Visual C++ runtime

`VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, `ucrtbase.dll` and the `api-ms-win-*.dll`
files are Microsoft Distributable Code, redistributed as part of the Python for
Windows runtime. They are subject to Microsoft's redistribution terms, summarised in
`licenses/PYTHON-LICENSE.txt`: they may not be altered, and they may be used only on
Microsoft operating systems. By installing RUDRA you agree to terms that protect the
Microsoft Distributable Code at least as much as Microsoft's own requirements.

## Fonts

RUDRA draws mathematics with fonts that ship with Windows (Cambria and Cambria Math,
with Times New Roman and Segoe UI Symbol as fallbacks). No font is bundled.

## Windows components used, not distributed

OCR (`Windows.Media.Ocr`), PDF page rendering (`Windows.Data.Pdf`), image decoding
(`Windows.Graphics.Imaging`), the Windows Credential Manager, speech recognition and
Windows PowerShell are parts of Windows. RUDRA calls them on the user's own computer; it
does not bundle or redistribute them.

## Optional external services

The optional AI providers (Anthropic, OpenAI, Google Gemini, Mistral AI) are online
services reached over HTTPS with the user's own account and API key. No code or library
of theirs is bundled; their own terms apply to the user's use of them.

## Build-time and development tools (not distributed)

PyInstaller and its dependencies (`altgraph`, `pefile`, `pywin32-ctypes`,
`pyinstaller-hooks-contrib`, `setuptools`), pytest and its dependencies, and Inno
Setup are used to build and test RUDRA. They are not part of the installed application,
except for the PyInstaller bootloader listed above.
