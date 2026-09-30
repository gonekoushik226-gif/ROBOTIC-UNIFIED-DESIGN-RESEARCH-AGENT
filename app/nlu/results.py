"""The read models of Phase 13 interpretation (ADR 0045 P13-3, P13-11, P13-12).

A `StructuredIntent` is section 96's intent object; an `Interpretation` is the whole
answer to one text. Read models, not entities: nothing here is persisted, and the stored
`Intent` table stays unused (P13-2). Every collection is a tuple and every model frozen
(ADR 0005). `to_json` gives the one canonical serialisation: the same text gives
byte-identical JSON (P13-13).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum

from app.models.enums import RiskLevel


class InterpretationStatus(StrEnum):
    #: Every intent is complete: its command or action request can be run as it stands.
    INTERPRETED = "INTERPRETED"
    #: Recognised, but something must still be supplied; what is missing is named.
    INCOMPLETE = "INCOMPLETE"
    #: More than one reading changes the outcome; every candidate is listed (section 97).
    AMBIGUOUS = "AMBIGUOUS"
    #: Outside the grammar; examples are given, nothing is guessed.
    UNRECOGNIZED = "UNRECOGNIZED"


class EntityType(StrEnum):
    APPLICATION = "APPLICATION"
    QUANTITY = "QUANTITY"
    CONCEPT = "CONCEPT"
    SYMBOL = "SYMBOL"
    IDENTIFIER = "IDENTIFIER"
    FILE_PATH = "FILE_PATH"
    VALUE = "VALUE"
    FORMULA = "FORMULA"
    EXPRESSION = "EXPRESSION"
    KEY = "KEY"
    TEXT = "TEXT"
    PROJECT = "PROJECT"
    SEARCH_TERM = "SEARCH_TERM"
    REFERENCE = "REFERENCE"


@dataclass(frozen=True, slots=True)
class Entity:
    """One thing the text names, exactly as said, with its normalised value."""

    type: EntityType
    text: str
    value: str
    #: For an application: whether the name is in the closed alias table. None otherwise.
    known: bool | None = None


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """An action for the action engine (Phase 14): a name and parameters. Data only."""

    action: str
    parameters: tuple[tuple[str, str], ...]
    #: Nothing executes an action before the Phase 15 pipeline: plan, permission,
    #: execution, verification, report.
    note: str = "an action request is data; it is executed only by the Phase 14-15 action pipeline"


@dataclass(frozen=True, slots=True)
class StructuredIntent:
    """Section 96's intent object, for one request."""

    intent_id: str
    intent_type: str
    #: Section 95's classes, plus KNOWLEDGE_REASONING (P13-4); the first is primary.
    task_classes: tuple[str, ...]
    status: InterpretationStatus
    #: The grammar rule that recognised it, so the interpretation can be traced.
    rule: str
    target: str | None = None
    entities: tuple[Entity, ...] = ()
    parameters: tuple[tuple[str, str], ...] = ()
    constraints: tuple[str, ...] = ()
    source_scope: str | None = None
    requested_output: str | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False
    #: What must still be supplied, for an INCOMPLETE intent.
    missing: tuple[str, ...] = ()
    #: Every reading, for an AMBIGUOUS intent; none is chosen.
    candidates: tuple[str, ...] = ()
    #: The exact `python -m app` arguments for a capability that exists; None otherwise.
    command: tuple[str, ...] | None = None
    #: Other commands that serve the request partially, labelled as routes, not answers.
    routes: tuple[tuple[str, ...], ...] = ()
    #: For an action or a procedure: the request for the action engine.
    action: ActionRequest | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Interpretation:
    """The whole interpretation of one text. Returned, never stored; nothing executed."""

    text: str
    status: InterpretationStatus
    message: str
    intents: tuple[StructuredIntent, ...]
    grammar: str
    grammar_version: str
    #: For an UNRECOGNIZED text: phrasings the grammar does recognise.
    examples: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def as_plain(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: as_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [as_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): as_plain(item) for key, item in value.items()}
    return value


def to_json(result: object) -> str:
    return json.dumps(as_plain(result), sort_keys=True, ensure_ascii=False, indent=2)


def command_text(command: tuple[str, ...]) -> str:
    """How a user would type the command: `python -m app …`, arguments quoted when needed."""
    shown = [arg if arg and not any(c.isspace() or c in "\"'" for c in arg) else '"' + arg.replace('"', '\\"') + '"'
             for arg in command]
    return "python -m app " + " ".join(shown)
