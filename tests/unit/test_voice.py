"""Phase 20: voice (ADR 0052).

What every test holds Phase 20 to: the command grammar is RUDRA's data (the registry's
applications and the interpreter's verbs); nothing a user or a document supplies is ever
evaluated by the speech bridge; audio is checked before use; a transcript is ordinary
text for the same pipeline, so voice never authorizes. The microphone is never opened here.

The speech tests use Windows' own engine; where it is not available they are skipped with
that reason (P20-10), never passed silently.
"""

from __future__ import annotations

import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.actions import SimulatedPlatform
from app.applications import APPLICATIONS
from app.core.errors import InvalidInputError
from app.orchestration import Outcome, Pipeline
from app.security import Decision
from app.voice import SpeechUnavailable, check_audio, command_phrases, recognize_file, synthesize
from app.voice.speech import _quote

FIXED = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


class _LiveLike(SimulatedPlatform):
    """A simulated computer the permission engine treats as live."""

    live = True


def _engine_available(tmp_path_factory) -> bool:
    try:
        synthesize("Open Calculator.", tmp_path_factory.mktemp("probe") / "probe.wav")
    except SpeechUnavailable:
        return False
    return True


@pytest.fixture(scope="module")
def spoken(tmp_path_factory) -> dict[str, Path]:
    """Two requests spoken by the Windows voice into WAV files, and one second of silence."""
    if not _engine_available(tmp_path_factory):
        pytest.skip("the Windows speech engine is not available on this machine")
    folder = tmp_path_factory.mktemp("speech")
    files = {"open": synthesize("Open Calculator.", folder / "open.wav"),
             "close": synthesize("Close Notepad.", folder / "close.wav")}
    silence = folder / "silence.wav"
    with wave.open(str(silence), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 16000)
    files["silence"] = silence
    return files


# ------------------------------------------------------------ the grammar (P20-2)


def test_the_command_grammar_is_rudras_data():
    verbs, names = command_phrases()
    assert verbs[:2] == ("open", "launch") and "close" in verbs
    for application in APPLICATIONS:
        assert application.name in names


@pytest.mark.parametrize("text", ["it's", "'; Remove-Item C:\\ -Recurse; '", "$(Get-Process)"])
def test_text_reaches_powershell_only_as_a_single_quoted_literal(text):
    quoted = _quote(text)
    assert quoted.startswith("'") and quoted.endswith("'")
    assert quoted[1:-1].replace("''", "") .count("'") == 0  # every inner quote is doubled


# ------------------------------------------------------------ audio (P20-6)


def test_audio_that_is_not_a_wav_file_is_refused(tmp_path):
    with pytest.raises(InvalidInputError):
        check_audio(tmp_path / "missing.wav")
    text = tmp_path / "notes.wav"
    text.write_text("not audio", encoding="utf-8")
    with pytest.raises(InvalidInputError):
        check_audio(text)


def test_speech_is_never_written_over_a_file(tmp_path):
    existing = tmp_path / "existing.wav"
    existing.write_bytes(b"RIFF")
    with pytest.raises(InvalidInputError):
        synthesize("Open Calculator.", existing)
    assert existing.read_bytes() == b"RIFF"


# ------------------------------------------------------------ recognition (P20-2, P20-7)


def test_open_calculator_is_recognized_by_the_command_grammar(spoken):
    transcript = recognize_file(spoken["open"])
    assert transcript.text.casefold() == "open calculator" and transcript.grammar == "commands"
    assert transcript.confidence is not None and "SHA-256" in transcript.audio


def test_silence_is_nothing_recognized(spoken):
    assert recognize_file(spoken["silence"]).text == ""


# ------------------------------------------------------------ the same pipeline (P20-3, P20-4)


def _run(text: str, *, confirmed: bool = False):
    return Pipeline(SimulatedPlatform(), screenshots=Path("."), clock=lambda: FIXED).run(text, confirmed=confirmed)


def test_the_transcript_is_processed_exactly_as_typed_text(spoken):
    spoken_report = _run(recognize_file(spoken["open"]).text)
    typed_report = _run("open Calculator")
    assert spoken_report == typed_report
    assert spoken_report.outcome is Outcome.DONE
    assert [s.name for s in spoken_report.stages] == ["INTERPRET", "PLAN", "VALIDATE", "PERMISSION", "EXECUTE",
                                                      "VERIFY", "REPORT"]


def test_voice_never_authorizes(spoken):
    transcript = recognize_file(spoken["close"]).text
    assert transcript.casefold() == "close notepad"
    platform = _LiveLike(open_windows=("Notepad",))
    refused = Pipeline(platform, screenshots=Path(".")).run(transcript)
    assert refused.outcome is Outcome.REFUSED and refused.permissions[0].decision is Decision.REFUSED
    permitted = Pipeline(platform, screenshots=Path(".")).run(transcript, confirmed=True)  # the typed --confirm
    assert permitted.outcome is Outcome.DONE
