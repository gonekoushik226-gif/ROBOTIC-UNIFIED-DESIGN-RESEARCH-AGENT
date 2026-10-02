"""Phase 20 acceptance: Part 5 section 221 through the `voice` command (ADR 0052 P20-11).

    Say: "Open Calculator." Expected: Speech -> text -> intent -> action -> verification.
    The behavior must be equivalent to a typed command.

Each step is one `python -m app` process in a temporary project root; the live database is
never opened. The request is spoken by the Windows voice into a WAV file in pytest's
temporary folder (`voice --say`), and recognized offline by Whisper. The pipeline runs
on the simulated computer (`--dry-run`): the suite never acts on the live desktop (ADR 0047
P15-11); the live path is `do`'s, demonstrated live in Phase 15. The microphone is never
opened. Without the Windows speech engine the module is skipped, and says so.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from app.ui.cli import main as cli_main
from app.voice import SpeechUnavailable, engine, synthesize
from tests.conftest import PROJECT_ROOT


def cli(root, *args: str) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PROJECT_ROOT), timeout=240,
    )
    return done.returncode, done.stdout, done.stderr


def _json(root, *args) -> tuple[int, dict]:
    code, out, err = cli(root, *args, "--json")
    assert out, err
    return code, json.loads(out)


@pytest.fixture(scope="module")
def speech(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("speech")
    try:
        synthesize("Open Calculator.", folder / "probe.wav")
        engine.find_components()
    except SpeechUnavailable as missing:
        pytest.skip(f"speech is not available (Windows' voice, or the recogniser in the speech folder):{missing.reason}")
    return folder


def _comparable(report: dict) -> dict:
    """A pipeline report without the time-stamped parts of each step's execution record."""
    execution = report["execution"]
    if execution is not None:
        for step in execution["steps"]:
            step.pop("started", None)
            step.pop("finished", None)
    return report


def test_the_command_set_gains_exactly_voice():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review", "edition",
              "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "act", "do",
              "procedure", "manual", "research", "diagram", "version"}
    # Later phases add their own commands (ADR 0047 onward); Phase 20's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"ask", "source", "solve", "inventory"} == before | {"voice"}


def test_section_221_spoken_open_calculator_equals_the_typed_command(speech, tmp_path):
    root = tmp_path / "project"
    audio = speech / "open-calculator.wav"
    code, written = _json(root, "voice", "--say", "Open Calculator.", "--audio", str(audio))
    assert code == 0 and written["status"] == "WRITTEN" and audio.exists()

    code, spoken = _json(root, "voice", "--audio", str(audio), "--dry-run")
    assert code == 0
    transcript = spoken["speech"]
    assert transcript["text"].casefold().strip(" .") == "open calculator" and transcript["grammar"] == "dictation"
    assert "Whisper" in transcript["recognizer"] and transcript["confidence"] is not None
    report = spoken["report"]
    # Speech -> text -> intent -> action -> verification.
    assert [s["name"] for s in report["stages"]] == ["INTERPRET", "PLAN", "VALIDATE", "PERMISSION", "EXECUTE",
                                                     "VERIFY", "REPORT"]
    (intent,) = report["interpretation"]["intents"]
    assert intent["action"]["action"] == "OPEN_APPLICATION"
    assert report["execution"]["steps"][0]["status"] == "VERIFIED" and report["outcome"] == "DONE"

    # Equivalent to the typed command.
    code, typed = _json(root, "do", transcript["text"], "--dry-run")
    assert code == 0 and _comparable(report) == _comparable(typed)


def test_voice_does_not_bypass_authorization(speech, tmp_path):
    root = tmp_path / "project"
    audio = speech / "close-notepad.wav"
    assert cli(root, "voice", "--say", "Close Notepad.", "--audio", str(audio))[0] == 0
    code, spoken = _json(root, "voice", "--audio", str(audio), "--dry-run")
    assert spoken["speech"]["text"].casefold().strip(" .") == "close notepad"
    # On the simulated computer a dry run needs no permission; the step is MEDIUM risk, and
    # the permission engine's decision for a live run is the typed --confirm's alone.
    (decision,) = spoken["report"]["permissions"]
    assert decision["decision"] == "NOT_REQUIRED" and decision["risk_level"] == "MEDIUM"
    code, typed = _json(root, "do", spoken["speech"]["text"], "--dry-run")
    assert _comparable(spoken["report"]) == _comparable(typed)


def test_silence_executes_nothing(speech, tmp_path):
    silence = speech / "silence.wav"
    with wave.open(str(silence), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 16000)
    code, answer = _json(tmp_path / "project", "voice", "--audio", str(silence), "--dry-run")
    assert code == 3 and answer["report"] is None and answer["speech"]["text"] == ""


@pytest.mark.parametrize(
    "args",
    [("voice",), ("voice", "Open Calculator."), ("voice", "--audio", "missing.wav"),
     ("voice", "--listen", "--audio", "x.wav"), ("voice", "--say", "Hello"),
     ("voice", "--say", "Hello", "--audio", "new.wav", "--confirm"), ("do", "Open Calculator.", "--listen"),
     ("reason", "X", "--audio", "x.wav")],
)
def test_invalid_requests_exit_2_and_never_open_the_microphone(tmp_path, args):
    assert cli(tmp_path / "project", *args)[0] == 2
