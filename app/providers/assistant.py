"""What an external provider may do for RUDRA, and the checks that keep documents authoritative.

**Interpretation.** Only the question's own text is sent. The provider returns a
structured query - an intent from a fixed list and the topic - which RUDRA then runs
against its local knowledge. The topic must be words taken from the question: a provider
that names a topic the user did not write is refused, so it cannot steer the search with
knowledge of its own.

**Explanation.** Only the question and the numbered statements RUDRA already retrieved from
the user's documents are sent - no file names, paths, identifiers or anything else. The
provider must support every sentence with the numbers of the statements it uses. RUDRA then
checks each sentence: a sentence without valid statement numbers, or with a number
(a value, a date, a quantity) that none of its statements contains, is removed and
counted. What remains is shown as wording by the provider, next to the statements it rests
on - never as a source.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

#: One call: (system instruction, user message) -> the reply's text.
Send = Callable[[str, str], str]

INTENTS = {
    "definition": "What is {c}?",
    "explanation": "Explain {c}",
    "properties": "What are the properties of {c}?",
    "equations": "Which equations are associated with {c}?",
    "variables": "What variables are associated with {c}?",
    "summary": "Summarize {c}",
    "locations": "Where is {c} stated?",
    "compare_definitions": "Compare the definitions of {c}",
    "dependencies": "What does {c} depend on?",
}
MAX_STATEMENTS = 12
MAX_STATEMENT_CHARS = 1200
MAX_QUESTION_CHARS = 2000

INTERPRET_SYSTEM = (
    "You convert a question into a search request for a local document library. Do not answer the question "
    "and do not add information. Reply with JSON only, in this form: "
    '{"intent": "<one of: ' + ", ".join(INTENTS) + '>", "topic": "<the topic, copied word for word from the '
    'question>"}. If the question does not ask about a topic in one of these ways, reply {"intent": "unsupported"}.'
)
EXPLAIN_SYSTEM = (
    "You explain an answer to a question using ONLY the numbered statements provided, which come from the "
    "user's own documents. Do not use any other knowledge. Do not add facts, numbers, formulas, names or "
    "examples that are not in the statements. End every sentence with the numbers of the statements it relies "
    "on, for example [1] or [2][3]. Keep it short: at most six sentences. If the statements do not answer the "
    "question, reply with the single word INSUFFICIENT."
)


@dataclass(frozen=True)
class Interpretation:
    ok: bool
    intent: str | None = None
    topic: str | None = None
    #: The local question RUDRA will run, in its own grammar.
    local_question: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class GroundedSentence:
    text: str
    #: 1-based numbers of the statements the sentence rests on.
    citations: tuple[int, ...]


@dataclass(frozen=True)
class Explanation:
    sentences: tuple[GroundedSentence, ...]
    #: Sentences removed because no statement supports them.
    removed: int
    insufficient: bool
    statements: tuple[str, ...]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w'-]+", text.casefold()) if w]


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _json_reply(reply: str) -> dict | None:
    text = reply.strip()
    fenced = re.search(r"\{.*\}", text, re.S)
    if fenced is None:
        return None
    try:
        value = json.loads(fenced.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def interpret_question(question: str, send: Send) -> Interpretation:
    """A provider's reading of a question RUDRA's own grammar did not recognise."""
    question = " ".join(question.split())[:MAX_QUESTION_CHARS]
    if not question:
        return Interpretation(False, reason="There is no question.")
    data = _json_reply(send(INTERPRET_SYSTEM, question))
    if data is None:
        return Interpretation(False, reason="The provider's reply was not a search request.")
    intent = str(data.get("intent", "")).strip().lower()
    if intent == "unsupported" or intent not in INTENTS:
        return Interpretation(False, reason="The provider found no topic to look up in this question.")
    topic = " ".join(str(data.get("topic", "")).split()).strip(" .?!\"'")[:80]
    asked = {_stem(w) for w in _words(question)}
    topic_words = _words(topic)
    if not topic_words or any(_stem(w) not in asked for w in topic_words):
        return Interpretation(False, intent=intent, topic=topic or None,
                              reason="The provider named a topic that is not in the question, so it was not used.")
    return Interpretation(True, intent, topic, INTENTS[intent].format(c=topic))


def evidence_payload(question: str, statements: list[str] | tuple[str, ...]) -> str:
    """The only text an explanation request carries: the question and the numbered statements."""
    lines = [f"Question: {' '.join(question.split())[:MAX_QUESTION_CHARS]}", "",
             "Statements from the user's documents:"]
    for number, statement in enumerate(statements[:MAX_STATEMENTS], start=1):
        lines.append(f"[{number}] {' '.join(statement.split())[:MAX_STATEMENT_CHARS]}")
    return "\n".join(lines)


_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)(?:\s*(?:\[\d+\])+)?")
_CITATION = re.compile(r"\[(\d+)\]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def explain(question: str, statements: list[str] | tuple[str, ...], send: Send) -> Explanation:
    """A provider's wording of the retrieved statements, kept only where the statements support it."""
    used = tuple(" ".join(s.split())[:MAX_STATEMENT_CHARS] for s in statements[:MAX_STATEMENTS] if s.strip())
    if not used:
        return Explanation((), 0, True, used)
    reply = send(EXPLAIN_SYSTEM, evidence_payload(question, used)).strip()
    if not reply or reply.upper().startswith("INSUFFICIENT"):
        return Explanation((), 0, True, used)
    kept: list[GroundedSentence] = []
    removed = 0
    # Citations may follow the full stop: "... charge. [1]" belongs to the sentence before it.
    normalised = re.sub(r"\s+", " ", reply)
    for match in _SENTENCE.finditer(normalised):
        chunk = match.group(0).strip()
        if not chunk or not re.search(r"\w", _CITATION.sub("", chunk)):
            continue
        numbers = tuple(int(n) for n in _CITATION.findall(chunk))
        text = _CITATION.sub("", chunk).strip()
        valid = numbers and all(1 <= n <= len(used) for n in numbers)
        cited = " ".join(used[n - 1] for n in numbers if 1 <= n <= len(used))
        unsupported = [n for n in _NUMBER.findall(text) if n not in cited]
        if not valid or unsupported:
            removed += 1
            continue
        kept.append(GroundedSentence(re.sub(r"\s+([.!?,;:])", r"\1", text), tuple(dict.fromkeys(numbers))))
    return Explanation(tuple(kept), removed, not kept, used)
