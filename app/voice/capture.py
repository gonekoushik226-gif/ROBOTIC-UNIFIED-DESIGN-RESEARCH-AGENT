"""One utterance from the default microphone, through Windows' own audio API (winmm).

No dependency: `waveIn` ships with Windows and is reached with `ctypes`. Audio is captured
as 16 kHz mono 16-bit PCM - what the recogniser reads - and lives only in memory: it is
handed to the recogniser and dropped; nothing is written to disk and nothing records until
the user asked it to (there is no always-listening mode).

Listening ends when the speaker has finished (about a second of quiet after speech), when
the user stops it, or when `seconds` have passed. "Speech" here is only a loudness test
against the room's own noise level, used to decide when to stop recording; it decides
nothing about what was said - the recogniser's voice-activity model does that.
"""

from __future__ import annotations

import array
import ctypes
import math
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from app.voice.errors import SpeechCancelled, SpeechUnavailable

SAMPLE_RATE = 16_000
FRAME_SECONDS = 0.02
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_SECONDS)
BUFFER_SECONDS = 0.1
BUFFER_COUNT = 8

#: The speech threshold is this many times the room's noise level, and never below the floor.
NOISE_FACTOR = 3.0
THRESHOLD_FLOOR = 90
#: The room's noise level is read from this many frames at the start (0.2 s).
CALIBRATION_FRAMES = 10
#: Speech begins after this much of it in a row, and ends after this much quiet.
START_SECONDS = 0.10
END_SECONDS = 1.1
#: Audio kept from before the speech began, and from after it ended.
PREROLL_SECONDS = 0.35
TAIL_SECONDS = 0.35
#: A recording quieter than this peak, with no speech in it, is silence: it is not decoded.
SILENT_PEAK = 400

_WAVE_MAPPER = 0xFFFFFFFF
_CALLBACK_NULL = 0
_WHDR_DONE = 0x1
_MMSYSERR_ALLOCATED = 4
_MMSYSERR_NODRIVER = 6
_WAVERR_BADFORMAT = 32


class _WaveFormat(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class _WaveHeader(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t), ("dwFlags", wintypes.DWORD),
                ("dwLoops", wintypes.DWORD), ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


@dataclass(frozen=True, slots=True)
class Recording:
    """What the microphone gave."""

    pcm: bytes
    seconds: float
    #: Whether the speaker was heard to start (loudness over the room's noise).
    speech: bool
    #: The loudest sample, 0..32767.
    peak: int
    #: Every sample was exactly zero: the microphone is muted or Windows is blocking it.
    blocked: bool

    @property
    def silent(self) -> bool:
        return not self.speech and self.peak < SILENT_PEAK


def _api():
    if sys.platform != "win32":
        raise SpeechUnavailable("Listening through the microphone is available on Windows only.")
    winmm = ctypes.WinDLL("winmm")
    winmm.waveInOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(_WaveFormat),
                                 ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    for name in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
        getattr(winmm, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(_WaveHeader), wintypes.UINT]
    for name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
        getattr(winmm, name).argtypes = [wintypes.HANDLE]
    return winmm


def _explain_open_failure(code: int) -> SpeechUnavailable:
    if code == _MMSYSERR_ALLOCATED:
        return SpeechUnavailable("The microphone is being used by another program. Close it and try again.",
                                 f"waveInOpen error {code}")
    if code in (_MMSYSERR_NODRIVER, _WAVERR_BADFORMAT, 2):
        return SpeechUnavailable("RUDRA could not find a microphone. Check that one is connected and turned on.",
                                 f"waveInOpen error {code}")
    return SpeechUnavailable(
        "RUDRA could not use the microphone. Check that Windows allows desktop apps to use it "
        "(Settings > Privacy & security > Microphone).", f"waveInOpen error {code}")


def _rms(frame: array.array) -> float:
    return math.sqrt(sum(sample * sample for sample in frame) / len(frame)) if frame else 0.0


class Endpointer:
    """Decides, from the loudness of 20 ms frames, when the speaker has begun and finished.

    The room's noise level is learnt from the first 0.2 s (the median frame - a speaker
    rarely starts within 0.2 s of pressing the button), then follows the quietest frames, and
    rises only slowly, so a long sentence does not teach it that speech is the background. A
    frame is speech when it is well over that level. Speech starts after 0.1 s of it in a row
    and ends after 1.1 s of quiet. This only decides when to stop recording: what was said is
    the recogniser's work, and a recording in which no start was found is still given to it.
    """

    def __init__(self) -> None:
        self.samples = array.array("h")
        self.position = 0  # samples already analysed
        self.noise: float | None = None
        self._calibration: list[tuple[float, int]] = []  # (level, end position) of the first frames
        self.voiced_run = 0
        self.quiet_run = 0
        #: Sample index where speech began, once it has.
        self.speech_at: int | None = None
        #: Sample index just after the last loud frame.
        self.last_voiced = 0
        self.peak = 0

    def feed(self, chunk: array.array) -> None:
        self.samples.extend(chunk)
        while len(self.samples) - self.position >= FRAME_SAMPLES:
            frame = self.samples[self.position:self.position + FRAME_SAMPLES]
            self.position += FRAME_SAMPLES
            self.peak = max(self.peak, max(frame), -min(frame))
            level = _rms(frame)
            if self.noise is None:
                self._calibration.append((level, self.position))
                if len(self._calibration) >= CALIBRATION_FRAMES:
                    self.noise = sorted(item[0] for item in self._calibration)[len(self._calibration) // 2]
                    for earlier, end in self._calibration:
                        self._judge(earlier, end)
                continue
            if level < self.noise:
                self.noise = 0.7 * self.noise + 0.3 * level
            else:
                self.noise *= 1.001
            self._judge(level, self.position)

    def _judge(self, level: float, end: int) -> None:
        if level > max(THRESHOLD_FLOOR, NOISE_FACTOR * (self.noise or 0.0)):
            self.voiced_run += 1
            self.quiet_run = 0
            self.last_voiced = end
            if self.speech_at is None and self.voiced_run * FRAME_SECONDS >= START_SECONDS:
                self.speech_at = max(0, end - self.voiced_run * FRAME_SAMPLES)
        else:
            self.voiced_run = 0
            self.quiet_run += 1

    @property
    def finished(self) -> bool:
        """The speaker started and has been quiet long enough to be done."""
        return self.speech_at is not None and self.quiet_run * FRAME_SECONDS >= END_SECONDS

    def recording(self) -> Recording:
        """What to keep: the speech with a little before and after, or everything if none was heard."""
        if self.speech_at is not None:
            begin = max(0, self.speech_at - int(PREROLL_SECONDS * SAMPLE_RATE))
            end = min(len(self.samples), self.last_voiced + int(TAIL_SECONDS * SAMPLE_RATE))
            kept = self.samples[begin:end]
        else:
            kept = self.samples
        blocked = len(self.samples) > 0 and self.peak == 0
        return Recording(kept.tobytes(), len(kept) / SAMPLE_RATE, self.speech_at is not None, self.peak, blocked)


def record(seconds: float = 10, *, cancel: threading.Event | None = None, finish: threading.Event | None = None,
           stop_on_silence: bool = True) -> Recording:
    """Listen for one utterance, for at most `seconds`.

    `finish` ends the recording early and keeps it (the user is done speaking). Raises
    `SpeechCancelled` when `cancel` is set - what was recorded is discarded - and
    `SpeechUnavailable` when there is no usable microphone.
    """
    winmm = _api()
    if winmm.waveInGetNumDevs() == 0:
        raise SpeechUnavailable("RUDRA could not find a microphone. Check that one is connected and turned on.",
                                "no audio input devices")
    fmt = _WaveFormat(1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0)
    handle = wintypes.HANDLE()
    code = winmm.waveInOpen(ctypes.byref(handle), _WAVE_MAPPER, ctypes.byref(fmt), 0, 0, _CALLBACK_NULL)
    if code != 0:
        raise _explain_open_failure(code)
    size = int(SAMPLE_RATE * BUFFER_SECONDS) * 2
    buffers = [ctypes.create_string_buffer(size) for _ in range(BUFFER_COUNT)]
    headers = [_WaveHeader(ctypes.addressof(buffer), size, 0, 0, 0, 0, None, 0) for buffer in buffers]
    prepared = 0
    started = False
    try:
        for header in headers:
            winmm.waveInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
            prepared += 1
            winmm.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header))
        winmm.waveInStart(handle)
        started = True
        return _listen(winmm, handle, headers, buffers, seconds, cancel, finish, stop_on_silence)
    finally:
        if started:
            winmm.waveInStop(handle)
        winmm.waveInReset(handle)
        for header in headers[:prepared]:
            winmm.waveInUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
        winmm.waveInClose(handle)


def _listen(winmm, handle, headers, buffers, seconds, cancel, finish, stop_on_silence) -> Recording:
    heard = Endpointer()
    deadline = time.monotonic() + seconds
    index = 0
    while True:
        if cancel is not None and cancel.is_set():
            raise SpeechCancelled("listening was cancelled")
        if finish is not None and finish.is_set():
            break
        header = headers[index]
        if header.dwFlags & _WHDR_DONE:
            recorded = header.dwBytesRecorded
            chunk = array.array("h")
            chunk.frombytes(bytes(buffers[index].raw[:recorded - recorded % 2]))
            winmm.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header))
            index = (index + 1) % len(headers)
            heard.feed(chunk)
            if stop_on_silence and heard.finished:
                break
            continue
        if time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    return heard.recording()
