"""The offline speech recogniser: OpenAI Whisper, run by whisper.cpp as a separate program.

RUDRA does not decode speech itself. It runs `whisper-cli.exe` (whisper.cpp, MIT) with a
Whisper model (OpenAI, MIT) and a Silero voice-activity model (MIT) from the `speech` folder
beside the program - installed with RUDRA, or put there by `windows\\fetch_speech.py` for a
source checkout. Each utterance is one run of that program:

* the audio goes to its standard input, so it is never written to disk;
* it starts with no window of its own, at below-normal priority so RUDRA's window stays
  responsive, and ends when the text is out - its memory (about half a gigabyte for the
  small model) is released straight away rather than held while RUDRA idles;
* it gets a model file and audio and nothing else. It makes no network connection and needs
  no account;
* only the result - the words, and how sure the model was of each - is written to a private
  temporary folder, read back and deleted.

What it hears is the model's best reading, never a guess RUDRA completes. When it hears no
speech it returns nothing; it does not invent words for silence (a known weakness of the
model, answered by the voice-activity model that discards non-speech before decoding).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.voice.errors import SpeechCancelled, SpeechUnavailable
from app.voice.process import hidden_process

FOLDER_ENV = "RUDRA_SPEECH_DIR"
MANIFEST = "manifest.json"
#: Where an utterance gets this long before the program is given up on, per 30 s of audio.
SECONDS_PER_WINDOW = 25.0
BASE_TIMEOUT = 40.0
_POLL_SECONDS = 0.1
#: Windows priority class: below normal, so the window stays responsive while words are found.
BELOW_NORMAL_PRIORITY = 0x00004000

#: A reading is shown as uncertain when the model itself was unsure of it: a word it gave under
#: UNCERTAIN_WORD, or an average under UNCERTAIN_MEAN. These are the model's own probabilities;
#: the values come from the measurements in docs/VOICE.md (on 96 test recordings a word under 0.5
#: marked 13 of the 17 readings with a wrong word and 11 of the 79 correct ones; no reading's
#: average was under 0.7, so the average catches only gross failure).
UNCERTAIN_WORD = 0.5
UNCERTAIN_MEAN = 0.60


def speech_folder() -> Path:
    """The `speech` folder: RUDRA_SPEECH_DIR, else beside the packaged program, else the
    source checkout's own `speech` folder."""
    override = os.environ.get(FOLDER_ENV)
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "speech"
    return Path(__file__).resolve().parents[2] / "speech"


@dataclass(frozen=True, slots=True)
class Components:
    """The three files a recognition needs, and what to call the recogniser."""

    folder: Path
    program: Path
    model: Path
    vad: Path | None
    #: "OpenAI Whisper small.en (whisper.cpp b5130), offline" - shown with every transcript.
    label: str
    threads: int
    manifest: dict


def _install_hint() -> str:
    return ("Reinstall RUDRA with its installer. From a source checkout, run: "
            "python windows\\fetch_speech.py")


def find_components(folder: Path | None = None) -> Components:
    """The recogniser's files, or `SpeechUnavailable` saying which are missing."""
    folder = folder or speech_folder()
    manifest_path = folder / MANIFEST
    if not manifest_path.is_file():
        raise SpeechUnavailable(f"Speech recognition is not installed. {_install_hint()}",
                                f"no {MANIFEST} in {folder}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        program = folder / manifest["program"]["file"]
        model = folder / manifest["model"]["file"]
        vad = folder / manifest["vad"]["file"] if manifest.get("vad") else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SpeechUnavailable(f"RUDRA's speech files are damaged. {_install_hint()}",
                                f"unreadable {MANIFEST}: {exc}") from None
    for path, entry in ((program, manifest["program"]), (model, manifest["model"]),
                        *(((vad, manifest["vad"]),) if vad else ())):
        if not path.is_file() or (entry.get("size") and path.stat().st_size != entry["size"]):
            raise SpeechUnavailable(f"A speech file ({path.name}) is missing or damaged. {_install_hint()}",
                                    f"{path} {'missing' if not path.is_file() else 'has the wrong size'}")
    threads = int(manifest.get("threads") or max(2, min(4, (os.cpu_count() or 4) // 2)))
    return Components(folder, program, model, vad, str(manifest.get("recognizer") or "Whisper"), threads, manifest)


# ------------------------------------------------------------------ what was heard


@dataclass(frozen=True, slots=True)
class Heard:
    """The recogniser's reading of one utterance."""

    #: The words; "" when no speech was heard.
    text: str
    #: The mean probability the model gave its tokens (0..1), or None when there were none.
    confidence: float | None
    #: The least probable word's probability, or None.
    weakest: float | None
    seconds: float

    @property
    def uncertain(self) -> bool:
        if not self.text or self.confidence is None:
            return False
        return self.confidence < UNCERTAIN_MEAN or (self.weakest is not None and self.weakest < UNCERTAIN_WORD)


#: Whisper's non-speech annotations: [BLANK_AUDIO], (music), *sighs*, [ Silence ].
_ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")
_SPECIAL_TOKEN = re.compile(r"^\[_.*\]$")  # [_BEG_], [_TT_150]


def parse_result(document: dict, seconds: float = 0.0) -> Heard:
    """The words and their probabilities from the program's `-ojf` JSON output."""
    pieces: list[str] = []
    probabilities: list[float] = []
    for segment in document.get("transcription") or ():
        text = _ANNOTATION.sub("", str(segment.get("text", "")))
        if not text.strip():
            continue
        pieces.append(text.strip())
        for token in segment.get("tokens") or ():
            word = str(token.get("text", ""))
            if _SPECIAL_TOKEN.match(word) or not word.strip(" ,.?!;:'\"-"):
                continue
            probability = token.get("p")
            if isinstance(probability, (int, float)):
                probabilities.append(float(probability))
    text = " ".join(" ".join(pieces).split())
    if not text:
        return Heard("", None, None, seconds)
    mean = sum(probabilities) / len(probabilities) if probabilities else None
    return Heard(text, None if mean is None else round(mean, 3),
                 None if not probabilities else round(min(probabilities), 3), seconds)


# ------------------------------------------------------------------ running the program


#: Whisper reads audio in 30 s windows of 1500 positions (50 a second) and, left alone, spends
#: the same effort on a three-second question as on thirty seconds of speech. The window is
#: sized to the utterance instead - its length plus a margin of silence - which cuts the time
#: several-fold with no change in what is heard (measured in docs/VOICE.md). Over
#: LONG_AUDIO seconds the full window is used.
CONTEXT_MARGIN_SECONDS = 1.5
CONTEXT_FLOOR = 200
FULL_CONTEXT = 1500
LONG_AUDIO = 22.0


def audio_context(audio_seconds: float) -> int | None:
    """The encoder window for an utterance of this length, or None for the full 30 s window."""
    if audio_seconds <= 0 or audio_seconds >= LONG_AUDIO:
        return None
    return min(FULL_CONTEXT, max(CONTEXT_FLOOR, int((audio_seconds + CONTEXT_MARGIN_SECONDS) * 50)))


def _timeout_for(audio_seconds: float) -> float:
    windows = max(1.0, -(-audio_seconds // 30))
    return BASE_TIMEOUT + SECONDS_PER_WINDOW * windows


def _explain(detail: str, code: int | None) -> str:
    """What a failed run means for the person; the program's own words stay in `detail`."""
    text = detail.casefold()
    if (code in (0xC0000017, 0xC0000005, 0xC0000409, 3221225495, 3221225477, 3221226505) or "bad_alloc" in text
            or "failed to allocate" in text or "out of memory" in text or "not enough memory" in text):
        return ("There was not enough free memory to recognise speech. Close some programs and try again.")
    return "RUDRA's speech recognition stopped unexpectedly. Try again."


def recognize(wav: bytes, audio_seconds: float, *, cancel: threading.Event | None = None,
              prompt: str = "", components: Components | None = None) -> Heard:
    """Run the recogniser over WAV bytes (16-bit PCM, any rate the program reads).

    Raises `SpeechCancelled` when `cancel` is set while waiting, and `SpeechUnavailable`
    when the files are missing, the program cannot start, runs out of memory, fails or
    does not answer in time.
    """
    parts = components or find_components()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rudra-voice-") as scratch:
        base = Path(scratch) / "result"
        # Greedy decoding (-bs 1 -bo 1): measured to give the same words as a five-way beam search
        # (within a word or two in 756) and, with a context prompt, to avoid a threefold slowdown
        # that the beam search showed on very short utterances (docs/VOICE.md).
        command = [str(parts.program), "-m", str(parts.model), "-f", "-", "-t", str(parts.threads), "-l", "en",
                   "-nt", "-np", "-sns", "-bs", "1", "-bo", "1", "-ojf", "-of", str(base)]
        window = audio_context(audio_seconds)
        if window is not None:
            command += ["-ac", str(window)]
        if parts.vad is not None:
            command += ["--vad", "-vm", str(parts.vad)]
        if prompt:
            command += ["--prompt", prompt]
        options = hidden_process()
        options["stdin"] = subprocess.PIPE
        options["creationflags"] = options.get("creationflags", 0) | BELOW_NORMAL_PRIORITY
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(parts.folder),
                                       **options)
        except OSError as exc:
            if getattr(exc, "winerror", None) in (5, 225, 1260):  # access denied, virus found, blocked by policy
                raise SpeechUnavailable("Windows blocked RUDRA's speech program. Allow the speech folder in your "
                                        "security software, then try again.", str(exc)) from None
            raise SpeechUnavailable(f"RUDRA could not start its speech program. {_install_hint()}",
                                    str(exc)) from None
        deadline = started + _timeout_for(audio_seconds)
        pending: bytes | None = wav
        while True:
            try:
                _, stderr = process.communicate(input=pending, timeout=_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pending = None
                if cancel is not None and cancel.is_set():
                    process.kill()
                    process.communicate()
                    raise SpeechCancelled("listening was cancelled") from None
                if time.monotonic() >= deadline:
                    process.kill()
                    process.communicate()
                    raise SpeechUnavailable("Speech recognition took too long and was stopped. Try a shorter "
                                            "request.", f"no answer within {_timeout_for(audio_seconds):.0f} s") from None
        detail = " ".join(stderr.decode("utf-8", "replace").split())[-300:]
        result = Path(f"{base}.json")
        if process.returncode != 0 or not result.is_file():
            raise SpeechUnavailable(_explain(detail, process.returncode),
                                    detail or f"exit code {process.returncode}")
        try:
            document = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SpeechUnavailable("Speech recognition gave an answer RUDRA could not read. Try again.",
                                    "unreadable result") from None
    return parse_result(document, time.monotonic() - started)
