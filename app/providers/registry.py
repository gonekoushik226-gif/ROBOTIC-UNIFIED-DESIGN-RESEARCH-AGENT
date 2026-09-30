"""The external providers, spoken to over HTTPS with the standard library.

Each provider is described by data: its endpoints, how its API key is sent, the request
body for one system instruction plus one user message, and where the reply's text is.
The shapes follow each provider's official API reference:

* Anthropic - POST /v1/messages, headers ``x-api-key`` and ``anthropic-version: 2023-06-01``;
  text in ``content[].text``; models from GET /v1/models.
* OpenAI - POST /v1/responses with ``instructions`` and ``input``, ``Authorization: Bearer``;
  text in ``output[].content[]`` items of type ``output_text``; models from GET /v1/models.
* Google Gemini - POST /v1beta/models/{model}:generateContent, header ``x-goog-api-key``
  (never a URL parameter); text in ``candidates[0].content.parts[].text``; models from
  GET /v1beta/models, keeping those that support ``generateContent``.
* Mistral - POST /v1/chat/completions, ``Authorization: Bearer``; text in
  ``choices[0].message.content``; models from GET /v1/models.

No model name is built in: models change, so the user picks one from the provider's own
list. A request carries only what the caller passes; the key is never logged or echoed.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

TIMEOUT_SECONDS = 90.0
MAX_RESPONSE_BYTES = 4_000_000
USER_AGENT = "RUDRA"

Transport = Callable[[urllib.request.Request, float], bytes]


class ProviderError(Exception):
    """The provider could not be used for this request. The message never contains the key."""


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    website: str
    key_page: str
    #: Official documentation of the data the provider keeps, shown to the user before enabling.
    privacy_page: str

    def headers(self, api_key: str) -> dict[str, str]:
        common = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self.key == "anthropic":
            return {**common, "x-api-key": api_key, "anthropic-version": "2023-06-01"}
        if self.key == "gemini":
            return {**common, "x-goog-api-key": api_key}
        return {**common, "Authorization": f"Bearer {api_key}"}

    def chat_request(self, model: str, system: str, user: str, max_tokens: int = 4000) -> tuple[str, dict]:
        if self.key == "anthropic":
            return "https://api.anthropic.com/v1/messages", {
                "model": model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}]}
        if self.key == "openai":
            return "https://api.openai.com/v1/responses", {"model": model, "instructions": system, "input": user}
        if self.key == "gemini":
            path = urllib.parse.quote(model if model.startswith("models/") else f"models/{model}", safe="/-._")
            return f"https://generativelanguage.googleapis.com/v1beta/{path}:generateContent", {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}]}
        if self.key == "mistral":
            return "https://api.mistral.ai/v1/chat/completions", {
                "model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        raise ProviderError(f"Unknown provider {self.key}")

    def models_url(self) -> str:
        return {
            "anthropic": "https://api.anthropic.com/v1/models?limit=100",
            "openai": "https://api.openai.com/v1/models",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
            "mistral": "https://api.mistral.ai/v1/models",
        }[self.key]

    def reply_text(self, data: dict) -> str:
        """The generated text of one reply."""
        if self.key == "anthropic":
            if data.get("stop_reason") == "refusal":
                raise ProviderError("The provider declined this request.")
            return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        if self.key == "openai":
            return "".join(part.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                           for part in item.get("content", []) if part.get("type") == "output_text")
        if self.key == "gemini":
            candidates = data.get("candidates") or []
            if not candidates:
                raise ProviderError("The provider returned no answer (it may have blocked the request).")
            return "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", []))
        if self.key == "mistral":
            choices = data.get("choices") or []
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            if isinstance(content, list):
                content = "".join(chunk.get("text", "") for chunk in content if isinstance(chunk, dict))
            return str(content)
        raise ProviderError(f"Unknown provider {self.key}")

    def model_ids(self, data: dict) -> list[str]:
        if self.key == "gemini":
            return sorted(model.get("name", "").removeprefix("models/") for model in data.get("models", [])
                          if "generateContent" in model.get("supportedGenerationMethods", []))
        return sorted(model.get("id", "") for model in data.get("data", []) if model.get("id"))


PROVIDERS: dict[str, Provider] = {p.key: p for p in (
    Provider("anthropic", "Anthropic", "https://www.anthropic.com",
             "https://console.anthropic.com/settings/keys", "https://www.anthropic.com/legal/privacy"),
    Provider("openai", "OpenAI", "https://openai.com", "https://platform.openai.com/api-keys",
             "https://openai.com/policies/privacy-policy"),
    Provider("gemini", "Google Gemini", "https://ai.google.dev", "https://aistudio.google.com/apikey",
             "https://ai.google.dev/gemini-api/terms"),
    Provider("mistral", "Mistral AI", "https://mistral.ai", "https://console.mistral.ai/api-keys",
             "https://mistral.ai/terms"),
)}


def _default_transport(request: urllib.request.Request, timeout: float) -> bytes:
    opener = urllib.request.build_opener()  # no cookies, no stored credentials
    with opener.open(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


#: Anything shaped like a credential: a long run of key characters (masked or not).
_KEY_LIKE = re.compile(r"[A-Za-z0-9_\-*.]{20,}")


def _redacted(text: str, secret: str) -> str:
    """A provider's message without the key or anything shaped like one (some echo it, partly masked)."""
    if secret:
        text = text.replace(secret, "[key]")
    return _KEY_LIKE.sub("[redacted]", text)


def _call(url: str, headers: dict[str, str], body: dict | None, transport: Transport, secret: str = "") -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        raw = transport(request, TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read(20000).decode("utf-8", "replace"))
            error = payload.get("error", payload)
            detail = error.get("message", "") if isinstance(error, dict) else str(error)
        except (ValueError, AttributeError, OSError):
            detail = ""
        meaning = {400: "the request was not accepted", 401: "the API key was refused",
                   403: "the API key may not use this", 404: "the model or endpoint was not found",
                   429: "too many requests or no remaining credit"}.get(exc.code, "the provider reported an error")
        detail = _redacted(detail[:300], secret)
        raise ProviderError(f"HTTP {exc.code}: {meaning}." + (f" {detail}" if detail else "")) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"The provider could not be reached ({type(exc).__name__}). An Internet connection "
                            "is needed for AI assistance; everything else works offline.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("The provider's reply was too large.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ProviderError("The provider's reply could not be read.") from None


def send(provider: Provider, api_key: str, model: str, system: str, user: str,
         transport: Transport = _default_transport) -> str:
    """One system instruction and one user message; the reply's text."""
    if not api_key:
        raise ProviderError("No API key is stored for this provider.")
    if not model:
        raise ProviderError("No model is chosen for this provider.")
    url, body = provider.chat_request(model, system, user)
    data = _call(url, provider.headers(api_key), body, transport, api_key)
    try:
        text = provider.reply_text(data)
    except (AttributeError, TypeError, KeyError, IndexError):
        raise ProviderError("The provider's reply was not in the expected form.") from None
    if not text.strip():
        raise ProviderError("The provider returned an empty answer.")
    return text


def list_models(provider: Provider, api_key: str, transport: Transport = _default_transport) -> list[str]:
    """The models this key may use, from the provider's own list."""
    data = _call(provider.models_url(), provider.headers(api_key), None, transport, api_key)
    try:
        return provider.model_ids(data)
    except (AttributeError, TypeError, KeyError):
        raise ProviderError("The provider's list of models was not in the expected form.") from None
