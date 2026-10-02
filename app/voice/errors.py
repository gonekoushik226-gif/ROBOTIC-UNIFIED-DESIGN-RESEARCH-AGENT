"""What can go wrong with speech, as the window and the command line report it."""


class SpeechUnavailable(Exception):
    """The speech engine could not be used: not Windows, not installed, no microphone, or it failed.

    `reason` is for the person using RUDRA; `detail` is the engine's own wording, kept for
    the log and never shown as the message.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class SpeechCancelled(Exception):
    """Listening was stopped by the caller (the user pressed Stop) before it finished."""
