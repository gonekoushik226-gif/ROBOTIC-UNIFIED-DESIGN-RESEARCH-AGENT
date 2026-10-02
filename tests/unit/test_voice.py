"""Voice (ADR 0052, ADR 0058): offline speech recognition with Whisper, and Windows' voice for output.

What every test holds voice to: the vocabulary RUDRA gives the recogniser is its own data (the
registry's applications and the interpreter's verbs); nothing a user or a document supplies is
ever evaluated by the speech bridge; audio is checked before use; the recogniser runs hidden,
reads its audio from standard input (never a file) and gives its memory back when done; a
transcript is ordinary text for the same pipeline, so voice never authorizes; and the
microphone is never opened here.

Two defects found in manual testing are pinned: Windows' own dictation turned "My name is
Kaushik" into unrelated words (replaced by Whisper, measured in docs/VOICE.md), and a
recognition that never ended had to be cancellable. Tests that need the real recogniser
(`speech\\` installed by `windows\\fetch_speech.py`) are skipped, with that reason, where it is
not - never passed silently. Speech output tests use Windows' own voice and skip likewise.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.actions import SimulatedPlatform
from app.applications import APPLICATIONS
from app.core.errors import InvalidInputError
from app.orchestration import Outcome, Pipeline
from app.security import Decision
from app.voice import (
    SpeechCancelled,
    SpeechUnavailable,
    check_audio,
    command_phrases,
    recognize_file,
    synthesize,
    vocabulary_prompt,
)
from app.voice import engine
from app.voice.speech import _quote
from app.voice.speech import _run as _speech_run

FIXED = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


class _LiveLike(SimulatedPlatform):
    """A simulated computer the permission engine treats as live."""

    live = True


def _recogniser_available() -> bool:
    try:
        engine.find_components()
    except SpeechUnavailable:
        return False
    return True


needs_recogniser = pytest.mark.skipif(not _recogniser_available(),
                                      reason="the speech recogniser is not installed (python windows\\fetch_speech.py)")


def _voice_available(tmp_path_factory) -> bool:
    try:
        synthesize("Open Calculator.", tmp_path_factory.mktemp("probe") / "probe.wav")
    except SpeechUnavailable:
        return False
    return True


@pytest.fixture(scope="module")
def spoken(tmp_path_factory) -> dict[str, Path]:
    """Requests spoken by the Windows voice into WAV files (22 kHz, as Windows writes them), and silence."""
    if not _recogniser_available():
        pytest.skip("the speech recogniser is not installed")
    if not _voice_available(tmp_path_factory):
        pytest.skip("Windows' speech output is not available on this machine")
    folder = tmp_path_factory.mktemp("speech")
    files = {"open": synthesize("Open Calculator.", folder / "open.wav"),
             "close": synthesize("Close Notepad.", folder / "close.wav"),
             "name": synthesize("My name is Kaushik.", folder / "name.wav"),
             "question": synthesize("What is the resonant frequency of an LC circuit?", folder / "question.wav")}
    silence = folder / "silence.wav"
    with wave.open(str(silence), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 16000 * 2)
    files["silence"] = silence
    return files


# ------------------------------------------------------------ the vocabulary


def test_the_vocabulary_is_rudras_data():
    verbs, names = command_phrases()
    assert verbs[:2] == ("open", "launch") and "close" in verbs
    for application in APPLICATIONS:
        assert application.name in names
    prompt = vocabulary_prompt()
    assert prompt.endswith("RUDRA.") and "Open " in prompt and "Close " in prompt
    assert len(prompt) < 120  # a hint, not a script: the model reads at most half its context from it


# ------------------------------------------------------------ finding the recogniser's files


def _install(folder: Path, *, model_size: int = 5, with_vad: bool = True, label: str = "Test recogniser") -> Path:
    (folder / "models").mkdir(parents=True)
    (folder / "whisper-cli.exe").write_bytes(b"MZ")
    (folder / "models" / "model.bin").write_bytes(b"m" * model_size)
    manifest = {"format": 1, "recognizer": label, "threads": 3,
                "program": {"file": "whisper-cli.exe", "size": 2},
                "model": {"file": "models/model.bin", "size": 5}}
    if with_vad:
        (folder / "models" / "vad.bin").write_bytes(b"v")
        manifest["vad"] = {"file": "models/vad.bin", "size": 1}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_the_recognisers_files_are_found_from_their_manifest(tmp_path):
    parts = engine.find_components(_install(tmp_path / "speech"))
    assert parts.label == "Test recogniser" and parts.threads == 3
    assert parts.program.name == "whisper-cli.exe" and parts.model.name == "model.bin" and parts.vad.name == "vad.bin"


@pytest.mark.parametrize("damage, mentions", [
    ("nothing", "not installed"),
    ("manifest", "damaged"),
    ("model", "model.bin"),
    ("size", "model.bin"),
    ("program", "whisper-cli.exe"),
])
def test_missing_or_damaged_files_are_reported_plainly_with_the_way_to_fix_them(tmp_path, damage, mentions):
    folder = tmp_path / "speech"
    if damage != "nothing":
        _install(folder, model_size=9 if damage == "size" else 5)
    if damage == "manifest":
        (folder / "manifest.json").write_text("{not json", encoding="utf-8")
    if damage == "model":
        (folder / "models" / "model.bin").unlink()
    if damage == "program":
        (folder / "whisper-cli.exe").unlink()
    with pytest.raises(SpeechUnavailable) as raised:
        engine.find_components(folder)
    assert mentions in raised.value.reason and "fetch_speech.py" in raised.value.reason
    assert "Traceback" not in raised.value.reason


def test_the_folder_can_be_chosen_with_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(engine.FOLDER_ENV, str(tmp_path / "elsewhere"))
    assert engine.speech_folder() == tmp_path / "elsewhere"
    monkeypatch.delenv(engine.FOLDER_ENV)
    assert engine.speech_folder().name == "speech"


# ------------------------------------------------------------ what the recogniser says


def _result(*segments: tuple[str, list[tuple[str, float]]]) -> dict:
    return {"transcription": [{"text": text, "tokens": [{"text": word, "p": p} for word, p in tokens]}
                              for text, tokens in segments]}


def test_the_words_and_the_models_own_confidence_are_read_from_its_result():
    heard = engine.parse_result(_result((" My name is Koshik.", [(" My", 0.9), (" name", 0.95), (" is", 0.99),
                                                                  (" Kosh", 0.5), ("ik", 0.6), (".", 0.2),
                                                                  ("[_TT_150]", 0.1)])))
    assert heard.text == "My name is Koshik."
    assert heard.confidence == pytest.approx(0.79, abs=0.01)  # the full stop and the special token are not words
    assert heard.weakest == 0.5 and not heard.uncertain


def test_a_reading_the_model_was_unsure_of_is_marked_uncertain():
    heard = engine.parse_result(_result((" Like name is go seek.", [(" Like", 0.3), (" name", 0.5), (" is", 0.6),
                                                                      (" go", 0.2), (" seek", 0.3)])))
    assert heard.text == "Like name is go seek." and heard.uncertain


def test_one_word_the_model_doubted_is_enough_to_ask_the_person_to_check():
    """A name is the usual case: most of the sentence is certain, one word is not."""
    doubted = engine.parse_result(_result((" My name is Koshik.", [(" My", 0.97), (" name", 0.98), (" is", 0.99),
                                                                   (" Kosh", 0.31), ("ik", 0.88)])))
    assert doubted.confidence > engine.UNCERTAIN_MEAN and doubted.weakest == 0.31 and doubted.uncertain
    sure = engine.parse_result(_result((" My name is John.", [(" My", 0.97), (" name", 0.98), (" is", 0.99),
                                                                (" John", 0.52)])))
    assert not sure.uncertain                      # 0.52 is not under the line
    assert not engine.Heard("", None, None, 0.1).uncertain      # nothing heard: nothing to check


@pytest.mark.parametrize("text", [" [BLANK_AUDIO]", " (silence)", " [ Silence ]", " *sighs*", "  ", ""])
def test_non_speech_annotations_are_not_words(text):
    heard = engine.parse_result(_result((text, [(text, 0.9)])))
    assert heard.text == "" and heard.confidence is None and not heard.uncertain


def test_annotations_are_dropped_from_a_sentence_and_segments_are_joined():
    heard = engine.parse_result(_result((" Open the door [BLANK_AUDIO]", [(" Open", 0.9)]),
                                        (" (laughs) and close it.", [(" and", 0.8)])))
    assert heard.text == "Open the door and close it."


def test_a_missing_or_empty_result_is_nothing_heard():
    assert engine.parse_result({}).text == "" and engine.parse_result({"transcription": []}).text == ""


# ------------------------------------------------------------ running the recogniser (no real program)


class _FakeProcess:
    returncode = 0

    def __init__(self, command, writes: str | None = None, **options):
        self.command, self.options = command, options
        self.input = None
        self.killed = False
        if writes is not None:
            base = command[command.index("-of") + 1]
            Path(f"{base}.json").write_text(writes, encoding="utf-8")

    def communicate(self, input=None, timeout=None):
        self.input = input if input is not None else self.input
        return b"", b""

    def kill(self):
        self.killed = True


def _components(tmp_path) -> engine.Components:
    return engine.find_components(_install(tmp_path / "speech"))


def _hearing(text: str):
    return json.dumps(_result((f" {text}", [(f" {text}", 0.9)])))


def test_the_recogniser_is_given_its_audio_on_standard_input_and_runs_hidden_and_politely(monkeypatch, tmp_path):
    started = {}

    def fake_popen(command, **options):
        process = _FakeProcess(command, _hearing("Open Calculator."), **options)
        started["process"] = process
        return process

    monkeypatch.setattr(engine.subprocess, "Popen", fake_popen)
    heard = engine.recognize(b"RIFF....WAVE", 1.5, prompt="Open Notepad. RUDRA.", components=_components(tmp_path))
    process = started["process"]
    assert heard.text == "Open Calculator."
    command, options = process.command, process.options
    assert command[command.index("-f") + 1] == "-"          # the audio is read from standard input: never a file
    assert process.input == b"RIFF....WAVE"
    assert options["stdin"] is subprocess.PIPE
    assert options["creationflags"] & subprocess.CREATE_NO_WINDOW       # no console window
    assert options["creationflags"] & engine.BELOW_NORMAL_PRIORITY      # the window stays responsive
    assert options["startupinfo"].wShowWindow == subprocess.SW_HIDE
    assert "--vad" in command and command[command.index("-vm") + 1].endswith("vad.bin")
    assert command[command.index("--prompt") + 1] == "Open Notepad. RUDRA."
    assert command[command.index("-t") + 1] == "3" and command[command.index("-l") + 1] == "en"
    assert "-sns" in command  # non-speech tokens suppressed
    assert command[command.index("-bs") + 1] == "1" and command[command.index("-bo") + 1] == "1"  # greedy decoding


def test_a_short_utterance_is_given_a_window_sized_to_it_and_a_long_one_the_full_window(monkeypatch, tmp_path):
    assert engine.audio_context(3.0) == int((3.0 + engine.CONTEXT_MARGIN_SECONDS) * 50)
    assert engine.audio_context(0.2) == engine.CONTEXT_FLOOR          # never smaller than the floor
    assert engine.audio_context(engine.LONG_AUDIO) is None and engine.audio_context(45) is None
    assert engine.audio_context(0) is None
    assert engine.audio_context(21.9) <= engine.FULL_CONTEXT
    commands = []

    def fake_popen(command, **options):
        commands.append(command)
        return _FakeProcess(command, _hearing("x"), **options)

    monkeypatch.setattr(engine.subprocess, "Popen", fake_popen)
    parts = _components(tmp_path)
    engine.recognize(b"x", 3.0, components=parts)
    engine.recognize(b"x", 45.0, components=parts)
    assert commands[0][commands[0].index("-ac") + 1] == str(engine.audio_context(3.0))
    assert "-ac" not in commands[1]


def test_nothing_the_recogniser_leaves_behind_survives_the_recognition(monkeypatch, tmp_path):
    seen = {}

    def fake_popen(command, **options):
        seen["scratch"] = Path(command[command.index("-of") + 1]).parent
        return _FakeProcess(command, _hearing("hello"), **options)

    monkeypatch.setattr(engine.subprocess, "Popen", fake_popen)
    engine.recognize(b"x", 1.0, components=_components(tmp_path))
    assert seen["scratch"].name.startswith("rudra-voice-") and not seen["scratch"].exists()


def test_the_recogniser_needs_no_prompt_and_no_vad_model_to_run(monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(engine.subprocess, "Popen",
                        lambda command, **o: started.setdefault("p", _FakeProcess(command, _hearing("x"), **o)))
    parts = engine.find_components(_install(tmp_path / "speech", with_vad=False))
    engine.recognize(b"x", 1.0, components=parts)
    assert "--prompt" not in started["p"].command and "--vad" not in started["p"].command


@pytest.mark.parametrize("stderr, mentions", [
    ("whisper_init: failed to allocate 512 MiB: std::bad_alloc", "memory"),
    ("something unforeseen happened", "stopped unexpectedly"),
])
def test_a_failed_run_is_explained_without_the_programs_own_words(monkeypatch, tmp_path, stderr, mentions):
    class Failing(_FakeProcess):
        returncode = 1

        def communicate(self, input=None, timeout=None):
            return b"", stderr.encode()

    monkeypatch.setattr(engine.subprocess, "Popen", lambda command, **o: Failing(command, **o))
    with pytest.raises(SpeechUnavailable) as raised:
        engine.recognize(b"x", 1.0, components=_components(tmp_path))
    assert mentions in raised.value.reason and "bad_alloc" not in raised.value.reason
    assert stderr.split(":")[0].split()[0] in raised.value.detail  # kept for the log


def test_a_program_that_cannot_start_is_reported(monkeypatch, tmp_path):
    def blocked(command, **options):
        error = OSError("Operation did not complete successfully because the file contains a virus")
        error.winerror = 225
        raise error

    monkeypatch.setattr(engine.subprocess, "Popen", blocked)
    with pytest.raises(SpeechUnavailable) as raised:
        engine.recognize(b"x", 1.0, components=_components(tmp_path))
    assert "blocked" in raised.value.reason and "security software" in raised.value.reason


def test_an_unreadable_result_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(engine.subprocess, "Popen", lambda command, **o: _FakeProcess(command, "{not json", **o))
    with pytest.raises(SpeechUnavailable) as raised:
        engine.recognize(b"x", 1.0, components=_components(tmp_path))
    assert "could not read" in raised.value.reason


def test_a_recognition_that_never_ends_is_stopped_by_the_timeout(monkeypatch, tmp_path):
    class Hanging(_FakeProcess):
        def communicate(self, input=None, timeout=None):
            if self.killed:
                return b"", b""
            raise subprocess.TimeoutExpired("whisper-cli", timeout)

    holder = {}
    monkeypatch.setattr(engine.subprocess, "Popen", lambda command, **o: holder.setdefault("p", Hanging(command, **o)))
    monkeypatch.setattr(engine, "BASE_TIMEOUT", 0.05)
    monkeypatch.setattr(engine, "SECONDS_PER_WINDOW", 0.0)
    with pytest.raises(SpeechUnavailable) as raised:
        engine.recognize(b"x", 1.0, components=_components(tmp_path))
    assert holder["p"].killed and "too long" in raised.value.reason


def test_setting_cancel_stops_a_recognition_promptly_and_kills_the_program(monkeypatch, tmp_path):
    """The window's Stop button sets this event: the wait ends at once, not after the program's own time."""
    class Hanging(_FakeProcess):
        def communicate(self, input=None, timeout=None):
            if self.killed:
                return b"", b""
            time.sleep(min(timeout or 0.05, 0.05))
            raise subprocess.TimeoutExpired("whisper-cli", timeout)

    holder = {}
    monkeypatch.setattr(engine.subprocess, "Popen", lambda command, **o: holder.setdefault("p", Hanging(command, **o)))
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(SpeechCancelled):
        engine.recognize(b"x", 1.0, cancel=cancel, components=_components(tmp_path))
    assert holder["p"].killed and time.monotonic() - started < 5


def test_a_long_recording_is_given_longer_before_the_program_is_given_up_on():
    assert engine._timeout_for(5) == engine.BASE_TIMEOUT + engine.SECONDS_PER_WINDOW
    assert engine._timeout_for(65) == engine.BASE_TIMEOUT + 3 * engine.SECONDS_PER_WINDOW


# ------------------------------------------------------------ speech output (Windows' voice)


@pytest.mark.parametrize("text", ["it's", "'; Remove-Item C:\\ -Recurse; '", "$(Get-Process)"])
def test_text_reaches_powershell_only_as_a_single_quoted_literal(text):
    quoted = _quote(text)
    assert quoted.startswith("'") and quoted.endswith("'")
    assert quoted[1:-1].replace("''", "").count("'") == 0  # every inner quote is doubled


def test_setting_cancel_stops_a_long_running_speech_output():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(SpeechCancelled):
        _speech_run("Start-Sleep -Seconds 30\n'{}' | Write-Output\n", cancel=cancel)
    assert time.monotonic() - started < 5


def test_without_cancelling_a_quick_script_still_returns_normally():
    assert _speech_run("'{\"text\": \"ok\"}' | Write-Output\n") == {"text": "ok"}


class _FakePowerShell:
    returncode = 0

    def communicate(self, timeout=None):
        return '{"text": "ok"}\n', ""


def test_speech_output_never_opens_a_console_window(monkeypatch):
    """RUDRA's window has no console: PowerShell started without CREATE_NO_WINDOW gets its own,
    a black box that appears whenever speech is made."""
    from app.voice import speech

    started = {}

    def fake_popen(command, **options):
        started["command"], started["options"] = command, options
        return _FakePowerShell()

    monkeypatch.setattr(speech.subprocess, "Popen", fake_popen)
    assert speech._run("'{}' | Write-Output\n") == {"text": "ok"}
    options = started["options"]
    assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert options["startupinfo"].wShowWindow == subprocess.SW_HIDE
    assert options["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert options["stdin"] is subprocess.DEVNULL
    # The window is hidden by the process options above. "-WindowStyle Hidden -EncodedCommand" is the
    # invocation security software treats as malicious, and flagged the unsigned program for it.
    assert "-WindowStyle" not in started["command"] and "-EncodedCommand" in started["command"]


@pytest.mark.parametrize("stderr, mentions", [
    ("Exception calling \"SelectVoice\": No voice installed", "voice"),
    ("something unforeseen happened inside the engine", "speech output"),
])
def test_a_speech_output_failure_is_explained_without_exposing_powershell(monkeypatch, stderr, mentions):
    from app.voice import speech

    class Failing(_FakePowerShell):
        returncode = 1

        def communicate(self, timeout=None):
            return "", stderr

    monkeypatch.setattr(speech.subprocess, "Popen", lambda *a, **k: Failing())
    with pytest.raises(SpeechUnavailable) as raised:
        speech._run("irrelevant\n")
    message = raised.value.reason
    assert mentions in message.lower()
    assert "Exception calling" not in message and "PowerShell" not in message and "powershell" not in message


def test_speech_finds_powershell_by_its_fixed_location_not_the_path():
    """A trimmed PATH must not make speech output look broken: PowerShell is found where Windows keeps it."""
    from app.voice import speech

    shell = Path(speech.POWERSHELL)
    assert shell.is_absolute() and shell.is_file() and shell.name.lower() == "powershell.exe"


# ------------------------------------------------------------ audio


def test_audio_that_is_not_a_wav_file_is_refused(tmp_path):
    with pytest.raises(InvalidInputError):
        check_audio(tmp_path / "missing.wav")
    text = tmp_path / "notes.wav"
    text.write_text("not audio", encoding="utf-8")
    with pytest.raises(InvalidInputError):
        check_audio(text)


def test_audio_larger_than_ten_mebibytes_is_refused(tmp_path):
    big = tmp_path / "big.wav"
    big.write_bytes(b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * (10 * 1024 * 1024))
    with pytest.raises(InvalidInputError):
        check_audio(big)


def test_speech_is_never_written_over_a_file(tmp_path):
    existing = tmp_path / "existing.wav"
    existing.write_bytes(b"RIFF")
    with pytest.raises(InvalidInputError):
        synthesize("Open Calculator.", existing)
    assert existing.read_bytes() == b"RIFF"


def test_recognition_without_the_recogniser_installed_says_so_before_anything_else(monkeypatch, tmp_path):
    monkeypatch.setenv(engine.FOLDER_ENV, str(tmp_path / "none"))
    audio = tmp_path / "request.wav"
    with wave.open(str(audio), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 1600)
    with pytest.raises(SpeechUnavailable) as raised:
        recognize_file(audio)
    assert "not installed" in raised.value.reason and "fetch_speech.py" in raised.value.reason


# ------------------------------------------------------------ real recognition (needs the installed recogniser)


@needs_recogniser
def test_the_defect_my_name_is_kaushik_is_recognised_as_a_name_not_nonsense(spoken):
    """Windows' dictation returned "Like name is go seek" for this sentence."""
    transcript = recognize_file(spoken["name"])
    words = transcript.text.casefold()
    assert words.startswith("my name is ") and "go seek" not in words and "like" not in words
    assert transcript.confidence is not None and transcript.confidence > 0.5
    assert "SHA-256" in transcript.audio and "Whisper" in transcript.recognizer


@needs_recogniser
def test_open_calculator_is_recognised(spoken):
    transcript = recognize_file(spoken["open"])
    assert transcript.text.casefold().strip(" .") == "open calculator" and transcript.grammar == "dictation"


@needs_recogniser
def test_an_ordinary_technical_question_is_recognised_word_for_word(spoken):
    assert recognize_file(spoken["question"]).text.casefold().replace("?", "").strip() == (
        "what is the resonant frequency of an lc circuit")


@needs_recogniser
def test_silence_is_nothing_recognized_and_nothing_is_invented(spoken):
    transcript = recognize_file(spoken["silence"])
    assert transcript.text == "" and transcript.grammar == "" and transcript.confidence is None


# ------------------------------------------------------------ the same pipeline


def _run(text: str, *, confirmed: bool = False):
    return Pipeline(SimulatedPlatform(), screenshots=Path("."), clock=lambda: FIXED).run(text, confirmed=confirmed)


@needs_recogniser
def test_the_transcript_is_processed_exactly_as_typed_text(spoken):
    spoken_report = _run(recognize_file(spoken["open"]).text)
    assert spoken_report.outcome is Outcome.DONE
    assert [s.name for s in spoken_report.stages] == ["INTERPRET", "PLAN", "VALIDATE", "PERMISSION", "EXECUTE",
                                                      "VERIFY", "REPORT"]
    typed = _run("Open Calculator.")
    assert spoken_report.outcome is typed.outcome and spoken_report.plan == typed.plan


@needs_recogniser
def test_voice_never_authorizes(spoken):
    transcript = recognize_file(spoken["close"]).text
    assert transcript.casefold().strip(" .") == "close notepad"
    platform = _LiveLike(open_windows=("Notepad",))
    refused = Pipeline(platform, screenshots=Path(".")).run(transcript)
    assert refused.outcome is Outcome.REFUSED and refused.permissions[0].decision is Decision.REFUSED
    permitted = Pipeline(platform, screenshots=Path(".")).run(transcript, confirmed=True)  # the typed --confirm
    assert permitted.outcome is Outcome.DONE
