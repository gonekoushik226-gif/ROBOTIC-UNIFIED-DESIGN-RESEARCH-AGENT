"""Speech in and out: offline recognition with Whisper, speech output with Windows' voice.

    recognize_file(path)   a WAV file -> the transcript, how sure the model was, the recognizer
    listen(seconds)        ONE utterance from the default microphone, only when the user
                           asks (`voice --listen`); nothing is kept (section 142)
    synthesize(text, path) text -> a new WAV file (speech output, optional)

Recognition is OpenAI's Whisper model run offline by whisper.cpp (`app.voice.engine`): no
account, no network, no cloud. It replaced Windows' built-in dictation, whose reading of
ordinary English - names above all - was not good enough to use ("My name is Kaushik"
came back as "Like name is go seek"; docs/VOICE.md has the measurements). RUDRA's own
vocabulary - the verbs it understands and the registered applications' names (data, from
the registry) - is given to the model as context, which helps it spell "Calculator" and
"RUDRA" and costs nothing when a speaker says something else.

A transcript carries the model's own confidence, reported as it gives it; no threshold is
invented beyond marking a reading `uncertain` when the model itself was unsure of its words,
so the person checks them before anything is sent. Nothing is sent by speaking: the words
land in the text box to be read, edited and sent like typed text (section 220).

Speech output still goes through Windows' `System.Speech` (SAPI) by way of Windows
PowerShell with a script RUDRA writes, never with text a user or a document supplied as code.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.applications import APPLICATIONS
from app.core.errors import InvalidInputError
from app.voice import capture, engine
from app.voice.errors import SpeechCancelled, SpeechUnavailable
from app.voice.process import hidden_process

#: The verbs a spoken command may begin with (the interpreter's application rules).
VERBS = ("open", "launch", "start", "run", "close", "quit", "exit", "stop")
#: The language recognised. The model is English-only: it does not guess at other languages.
CULTURE = "en-US"
TIMEOUT = 90


def _powershell() -> str:
    """Windows PowerShell by its fixed location, so speech output works whatever the PATH holds."""
    system = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    candidate = system / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell.exe"


POWERSHELL = _powershell()
MAX_AUDIO_BYTES = 10 * 1024 * 1024

# Re-exported: callers and tests import these from here.
__all__ = ["SpeechUnavailable", "SpeechCancelled"]


@dataclass(frozen=True, slots=True)
class Transcript:
    #: What was recognized; "" when nothing was.
    text: str
    #: "dictation" (free speech) when something was recognized, "" when nothing was.
    grammar: str
    #: The mean probability the model gave its words (0..1), as it reports it; None if nothing.
    confidence: float | None
    recognizer: str
    #: Where the audio came from: the file and its SHA-256, or "microphone".
    audio: str
    #: The model was unsure of its own words: the person should read them before sending.
    uncertain: bool = False


def command_phrases() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """RUDRA's spoken vocabulary: the verbs, and every registered application's name and aliases."""
    names = []
    for application in APPLICATIONS:
        names.append(application.name)
        names.extend(alias for alias in application.aliases if alias.casefold() != application.name.casefold())
    return VERBS, tuple(dict.fromkeys(names))


def vocabulary_prompt(hints: tuple[str, ...] = ()) -> str:
    """RUDRA's own words, and any the person asked to be spelt as written, as the short text the
    recogniser is told it is continuing ("Kaushik. Open Notepad. Close Calculator. RUDRA.").

    A hint is a context, never a script: a model that hears something else writes that.
    """
    _, names = command_phrases()
    sample = names[:6]
    parts = [", ".join(hints) + "." if hints else "",
             f"Open {sample[0]}." if sample else "", f"Close {sample[1]}." if len(sample) > 1 else "", "RUDRA."]
    return " ".join(part for part in parts if part)


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(summary, reason, stage="voice", data_changed=False, retry_safe=True,
                                next_options=("python -m app voice --audio request.wav --dry-run",
                                              'python -m app voice --say "Open Calculator." --audio new.wav'))


def check_audio(path: Path) -> bytes:
    """The audio file's bytes, refused unless it is a readable RIFF/WAVE file of at most 10 MiB."""
    if not path.is_file():
        raise refuse(f"{path} is not a file.", "Give a WAV file recorded or spoken earlier.")
    data = path.read_bytes()
    if len(data) > MAX_AUDIO_BYTES:
        raise refuse(f"{path} is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MiB.", "One utterance is enough.")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise refuse(f"{path} is not a WAV file.", "Only RIFF/WAVE audio is read.")
    return data


def _wav_seconds(data: bytes) -> float:
    """The length of a WAV file in seconds; an estimate from its size if `wave` cannot read it."""
    try:
        with wave.open(io.BytesIO(data)) as reader:
            return reader.getnframes() / float(reader.getframerate() or 16_000)
    except (wave.Error, EOFError, ZeroDivisionError):
        return len(data) / 32_000.0


def _wav_bytes(pcm: bytes) -> bytes:
    """16 kHz mono 16-bit PCM as a WAV file in memory (the recogniser reads WAV from its input)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(capture.SAMPLE_RATE)
        writer.writeframes(pcm)
    return buffer.getvalue()


def _transcript(heard: engine.Heard, parts: engine.Components, audio: str) -> Transcript:
    return Transcript(heard.text, "dictation" if heard.text else "", heard.confidence, parts.label, audio,
                      heard.uncertain)


def recognize_file(path: Path, hints: tuple[str, ...] = ()) -> Transcript:
    """One utterance from a WAV file. `hints` are words to spell as given (names, terms)."""
    data = check_audio(path)
    parts = engine.find_components()
    heard = engine.recognize(data, _wav_seconds(data), prompt=vocabulary_prompt(hints), components=parts)
    return _transcript(heard, parts, f"{path} (SHA-256 {hashlib.sha256(data).hexdigest()})")


BLOCKED_MICROPHONE = ("Windows is giving RUDRA silence from the microphone. Check that it is not muted and that "
                      "Windows allows desktop apps to use it (Settings > Privacy & security > Microphone).")


def listen(seconds: int = 10, *, cancel: threading.Event | None = None, finish: threading.Event | None = None,
           on_phase: Callable[[str], None] | None = None, hints: tuple[str, ...] = ()) -> Transcript:
    """ONE utterance from the default microphone, on the user's request; nothing is kept.

    Listening ends when the speaker has finished, after `seconds`, or when `finish` is set
    (the user pressed Stop: what was said so far is recognized). `cancel` discards everything
    and raises `SpeechCancelled`. `on_phase`, when given, is called from this thread with
    "listening" and then "recognizing" - the window shows them on the button. `hints` are
    words to spell as given (names, terms: `app.voice.words`).
    """
    parts = engine.find_components()  # a missing model is reported before the microphone is opened
    if on_phase:
        on_phase("listening")
    recording = capture.record(seconds, cancel=cancel, finish=finish)
    if recording.blocked:
        raise SpeechUnavailable(BLOCKED_MICROPHONE, "every sample was zero")
    source = "microphone (one utterance; not stored)"
    if recording.silent:
        return Transcript("", "", None, parts.label, source)
    if on_phase:
        on_phase("recognizing")
    heard = engine.recognize(_wav_bytes(recording.pcm), recording.seconds, cancel=cancel,
                             prompt=vocabulary_prompt(hints), components=parts)
    return _transcript(heard, parts, source)


# ------------------------------------------------------------------ speech output (Windows)

#: How often a cancellable run checks the cancellation flag and the overall timeout.
_POLL_SECONDS = 0.2


def _quote(text: str) -> str:
    """A PowerShell single-quoted string literal: nothing inside it is evaluated."""
    return "'" + text.replace("'", "''") + "'"


def _hidden_process() -> dict:
    """`Popen` options that keep PowerShell's console away from the user (see `process`)."""
    return hidden_process()


def _run(script: str, *, cancel: threading.Event | None = None) -> dict:
    # -EncodedCommand runs the script as one unit (stdin input would be read line by line).
    encoded = base64.b64encode(("$ProgressPreference = 'SilentlyContinue'\n" + script).encode("utf-16-le")).decode()
    try:
        process = subprocess.Popen(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            **_hidden_process(),
        )
    except FileNotFoundError:
        raise SpeechUnavailable("Speech output is not available on this computer.") from None
    deadline = time.monotonic() + TIMEOUT
    stdout = stderr = None
    while stdout is None:
        try:
            stdout, stderr = process.communicate(timeout=_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                process.kill()
                process.communicate()
                raise SpeechCancelled("speech was cancelled")
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise SpeechUnavailable("Speech output did not respond. Try again.", f"no answer within {TIMEOUT} s") from None
    lines = [line for line in stdout.splitlines() if line.strip()]
    if process.returncode != 0 or not lines:
        detail = " ".join(stderr.split())[:300] or f"exit code {process.returncode}"
        raise SpeechUnavailable(_explain_failure(detail), detail)
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        raise SpeechUnavailable("Speech output gave an answer RUDRA could not read. Try again.",
                                lines[-1][:200]) from None


def _explain_failure(detail: str) -> str:
    """What a failure of Windows' speech output means for the person; the engine's own error
    text names a PowerShell call, which stays in `detail` for the log."""
    text = detail.casefold()
    if "voice" in text or "synthesizer" in text or "culture" in text:
        return ("Windows has no usable speech voice installed, so RUDRA cannot speak. A voice can be added in "
                "Settings > Time & language > Speech.")
    return "RUDRA could not use Windows speech output on this computer."


def synthesize(text: str, path: Path, voice: str | None = None) -> Path:
    """Speak `text` into a NEW WAV file (never overwritten)."""
    if path.exists():
        raise refuse(f"{path} exists.", "Speech is written only to a new file; nothing is overwritten.")
    if not text.strip():
        raise refuse("There is nothing to say.", "Give the text to speak.")
    select = "" if voice is None else f"$speaker.SelectVoice({_quote(voice)})\n"
    _run("Add-Type -AssemblyName System.Speech\n"
         "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer\n" + select +
         f"$speaker.SetOutputToWaveFile({_quote(str(path.resolve()))})\n"
         f"$speaker.Speak({_quote(text)})\n$speaker.Dispose()\n"
         "@{written = $true} | ConvertTo-Json -Compress\n")
    return path
