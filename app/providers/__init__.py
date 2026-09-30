"""Optional external language providers. RUDRA works fully without any of them.

A provider may help with language only: turning a question into a structured local query,
or wording an explanation of statements RUDRA has already retrieved from the user's own
documents. It is never a source of knowledge. It is off until the user enables it, with
their own API key and their explicit consent to send the selected text off the computer.

    registry    the providers RUDRA can talk to (Anthropic, OpenAI, Google Gemini, Mistral)
    credentials the user's API key in the Windows Credential Manager - never in a file
    settings    whether AI assistance is enabled, which provider and model (config/ai.json)
    assistant   the two operations, with their data minimisation and grounding checks
"""

from app.providers.assistant import (
    Explanation,
    GroundedSentence,
    Interpretation,
    evidence_payload,
    explain,
    interpret_question,
)
from app.providers.registry import PROVIDERS, Provider, ProviderError, list_models, send
from app.providers.settings import AiSettings, load_settings, save_settings

__all__ = [
    "PROVIDERS",
    "AiSettings",
    "Explanation",
    "GroundedSentence",
    "Interpretation",
    "Provider",
    "ProviderError",
    "evidence_payload",
    "explain",
    "interpret_question",
    "list_models",
    "load_settings",
    "save_settings",
    "send",
]
