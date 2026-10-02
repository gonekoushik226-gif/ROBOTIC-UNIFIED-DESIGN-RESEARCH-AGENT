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

## Speech recognition (the `speech` folder)

RUDRA recognizes speech offline with the three open-source pieces below, installed unmodified
in the `speech` folder of the program directory. They are pinned by exact URL and SHA-256 in
`windows/fetch_speech.py`, which verifies every file; the license texts are installed in the
`licenses` folder. Nothing here connects to the Internet when RUDRA runs.

| Component | Version | License | Full text |
|---|---|---|---|
| whisper.cpp (`whisper-cli.exe`, `whisper.dll`, `ggml*.dll`) - the program that runs the model | v1.9.4, release build b5130 (commit 927cfce34f31707e17f2bff35c349632fb9e2c3a), from github.com/ggml-org/whisper.cpp | MIT, Copyright (c) 2023-2026 The ggml authors | `licenses/WHISPER-CPP-LICENSE.txt` |
| OpenAI Whisper small.en model weights, in whisper.cpp's ggml format, 5-bit quantized (`ggml-small.en-q5_1.bin`, 190,098,681 bytes) | the file published at huggingface.co/ggerganov/whisper.cpp, revision 5359861c739e955e79d9a303bcbc70fb988958b1 | MIT (the Whisper repository states: "Whisper's code and model weights are released under the MIT License"), Copyright (c) 2022 OpenAI | `licenses/OPENAI-WHISPER-LICENSE.txt` |
| Silero VAD voice-activity model v5.1.2 in ggml format (`ggml-silero-v5.1.2.bin`, 885,098 bytes) | the file published at huggingface.co/ggml-org/whisper-vad, revision 9ffd54a1e1ee413ddf265af9913beaf518d1639b | MIT, Copyright (c) 2020-present Silero Team | `licenses/SILERO-VAD-LICENSE.txt` |

The licenses are as published by those projects at the versions named, on 2026-10-01; no
other terms were found attached to the files. Microphone audio is processed in memory and is
never stored.

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
(`Windows.Graphics.Imaging`), the microphone interface (`winmm`), speech output
(`System.Speech`), the Windows Credential Manager and Windows PowerShell are parts of Windows. RUDRA calls them on the user's own computer; it
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
