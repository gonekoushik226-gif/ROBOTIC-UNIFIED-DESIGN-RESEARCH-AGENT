"""Phase 20 voice (ADR 0052; Part 5 sections 220-221; Part 4 sections 141-142).

    speech  Windows' own speech engine through PowerShell: a WAV file or one
            user-activated utterance -> a transcript, with RUDRA's command grammar and
            dictation; text -> a new WAV file

A transcript is ordinary text: the caller hands it to the same pipeline as typed text, so
voice never bypasses authorization (section 220). There is no always-listening mode.
"""

from app.voice.speech import (
    CULTURE,
    VERBS,
    SpeechCancelled,
    SpeechUnavailable,
    Transcript,
    check_audio,
    command_phrases,
    listen,
    recognize_file,
    synthesize,
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
]
