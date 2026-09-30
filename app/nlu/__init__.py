"""Phase 13 natural-language interpretation (ADR 0045; Part 5 sections 206-207).

    lexicon      the closed vocabularies: applications, risk levels, task classes, examples
    results      the structured intent (section 96) and the interpretation, with JSON
    interpreter  `interpret(text)`: the deterministic, rule-based request grammar

The language layer **interprets and never acts** (section 207): it imports no storage and
no engine, opens no database or file, and runs nothing - its commands and action requests
are data. No model, provider, dependency or network is involved (P13-1).
"""

from app.nlu.interpreter import GRAMMAR_NAME, GRAMMAR_VERSION, interpret
from app.nlu.results import (
    ActionRequest,
    Entity,
    EntityType,
    Interpretation,
    InterpretationStatus,
    StructuredIntent,
    command_text,
    to_json,
)

__all__ = [
    "GRAMMAR_NAME",
    "GRAMMAR_VERSION",
    "ActionRequest",
    "Entity",
    "EntityType",
    "Interpretation",
    "InterpretationStatus",
    "StructuredIntent",
    "command_text",
    "interpret",
    "to_json",
]
