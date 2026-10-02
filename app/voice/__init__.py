"""Voice (ADR 0052; Part 5 sections 220-221; Part 4 sections 141-142).

    speech   offline speech recognition (OpenAI Whisper, run by whisper.cpp from the
             `speech` folder) for a WAV file or one user-activated utterance from the
             microphone -> a transcript; and Windows' voice for text -> a new WAV file
    engine   running the recogniser, and finding its files
    capture  one utterance from the default microphone, through Windows' own audio API

A transcript is ordinary text: the caller hands it to the same pipeline as typed text, so
voice never bypasses authorization (section 220). There is no always-listening mode.
"""

from app.voice.errors import SpeechCancelled, SpeechUnavailable
from app.voice.speech import (
    CULTURE,
    VERBS,
    Transcript,
    check_audio,
    command_phrases,
    listen,
    recognize_file,
    synthesize,
    vocabulary_prompt,
)

__all__ = [
    "CULTURE",
    "VERBS",
    "SpeechCancelled",
    "SpeechUnavailable",
    "Transcript",
    "check_audio",
    "command_phrases",
    "listen",
    "recognize_file",
    "synthesize",
    "vocabulary_prompt",
]
