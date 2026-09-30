"""Optional AI assistance (app/providers): off by default, the user's own key, the least data.

Every test runs against a fake transport that records the exact HTTP request and returns
a canned reply: no request leaves the machine and no real key is used. What is held:
each provider's request and reply shape; failures (malformed replies, timeouts, refused
keys, provider errors) as clear errors that never contain the key; consent before
anything is enabled; a key only in the Windows Credential Manager, never in a file or a
backup; the question alone for interpretation and the question plus the answer's own
statements for explanation; and every explained sentence grounded in cited statements.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import uuid

import pytest

from app.providers import assistant, credentials, settings
from app.providers.registry import PROVIDERS, ProviderError, list_models, send

KEY = "sk-test-1234567890abcdefghijklmnop"


class Transport:
    """Records every request; answers with a canned body or raises a canned error."""

    def __init__(self, reply=None, error: Exception | None = None):
        self.reply, self.error, self.requests = reply, error, []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.reply if isinstance(self.reply, bytes) else json.dumps(self.reply).encode()

    @property
    def body(self) -> dict:
        return json.loads(self.requests[-1].data.decode())


REPLIES = {
    "anthropic": {"content": [{"type": "text", "text": "Hello"}], "stop_reason": "end_turn"},
    "openai": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]},
    "gemini": {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]},
    "mistral": {"choices": [{"message": {"content": "Hello"}}]},
}
ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/responses",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
}


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_each_provider_sends_one_instruction_and_one_message_and_reads_the_reply(key):
    transport = Transport(REPLIES[key])
    assert send(PROVIDERS[key], KEY, "test-model", "SYSTEM TEXT", "USER TEXT", transport=transport) == "Hello"
    (request,) = transport.requests
    assert request.full_url == ENDPOINTS[key] and request.get_method() == "POST"
    headers = {k.lower(): v for k, v in request.header_items()}
    carried = {"anthropic": "x-api-key", "gemini": "x-goog-api-key"}.get(key, "authorization")
    assert KEY in headers[carried] and KEY not in request.full_url  # the key travels in a header only
    serialized = json.dumps(transport.body)
    assert "SYSTEM TEXT" in serialized and "USER TEXT" in serialized and KEY not in serialized
    assert "cookie" not in headers


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_models_come_from_the_providers_own_list(key):
    listing = {"gemini": {"models": [{"name": "models/g-1", "supportedGenerationMethods": ["generateContent"]},
                                     {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]}}
    transport = Transport(listing.get(key, {"data": [{"id": "m-2"}, {"id": "m-1"}]}))
    assert list_models(PROVIDERS[key], KEY, transport=transport) == (["g-1"] if key == "gemini" else ["m-1", "m-2"])
    assert transport.requests[0].get_method() == "GET"


@pytest.mark.parametrize("key", sorted(PROVIDERS))
@pytest.mark.parametrize("reply", [b"not json", {"unexpected": True}, [1, 2, 3]])
def test_a_malformed_reply_is_an_error_not_an_answer(key, reply):
    with pytest.raises(ProviderError):
        send(PROVIDERS[key], KEY, "m", "s", "u", transport=Transport(reply))


def _http_error(code: int, message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": {"message": message}}).encode())
    return urllib.error.HTTPError("https://example.invalid", code, "error", {}, body)


@pytest.mark.parametrize("code, meaning", [(401, "the API key was refused"), (429, "too many requests"),
                                           (500, "the provider reported an error"), (404, "not found")])
def test_provider_failures_are_explained_and_never_repeat_the_key(code, meaning):
    echo = f"Incorrect API key provided: {KEY}. Also sk-proj-****************wxyz."
    with pytest.raises(ProviderError) as failure:
        send(PROVIDERS["openai"], KEY, "m", "s", "u", transport=Transport(error=_http_error(code, echo)))
    message = str(failure.value)
    assert meaning in message and KEY not in message and "wxyz" not in message


@pytest.mark.parametrize("error", [TimeoutError("timed out"), urllib.error.URLError("no route"), OSError("down")])
def test_no_connection_is_an_error_that_says_the_rest_works_offline(error):
    with pytest.raises(ProviderError) as failure:
        send(PROVIDERS["mistral"], KEY, "m", "s", "u", transport=Transport(error=error))
    assert "offline" in str(failure.value)


def test_a_refusal_or_an_empty_answer_is_an_error():
    with pytest.raises(ProviderError):
        send(PROVIDERS["anthropic"], KEY, "m", "s", "u", transport=Transport({"content": [], "stop_reason": "refusal"}))
    with pytest.raises(ProviderError):
        send(PROVIDERS["gemini"], KEY, "m", "s", "u", transport=Transport({"candidates": []}))
    with pytest.raises(ProviderError):
        send(PROVIDERS["mistral"], KEY, "m", "s", "u", transport=Transport({"choices": [{"message": {"content": " "}}]}))


def test_nothing_is_sent_without_a_key_or_a_model():
    transport = Transport(REPLIES["openai"])
    for key, model in (("", "m"), (KEY, "")):
        with pytest.raises(ProviderError):
            send(PROVIDERS["openai"], key, model, "s", "u", transport=transport)
    assert transport.requests == []


# ------------------------------------------------------------------ consent and settings


def test_ai_assistance_is_off_by_default_and_enabled_only_with_consent(tmp_path):
    assert settings.load_settings(tmp_path).active is False
    with pytest.raises(ValueError):
        settings.enable(tmp_path, "openai", "m", consent=False)
    assert not settings.settings_path(tmp_path).exists()
    enabled = settings.enable(tmp_path, "openai", "m", consent=True)
    assert enabled.active and settings.load_settings(tmp_path).active
    stored = settings.settings_path(tmp_path).read_text(encoding="utf-8")
    assert "key" not in json.loads(stored)  # the settings file never holds a key
    assert settings.disable(tmp_path).active is False and settings.load_settings(tmp_path).consented_at is None


def test_a_damaged_settings_file_means_off(tmp_path):
    settings.settings_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert settings.load_settings(tmp_path).active is False
    settings.settings_path(tmp_path).write_text(json.dumps({"enabled": True, "provider": "openai", "model": "m"}),
                                                encoding="utf-8")
    assert settings.load_settings(tmp_path).active is False  # no recorded consent: not active


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_a_key_is_kept_by_the_credential_manager_and_removed_on_request():
    target = f"RUDRA-test/{uuid.uuid4()}"
    try:
        credentials.store_key("openai", KEY, target_name=target)
        assert credentials.read_key("openai", target_name=target) == KEY
    finally:
        credentials.delete_key("openai", target_name=target)
    assert credentials.read_key("openai", target_name=target) is None
    with pytest.raises(credentials.CredentialError):
        credentials.store_key("openai", "has a space", target_name=target)


def test_backups_never_carry_the_ai_settings_or_keys(tmp_path):
    from app.storage.archive import _safe_member

    for name in ("config/ai.json", "ai.json", "config/secrets.json", "credentials/openai"):
        assert not _safe_member(name)


# ------------------------------------------------------------------ what is sent, and what comes back


def test_interpretation_sends_only_the_question_and_runs_rudras_own_query():
    seen = []

    def fake(system, user):
        seen.append((system, user))
        return '{"intent": "properties", "topic": "MOSFETs"}'

    result = assistant.interpret_question("tell me what MOSFETs are like, according to my notes", fake)
    assert seen == [(assistant.INTERPRET_SYSTEM, "tell me what MOSFETs are like, according to my notes")]
    assert result.ok and result.local_question == "What are the properties of MOSFETs?"


@pytest.mark.parametrize("reply", ['{"intent": "definition", "topic": "graphene"}', "no json here",
                                   '{"intent": "unsupported"}', '{"intent": "delete everything", "topic": "MOSFETs"}'])
def test_an_interpretation_that_names_anything_not_asked_is_refused(reply):
    result = assistant.interpret_question("what are MOSFETs like", lambda s, u: reply)
    assert not result.ok and result.local_question is None


STATEMENTS = ("Definition: A capacitor stores electric charge. [notes.pdf p.3]",
              "Equation: C = Q/V, where C is 10 F in the example.")


def test_explanation_sends_the_question_and_the_numbered_statements_only():
    seen = []

    def fake(system, user):
        seen.append(user)
        return "A capacitor holds charge [1]. Its capacitance is charge over voltage [2]."

    result = assistant.explain("What is a capacitor?", STATEMENTS, fake)
    (payload,) = seen
    assert payload == assistant.evidence_payload("What is a capacitor?", STATEMENTS)
    assert payload.count("[1]") == 1 and payload.count("[2]") == 1 and "D:\\" not in payload
    assert [s.citations for s in result.sentences] == [(1,), (2,)] and result.removed == 0


def test_unsupported_sentences_and_numbers_are_removed_and_counted():
    reply = ("A capacitor holds charge [1]. It was invented in 1745 [1]. Capacitance is 10 F here [2]. "
             "Batteries also store energy. It is measured in farads [9].")
    result = assistant.explain("What is a capacitor?", STATEMENTS, lambda s, u: reply)
    kept = [s.text for s in result.sentences]
    assert kept == ["A capacitor holds charge.", "Capacitance is 10 F here."]
    assert result.removed == 3  # an invented year, an uncited claim, a citation to nothing


def test_insufficient_evidence_is_reported_as_such():
    assert assistant.explain("What is a gyrator?", STATEMENTS, lambda s, u: "INSUFFICIENT").insufficient
    assert assistant.explain("What is a gyrator?", (), lambda s, u: pytest.fail("nothing to send")).insufficient


def test_statements_sent_are_bounded():
    many = [f"Statement {n}." for n in range(40)]
    payload = assistant.evidence_payload("q", many)
    assert f"[{assistant.MAX_STATEMENTS}]" in payload and f"[{assistant.MAX_STATEMENTS + 1}]" not in payload
