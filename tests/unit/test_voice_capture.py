"""The microphone capture: when listening stops, what is kept, and every way it can fail.

The real microphone is never opened here. `Endpointer` (the decision of when the speaker
has started and finished) is tested with synthetic sound; `record` is tested against a fake
`winmm` that plays prepared audio into the buffers RUDRA hands it, the way Windows would.
"""

from __future__ import annotations

import array
import ctypes
import math
import random
import threading
import time

import pytest

from app.voice import SpeechCancelled, SpeechUnavailable, capture
from app.voice.capture import FRAME_SAMPLES, SAMPLE_RATE, Endpointer

RATE = SAMPLE_RATE


def noise(seconds: float, amplitude: int = 30, seed: int = 1) -> array.array:
    generator = random.Random(seed)
    return array.array("h", (generator.randint(-amplitude, amplitude) for _ in range(int(seconds * RATE))))


def voice(seconds: float, amplitude: int = 6000) -> array.array:
    """A steady tone with a little wobble: loud enough, and not one level for ever."""
    return array.array("h", (int(amplitude * (0.75 + 0.25 * math.sin(i / 700)) * math.sin(i * 0.19))
                             for i in range(int(seconds * RATE))))


def feed(endpointer: Endpointer, *parts: array.array, chunk: int = 1600) -> None:
    sound = array.array("h")
    for part in parts:
        sound.extend(part)
    for start in range(0, len(sound), chunk):
        endpointer.feed(sound[start:start + chunk])


# ---------------------------------------------------------------- when the speaker has finished


def test_a_quiet_room_is_not_speech():
    heard = Endpointer()
    feed(heard, noise(3))
    assert heard.speech_at is None and not heard.finished
    recording = heard.recording()
    assert not recording.speech and recording.silent and not recording.blocked


def test_speech_after_a_pause_is_found_and_listening_ends_after_a_second_of_quiet():
    heard = Endpointer()
    feed(heard, noise(0.6), voice(0.8))
    assert heard.speech_at is not None and abs(heard.speech_at / RATE - 0.6) < 0.05
    assert not heard.finished
    feed(heard, noise(0.5))
    assert not heard.finished                     # half a second of quiet is a pause between words
    feed(heard, noise(0.7))
    assert heard.finished                         # a second and more is the end


def test_what_is_kept_is_the_speech_with_a_little_either_side():
    heard = Endpointer()
    feed(heard, noise(1.0), voice(0.8), noise(1.5))
    recording = heard.recording()
    assert recording.speech and not recording.silent and recording.peak > 3000
    assert 0.35 + 0.8 + 0.3 < recording.seconds < 0.35 + 0.8 + 0.5  # leading quiet and the tail are trimmed
    assert len(recording.pcm) == int(recording.seconds * RATE) * 2


def test_a_pause_inside_a_sentence_does_not_end_it():
    heard = Endpointer()
    feed(heard, noise(0.5), voice(0.6), noise(0.6), voice(0.6))
    assert heard.speech_at is not None and not heard.finished
    assert heard.recording().seconds == pytest.approx(0.35 + 0.6 + 0.6 + 0.6, abs=0.03)  # all of it, pause included


def test_a_noisy_room_does_not_look_like_speech_and_speech_over_it_is_found():
    heard = Endpointer()
    feed(heard, noise(1.0, amplitude=300))        # an air conditioner: far over the absolute floor
    assert heard.speech_at is None
    feed(heard, voice(0.7, amplitude=7000))
    assert heard.speech_at is not None


def test_a_speaker_who_starts_at_once_is_still_recorded_from_the_first_word():
    heard = Endpointer()
    feed(heard, voice(1.0), noise(1.5))
    recording = heard.recording()
    # Speech began inside the 0.2 s the room's noise level is learnt from: it cannot be
    # told apart, so nothing is cut away - the recogniser gets everything.
    assert recording.seconds >= 2.4 and not recording.silent


def test_a_very_quiet_microphone_is_still_given_to_the_recogniser():
    heard = Endpointer()
    feed(heard, noise(0.5, amplitude=5), voice(1.0, amplitude=700), noise(1.5, amplitude=5))
    recording = heard.recording()
    assert recording.speech and not recording.silent


def test_a_microphone_that_gives_only_zeros_is_blocked_not_silent_room():
    heard = Endpointer()
    feed(heard, array.array("h", [0]) * (RATE * 2))
    recording = heard.recording()
    assert recording.blocked and recording.peak == 0


def test_a_room_with_a_little_noise_is_not_blocked():
    heard = Endpointer()
    feed(heard, noise(1, amplitude=2))
    assert not heard.recording().blocked


# ---------------------------------------------------------------- the microphone through a fake winmm


class FakeWinmm:
    """Plays `sound` into the buffers RUDRA queues, 100 ms at a time, as Windows would."""

    def __init__(self, sound: array.array, *, devices: int = 1, open_error: int = 0):
        self.sound = sound
        self.devices = devices
        self.open_error = open_error
        self.queue: list = []
        self.cursor = 0
        self.log: list[str] = []
        self.started = False
        self.thread: threading.Thread | None = None
        self.running = False

    def waveInGetNumDevs(self):
        return self.devices

    def waveInOpen(self, handle, device, fmt, *rest):
        self.log.append("open")
        fmt_struct = fmt._obj
        assert (fmt_struct.nChannels, fmt_struct.nSamplesPerSec, fmt_struct.wBitsPerSample) == (1, RATE, 16)
        return self.open_error

    def waveInPrepareHeader(self, handle, header, size):
        self.log.append("prepare")
        return 0

    def waveInAddBuffer(self, handle, header, size):
        self.queue.append(header._obj)
        header._obj.dwFlags = 0
        return 0

    def waveInStart(self, handle):
        self.log.append("start")
        self.running = True
        self.thread = threading.Thread(target=self._play, daemon=True)
        self.thread.start()
        return 0

    def _play(self):
        step = int(RATE * 0.1)
        while self.running:
            time.sleep(0.004)
            if not self.queue:
                continue
            header = self.queue.pop(0)
            chunk = self.sound[self.cursor:self.cursor + step]
            if len(chunk) < step:
                chunk = chunk + array.array("h", [0]) * (step - len(chunk))
            self.cursor += step
            data = chunk.tobytes()
            ctypes.memmove(header.lpData, data, len(data))
            header.dwBytesRecorded = len(data)
            header.dwFlags = 1  # WHDR_DONE

    def waveInStop(self, handle):
        self.log.append("stop")
        self.running = False
        return 0

    def waveInReset(self, handle):
        self.log.append("reset")
        self.running = False
        return 0

    def waveInUnprepareHeader(self, handle, header, size):
        self.log.append("unprepare")
        return 0

    def waveInClose(self, handle):
        self.log.append("close")
        return 0


def listening_to(monkeypatch, sound: array.array, **options) -> FakeWinmm:
    fake = FakeWinmm(sound, **options)
    monkeypatch.setattr(capture, "_api", lambda: fake)
    return fake


def test_listening_stops_by_itself_when_the_speaker_has_finished(monkeypatch):
    fake = listening_to(monkeypatch, noise(0.4) + voice(0.7) + noise(3.0))
    started = time.monotonic()
    recording = capture.record(10)
    assert time.monotonic() - started < 5            # not the whole ten seconds
    assert recording.speech and 1.2 < recording.seconds < 1.8
    assert fake.log[0] == "open"
    assert fake.log.count("prepare") == fake.log.count("unprepare") == capture.BUFFER_COUNT and fake.log[-1] == "close"


def test_the_window_gives_up_listening_after_the_seconds_it_allowed(monkeypatch):
    listening_to(monkeypatch, noise(5.0))
    started = time.monotonic()
    recording = capture.record(1.0)
    assert 0.9 < time.monotonic() - started < 3 and not recording.speech and recording.silent


def test_finish_ends_the_listening_and_keeps_what_was_said(monkeypatch):
    listening_to(monkeypatch, noise(0.4) + voice(30.0))
    finish = threading.Event()
    threading.Timer(1.2, finish.set).start()
    recording = capture.record(20, finish=finish)
    assert recording.speech and recording.seconds > 0.8


def test_cancel_discards_everything_and_still_closes_the_microphone(monkeypatch):
    fake = listening_to(monkeypatch, noise(0.4) + voice(30.0))
    cancel = threading.Event()
    threading.Timer(0.8, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(SpeechCancelled):
        capture.record(20, cancel=cancel)
    assert time.monotonic() - started < 4
    assert fake.log[-1] == "close" and "stop" in fake.log


def test_a_computer_without_a_microphone_says_so(monkeypatch):
    listening_to(monkeypatch, noise(1.0), devices=0)
    with pytest.raises(SpeechUnavailable) as raised:
        capture.record(5)
    assert "could not find a microphone" in raised.value.reason


@pytest.mark.parametrize("code, mentions", [
    (4, "another program"),
    (6, "could not find a microphone"),
    (2, "could not find a microphone"),
    (7, "Privacy"),
])
def test_a_microphone_that_cannot_be_opened_is_explained(monkeypatch, code, mentions):
    fake = listening_to(monkeypatch, noise(1.0), open_error=code)
    with pytest.raises(SpeechUnavailable) as raised:
        capture.record(5)
    assert mentions in raised.value.reason and f"error {code}" in raised.value.detail
    assert "stop" not in fake.log and "prepare" not in fake.log  # nothing was started


def test_a_muted_microphone_is_recognised_by_its_exact_silence(monkeypatch):
    listening_to(monkeypatch, array.array("h", [0]) * (RATE * 3))
    recording = capture.record(1.0)
    assert recording.blocked


def test_listening_is_for_windows_only(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(capture.sys, "platform", "linux")
    with pytest.raises(SpeechUnavailable) as raised:
        capture.record(1)
    assert "Windows only" in raised.value.reason


def test_a_frame_is_twenty_milliseconds():
    assert FRAME_SAMPLES == 320 and capture.FRAME_SECONDS == 0.02
