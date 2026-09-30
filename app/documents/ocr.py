"""Local optical character recognition through Windows' own OCR engine.

RUDRA bundles no OCR software. Windows 10 and 11 include an OCR engine
(`Windows.Media.Ocr`) and a PDF renderer (`Windows.Data.Pdf`); RUDRA reaches both
through Windows PowerShell with a script it writes itself, as the voice feature reaches
the speech engine. Everything runs on this computer: no image or text leaves it.

    status()                       whether OCR can run here, and the languages installed
    recognize_image(path)          each frame of a PNG, JPEG or TIFF image
    recognize_pdf_pages(path, ns)  the given 1-based pages of a PDF, rendered and recognized

A result keeps every recognized line with the bounding box of each word, in the
coordinates of the image that was recognized (for a PDF page: the page rendered at
`PDF_RENDER_SCALE` times its size in device-independent pixels). **Windows' engine reports
no confidence value**, so none is recorded; text from OCR is always marked as such and
treated as uncertain by extraction (`TextOrigin.OCR`).

The file path is passed to the script as data (an environment variable), never as code.
If Windows PowerShell, the engine or an OCR language is missing, `OcrUnavailable` says
why; nothing else in RUDRA depends on OCR.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

def _powershell() -> str:
    """Windows PowerShell by its fixed location, so OCR works whatever the PATH holds."""
    system = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    candidate = system / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell.exe"


POWERSHELL = _powershell()
TIMEOUT_SECONDS = 300
#: PDF pages are rendered at this multiple of their size in 1/96-inch units (2.5 = 240 dpi).
PDF_RENDER_SCALE = 2.5
ENGINE_NAME = "Windows.Media.Ocr"
#: Set to "1" to switch OCR off for a process (the test suite does, for reproducible
#: results on any machine; OCR tests switch it back on).
SWITCH = "RUDRA_DISABLE_OCR"


class OcrUnavailable(Exception):
    """OCR cannot run on this computer; the reason says what is missing."""


@dataclass(frozen=True)
class OcrWord:
    text: str
    #: x, y, width, height in the recognized image's pixels.
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class OcrLine:
    text: str
    words: tuple[OcrWord, ...] = ()

    @property
    def box(self) -> tuple[float, float, float, float] | None:
        if not self.words:
            return None
        left = min(w.box[0] for w in self.words)
        top = min(w.box[1] for w in self.words)
        right = max(w.box[0] + w.box[2] for w in self.words)
        bottom = max(w.box[1] + w.box[3] for w in self.words)
        return left, top, right - left, bottom - top


@dataclass(frozen=True)
class OcrPage:
    """One recognized image: a PDF page, or one frame of an image file."""

    number: int
    lines: tuple[OcrLine, ...]
    width: int
    height: int
    language: str
    angle: float | None = None
    engine: str = ENGINE_NAME
    #: Windows' engine reports none; kept explicit so no number is ever invented.
    confidence: float | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def to_json(self) -> dict:
        return {
            "number": self.number, "width": self.width, "height": self.height, "language": self.language,
            "angle": self.angle, "engine": self.engine, "confidence": self.confidence,
            "lines": [{"text": line.text, "words": [{"text": w.text, "box": list(w.box)} for w in line.words]}
                      for line in self.lines],
        }


@dataclass(frozen=True)
class OcrStatus:
    available: bool
    reason: str
    languages: tuple[str, ...] = field(default_factory=tuple)


_PRELUDE = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTaskOp = ($methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskAction = ($methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
function Await($operation, [Type]$type) {
    $task = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($operation))
    $null = $task.Wait(-1)
    return $task.Result
}
function AwaitAction($action) {
    $task = $asTaskAction.Invoke($null, @($action))
    $null = $task.Wait(-1)
}
function Engine() {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        $first = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | Select-Object -First 1
        if ($null -ne $first) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($first) }
    }
    if ($null -eq $engine) { throw 'No OCR language is installed in Windows.' }
    return $engine
}
function Recognize($engine, $bitmap, [int]$number) {
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($line in $result.Lines) {
        $words = @()
        foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            $words += ,@{ text = $word.Text; box = @($r.X, $r.Y, $r.Width, $r.Height) }
        }
        $lines += ,@{ text = $line.Text; words = $words }
    }
    $angle = $null
    if ($null -ne $result.TextAngle) { $angle = [double]$result.TextAngle }
    return @{ number = $number; width = $bitmap.PixelWidth; height = $bitmap.PixelHeight;
              language = $engine.RecognizerLanguage.LanguageTag; angle = $angle; lines = $lines }
}
function Bitmap($decoder) {
    return Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
}
function Limit($bitmap) {
    $max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($bitmap.PixelWidth -gt $max -or $bitmap.PixelHeight -gt $max) {
        throw "The image is larger than the OCR engine accepts ($max pixels on a side)."
    }
}
"""

_STATUS = r"""
$languages = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
@{ languages = $languages } | ConvertTo-Json -Compress
"""

_IMAGE = r"""
$engine = Engine
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:RUDRA_OCR_INPUT)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$pages = @()
for ($i = 0; $i -lt $decoder.FrameCount; $i++) {
    $frame = Await ($decoder.GetFrameAsync([uint32]$i)) ([Windows.Graphics.Imaging.BitmapFrame])
    $bitmap = Await ($frame.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    Limit $bitmap
    $pages += ,(Recognize $engine $bitmap ($i + 1))
}
$stream.Dispose()
@{ pages = $pages } | ConvertTo-Json -Depth 8 -Compress
"""

_PDF = r"""
$engine = Engine
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:RUDRA_OCR_INPUT)) ([Windows.Storage.StorageFile])
$pdf = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
$scale = [double]$env:RUDRA_OCR_SCALE
$max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
$pages = @()
foreach ($token in $env:RUDRA_OCR_PAGES.Split(',')) {
    $number = [int]$token
    if ($number -lt 1 -or $number -gt $pdf.PageCount) { continue }
    $page = $pdf.GetPage([uint32]($number - 1))
    $width = [Math]::Min([double]$max, [Math]::Round($page.Size.Width * $scale))
    $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $options.DestinationWidth = [uint32]$width
    $memory = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    AwaitAction ($page.RenderToStreamAsync($memory, $options))
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($memory)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Bitmap $decoder
    Limit $bitmap
    $pages += ,(Recognize $engine $bitmap $number)
    $memory.Dispose()
    $page.Dispose()
}
@{ pages = $pages; page_count = $pdf.PageCount } | ConvertTo-Json -Depth 8 -Compress
"""


_RENDER = r"""
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:RUDRA_OCR_INPUT)) ([Windows.Storage.StorageFile])
$pdf = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
$number = [int]$env:RUDRA_OCR_PAGES
if ($number -lt 1 -or $number -gt $pdf.PageCount) { throw "The document has no page $number." }
$page = $pdf.GetPage([uint32]($number - 1))
$options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
$options.DestinationWidth = [uint32]([Math]::Round($page.Size.Width * [double]$env:RUDRA_OCR_SCALE))
$memory = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
AwaitAction ($page.RenderToStreamAsync($memory, $options))
$stream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($memory.GetInputStreamAt(0))
$buffer = New-Object System.IO.MemoryStream
$stream.CopyTo($buffer)
@{ png = [Convert]::ToBase64String($buffer.ToArray()); page_count = $pdf.PageCount } | ConvertTo-Json -Compress
"""


def render_pdf_page(path: Path, page: int, target: Path, *, scale: float = 1.5) -> Path:
    """One PDF page drawn by Windows' own renderer into a PNG file (for inspecting the source)."""
    raw = _run(_RENDER, {"RUDRA_OCR_INPUT": str(Path(path).resolve()), "RUDRA_OCR_PAGES": str(int(page)),
                         "RUDRA_OCR_SCALE": str(scale)})
    data = base64.b64decode(raw.get("png", ""))
    if not data.startswith(b"\x89PNG"):
        raise OcrUnavailable("Windows did not return a PNG image for the page")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _run(script: str, env: dict[str, str] | None = None) -> dict:
    if sys.platform != "win32":
        raise OcrUnavailable("OCR uses Windows' own engine and is available on Windows only")
    encoded = base64.b64encode((_PRELUDE + script).encode("utf-16-le")).decode()
    environment = dict(os.environ)
    environment.update(env or {})
    try:
        done = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS,
            env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise OcrUnavailable("Windows PowerShell is not available on this computer") from None
    except subprocess.TimeoutExpired:
        raise OcrUnavailable(f"the OCR engine did not finish within {TIMEOUT_SECONDS} s") from None
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if done.returncode != 0 or not lines:
        detail = " ".join(done.stderr.split())
        marker = "Exception"
        if marker in detail:
            detail = detail[detail.find(":", detail.find(marker)) + 1:].strip() or detail
        raise OcrUnavailable(f"the OCR engine failed: {detail[:300] or f'exit code {done.returncode}'}")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        raise OcrUnavailable(f"the OCR engine's answer could not be read: {lines[-1][:200]}") from None


def _pages(raw: dict) -> tuple[OcrPage, ...]:
    pages = raw.get("pages") or []
    if isinstance(pages, dict):  # PowerShell writes a one-element array as the element itself
        pages = [pages]
    out = []
    for page in pages:
        lines = page.get("lines") or []
        if isinstance(lines, dict):
            lines = [lines]
        parsed = []
        for line in lines:
            words = line.get("words") or []
            if isinstance(words, dict):
                words = [words]
            parsed.append(OcrLine(str(line.get("text", "")),
                                  tuple(OcrWord(str(w.get("text", "")), tuple(float(v) for v in w.get("box", ())))
                                        for w in words)))
        out.append(OcrPage(int(page.get("number", 0)), tuple(parsed), int(page.get("width", 0)),
                           int(page.get("height", 0)), str(page.get("language", "")),
                           None if page.get("angle") is None else float(page["angle"])))
    return tuple(out)


_STATUS_CACHE: list[OcrStatus] = []


def status(*, refresh: bool = False) -> OcrStatus:
    """Whether OCR may be used now: switched on, and available on this computer."""
    if os.environ.get(SWITCH) == "1":
        return OcrStatus(False, f"OCR is switched off for this process ({SWITCH}=1).")
    return engine_status(refresh=refresh)


def engine_status(*, refresh: bool = False) -> OcrStatus:
    """Whether Windows' OCR engine can run on this computer (cached for the process)."""
    if _STATUS_CACHE and not refresh:
        return _STATUS_CACHE[0]
    try:
        raw = _run(_STATUS)
        languages = raw.get("languages") or []
        if isinstance(languages, str):
            languages = [languages]
        result = (OcrStatus(True, "Windows OCR is available.", tuple(languages)) if languages
                  else OcrStatus(False, "No OCR language is installed in Windows (Settings > Time & language > "
                                        "Language & region: add a language with the Optical character "
                                        "recognition feature)."))
    except OcrUnavailable as exc:
        result = OcrStatus(False, str(exc))
    _STATUS_CACHE[:] = [result]
    return result


def recognize_image(path: Path) -> tuple[OcrPage, ...]:
    """Every frame of a PNG, JPEG or TIFF image, recognized."""
    return _pages(_run(_IMAGE, {"RUDRA_OCR_INPUT": str(Path(path).resolve())}))


def recognize_pdf_pages(path: Path, pages: tuple[int, ...] | list[int]) -> tuple[OcrPage, ...]:
    """The given 1-based pages of a PDF, rendered by Windows and recognized."""
    wanted = sorted({int(n) for n in pages if int(n) >= 1})
    if not wanted:
        return ()
    return _pages(_run(_PDF, {"RUDRA_OCR_INPUT": str(Path(path).resolve()),
                              "RUDRA_OCR_PAGES": ",".join(str(n) for n in wanted),
                              "RUDRA_OCR_SCALE": str(PDF_RENDER_SCALE)}))
