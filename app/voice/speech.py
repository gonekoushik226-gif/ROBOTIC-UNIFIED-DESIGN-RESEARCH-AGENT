"""Speech recognition and synthesis through Windows' own speech engine (ADR 0052 P20-2,
P20-6). No dependency: `System.Speech` (SAPI) ships with Windows, and is reached through
Windows PowerShell with a script RUDRA writes, never with text a user or a document
supplied as code.

    recognize_file(path)   a WAV file -> the transcript, the grammar that matched, the
                           confidence and the recognizer
    listen(seconds)        ONE utterance from the default microphone, only when the user
                           asks (`voice --listen`); nothing is kept (section 142)
    synthesize(text, path) text -> a new WAV file (speech output, optional)

Two grammars are loaded: RUDRA's **command grammar** - its action verbs and the registered
applications' names (data, from the registry) - and free **dictation** as the fallback. The
result says which one matched; a dictation result is labelled less reliable. No confidence
threshold is invented: the confidence is reported as the engine gives it.
"""

import base64
import hashlib
import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.applications import APPLICATIONS
from app.core.errors import InvalidInputError

#: The verbs a spoken command may begin with (the interpreter's application rules).
VERBS = ("open", "launch", "start", "run", "close", "quit", "exit", "stop")
CULTURE = "en-US"
TIMEOUT = 90
POWERSHELL = "powershell.exe"
MAX_AUDIO_BYTES = 10 * 1024 * 1024


class SpeechUnavailable(Exception):
    """The speech engine could not be used: not Windows, no recognizer, or it failed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SpeechCancelled(Exception):
    """Listening was stopped by the caller (the user pressed Stop) before it finished."""


@dataclass(frozen=True, slots=True)
class Transcript:
    #: What was recognized; "" when nothing was.
    text: str
    #: "commands" (RUDRA's command grammar), "dictation" (free speech), or "" (nothing).
    grammar: str
    confidence: float | None
    recognizer: str
    #: Where the audio came from: the file and its SHA-256, or "microphone".
    audio: str


def command_phrases() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The command grammar: the verbs, and every registered application's name and aliases."""
    names = []
    for application in APPLICATIONS:
        names.append(application.name)
        names.extend(alias for alias in application.aliases if alias.casefold() != application.name.casefold())
    return VERBS, tuple(dict.fromkeys(names))


def _quote(text: str) -> str:
    """A PowerShell single-quoted string literal: nothing inside it is evaluated."""
    return "'" + text.replace("'", "''") + "'"


def _grammar_script() -> str:
    verbs, names = command_phrases()
    return (
        "Add-Type -AssemblyName System.Speech\n"
        f"$culture = New-Object System.Globalization.CultureInfo({_quote(CULTURE)})\n"
        "$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)\n"
        "$builder = New-Object System.Speech.Recognition.GrammarBuilder\n"
        "$builder.Culture = $culture\n"
        f"$builder.Append((New-Object System.Speech.Recognition.Choices(@({', '.join(map(_quote, verbs))}))))\n"
        f"$builder.Append((New-Object System.Speech.Recognition.Choices(@({', '.join(map(_quote, names))}))))\n"
        "$commands = New-Object System.Speech.Recognition.Grammar($builder)\n"
        "$commands.Name = 'commands'\n"
        "$engine.LoadGrammar($commands)\n"
        "$dictation = New-Object System.Speech.Recognition.DictationGrammar\n"
        "$dictation.Name = 'dictation'\n"
        "$engine.LoadGrammar($dictation)\n"
    )


_REPORT = (
    "if ($result) { $out = @{text = $result.Text; grammar = $result.Grammar.Name; confidence = $result.Confidence} }\n"
    "else { $out = @{text = ''; grammar = ''; confidence = $null} }\n"
    "$out.recognizer = $engine.RecognizerInfo.Description\n"
    "$engine.Dispose()\n"
    "$out | ConvertTo-Json -Compress\n"
)


#: How often a cancellable run checks the cancellation flag and the overall timeout.
_POLL_SECONDS = 0.2


def _run(script: str, *, cancel: threading.Event | None = None) -> dict:
    # -EncodedCommand runs the script as one unit (stdin input would be read line by line).
    encoded = base64.b64encode(("$ProgressPreference = 'SilentlyContinue'\n" + script).encode("utf-16-le")).decode()
    try:
        process = subprocess.Popen(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        raise SpeechUnavailable("Windows PowerShell is not available on this machine") from None
    deadline = time.monotonic() + TIMEOUT
    stdout = stderr = None
    while stdout is None:
        try:
            stdout, stderr = process.communicate(timeout=_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                process.kill()
                process.communicate()
                raise SpeechCancelled("listening was cancelled")
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise SpeechUnavailable(f"the speech engine did not answer within {TIMEOUT} s") from None
    lines = [line for line in stdout.splitlines() if line.strip()]
    if process.returncode != 0 or not lines:
        detail = " ".join(stderr.split())[:300] or f"exit code {process.returncode}"
        raise SpeechUnavailable(f"the speech engine failed: {detail}")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        raise SpeechUnavailable(f"the speech engine's answer could not be read: {lines[-1][:200]}") from None


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
        raise refuse(f"{path} is not a WAV file.", "Only RIFF/WAVE audio is read (ADR 0052 P20-6).")
    return data


def recognize_file(path: Path) -> Transcript:
    """One utterance from a WAV file."""
    data = check_audio(path)
    result = _run(_grammar_script() + f"$engine.SetInputToWaveFile({_quote(str(path.resolve()))})\n"
                  "$result = $engine.Recognize()\n" + _REPORT)
    return Transcript(result.get("text") or "", result.get("grammar") or "", result.get("confidence"),
                      result.get("recognizer") or "", f"{path} (SHA-256 {hashlib.sha256(data).hexdigest()})")


def listen(seconds: int = 10, *, cancel: threading.Event | None = None) -> Transcript:
    """ONE utterance from the default microphone, on the user's request; nothing is kept.

    `cancel`, when given, is checked while RUDRA waits for the engine; setting it (the
    user pressed Stop) ends the wait and raises `SpeechCancelled` instead of a transcript.
    """
    result = _run(_grammar_script() + "$engine.SetInputToDefaultAudioDevice()\n"
                  f"$result = $engine.Recognize([TimeSpan]::FromSeconds({int(seconds)}))\n" + _REPORT, cancel=cancel)
    return Transcript(result.get("text") or "", result.get("grammar") or "", result.get("confidence"),
                      result.get("recognizer") or "", "microphone (one utterance; not stored)")


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
