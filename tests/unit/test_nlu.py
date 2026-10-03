"""Phase 13: the deterministic request interpreter (ADR 0045 P13-1 ... P13-13).

What every test holds the interpreter to: section 96's intent object; section 95's task
classes plus KNOWLEDGE_REASONING; exact, textual entity and parameter extraction; a
calculation without formulas INCOMPLETE, never a guessed formula or value; ambiguity
reported with every candidate, never a choice; compound requests split; declared risk
levels with confirmation for deletion and web search; structured commands and action
requests as data; deterministic output; and no reasoning, reading or writing at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.errors import InvalidInputError
from app.models.enums import RiskLevel
from app.nlu import InterpretationStatus as S
from app.nlu import interpret, to_json
from app.nlu.lexicon import TASK_CLASSES, resolve_application
from tests.conftest import PROJECT_ROOT


def _one(text):
    result = interpret(text)
    (intent,) = result.intents
    return result, intent


# ------------------------------------------------------------------ section 207


def test_open_chrome_is_open_application_with_application_chrome():
    result, intent = _one("Open Chrome.")
    assert result.status is S.INTERPRETED
    assert (intent.intent_type, intent.target, intent.task_classes) == (
        "OPEN_APPLICATION", "Chrome", ("APPLICATION_CONTROL",))
    assert intent.parameters == (("application", "Chrome"),)
    assert (intent.risk_level, intent.requires_confirmation) == (RiskLevel.LOW, False)
    assert intent.action.action == "OPEN_APPLICATION" and intent.command is None
    assert intent.entities[0].known is True and intent.intent_id == "intent-1"


def test_calculate_the_drain_current_is_calculation_and_knowledge_reasoning_with_its_target():
    result, intent = _one("Calculate the drain current.")
    assert result.status is S.INCOMPLETE
    assert intent.intent_type == "CALCULATE" and intent.target == "drain current"
    assert intent.task_classes == ("CALCULATION", "KNOWLEDGE_REASONING")
    assert intent.command is None  # no formula or value is guessed
    assert any("the values you know" in m for m in intent.missing)
    assert intent.routes == (("reason", "drain current"),)


def test_the_language_layer_imports_no_storage_and_no_engine():
    """Section 207: it cannot reason, because it cannot reach anything that does."""
    for path in (PROJECT_ROOT / "app" / "nlu").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            names = [module] if module else [a.name for a in getattr(node, "names", ())] if isinstance(node, ast.Import) else []
            for name in names:
                assert not name.startswith(("app.storage", "app.calculation", "app.reasoning", "app.query",
                                            "app.knowledge", "app.provenance", "app.verification")), (path.name, name)


# ------------------------------------------------------------ recognition


@pytest.mark.parametrize(
    ("text", "intent_type", "target", "command"),
    [
        ("What is voltage?", "QUERY_CONCEPT", "voltage", ("query", "--name", "voltage")),
        ("Define a resistor", "QUERY_CONCEPT", "resistor", ("query", "--name", "resistor")),
        ("Find everything about MOSFETs in my books.", "QUERY_CONCEPT", "MOSFETs",
         ("query", "--name", "MOSFETs", "--scope", "my-books")),
        ("Which equations are associated with the diode?", "QUERY_CONCEPT", "diode", ("query", "--name", "diode")),
        ("What are the prerequisites of Thevenin's theorem?", "QUERY_CONCEPT", "Thevenin's theorem",
         ("query", "--name", "Thevenin's theorem")),
        ("What does Xenode require?", "REASON", "Xenode", ("reason", "Xenode")),
        ("Search for Norton", "KEYWORD_SEARCH", "Norton", ("query", "--keyword", "Norton")),
        ("Look up CPT-00000004", "QUERY_ITEM", "CPT-00000004", ("query", "CPT-00000004")),
        ("Where did you get K-00000002?", "SOURCE_QUERY", "K-00000002", ("provenance", "K-00000002")),
        ("What is 2 + 3?", "CALCULATE", "value", ("calculate", "value", "--formula", "value = 2 + 3")),
    ],
)
def test_knowledge_requests_become_the_commands_that_serve_them(text, intent_type, target, command):
    result, intent = _one(text)
    assert result.status is S.INTERPRETED
    assert (intent.intent_type, intent.target, intent.command) == (intent_type, target, command)
    assert (intent.risk_level, intent.requires_confirmation) == (RiskLevel.LOW, False)


def test_a_calculation_with_formulas_and_values_is_complete():
    _, intent = _one("Calculate I given I = V / R, V = 10 V and R = 5 Ω.")
    assert intent.command == ("calculate", "I", "--formula", "I = V / R", "--input", "V=10 V", "--input", "R=5 Ω")
    assert intent.parameters == (("formula", "I = V / R"), ("input", "V=10 V"), ("input", "R=5 Ω"))
    assert intent.status is S.INTERPRETED


def test_a_calculation_whose_target_is_not_a_symbol_is_incomplete_never_mapped_by_guess():
    _, intent = _one("Calculate the current given I = V / R, V = 10 V and R = 5 Ω")
    assert intent.status is S.INCOMPLETE and intent.command is None
    assert any("symbol that stands for 'current'" in m for m in intent.missing)


def test_a_calculation_reads_is_as_explicit_an_assignment_as_an_equals_sign():
    """Natural phrasing ("R1 is 10 ohms") is read exactly like "R1 = 10 ohms" - still
    textual and exact (P13-6), never a guessed value."""
    _, intent = _one("Calculate I given I is V / R, V is 10 V and R is 5 Ω.")
    assert intent.status is S.INTERPRETED
    assert intent.command == ("calculate", "I", "--formula", "I = V / R", "--input", "V=10 V", "--input", "R=5 Ω")


def test_a_calculation_with_only_values_is_for_rudra_to_solve_from_the_documents():
    """"Calculate the current if R1 is 10 ohms, R2 is 20 ohms and V is 10 volts": every value is
    read, in the units the engine reads, and RUDRA chooses the equations (the solve command)."""
    _, intent = _one("Calculate the current if R1 is 10 ohms, R2 is 20 ohms and V is 10 volts.")
    assert intent.status is S.INTERPRETED
    assert intent.command == ("solve", "current", "--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V")
    assert set(p for name, p in intent.parameters if name == "input") == {"R1=10 Ω", "R2=20 Ω", "V=10 V"}


@pytest.mark.parametrize("text, target, inputs", [
    ("Calculate I when V = 10 V and R = 5 Ω", "I", ["V=10 V", "R=5 Ω"]),
    ("Find the output voltage given Vin = 12 V, R1 = 10 kilohms and R2 = 4.7 kilohms", "output voltage",
     ["Vin=12 V", "R1=10 kΩ", "R2=4.7 kΩ"]),
    ("What is the power if the voltage is 10 volts and the current is 2 amps?", "power", ["voltage=10 V", "current=2 A"]),
    ("What is I when V equals 10 V and R equals 5 ohms", "I", ["V=10 V", "R=5 Ω"]),
    ("If V = 10 V and R = 5 Ω, what is the current?", "current", ["V=10 V", "R=5 Ω"]),
    ("Given a resistance of 5 ohms and a voltage of 10 volts, calculate the current", "current",
     ["resistance=5 Ω", "voltage=10 V"]),
    ("Compute P for V = 3 and I = 4", "P", ["V=3", "I=4"]),
])
def test_natural_calculation_questions_become_solve_requests(text, target, inputs):
    _, intent = _one(text)
    assert intent.status is S.INTERPRETED, intent.missing
    assert intent.command[:2] == ("solve", target)
    assert [intent.command[i + 1] for i in range(2, len(intent.command), 2)] == inputs


def test_a_calculation_with_formulas_still_uses_exactly_those_formulas():
    _, intent = _one("Calculate I given I = V / R, V = 10 V and R = 5 ohms")
    assert intent.command == ("calculate", "I", "--formula", "I = V / R", "--input", "V=10 V", "--input", "R=5 Ω")


@pytest.mark.parametrize("text", ["What is resistance when it is hot?", "What is gain at high frequency?",
                                  "What is the current in a series circuit?"])
def test_a_what_is_question_without_a_value_stays_a_knowledge_question(text):
    _, intent = _one(text)
    assert intent.intent_type == "QUERY_CONCEPT"


@pytest.mark.parametrize(("text", "target", "rule"), [
    ("Explain how a MOSFET works.", "MOSFET", "query.explain_how_works"),
    ("Describe how the sampling theorem works", "sampling theorem", "query.explain_how_works"),
    ("State Ohm's law.", "Ohm's law", "query.state_law"),
    ("What does Ohm's law state?", "Ohm's law", "query.state_law"),
    ("How is a MOSFET used?", "MOSFET", "query.how_used"),
    ("What are applications of a MOSFET?", "MOSFET", "query.applications"),
])
def test_common_explanation_and_law_phrasings_resolve_the_subject(text, target, rule):
    _, intent = _one(text)
    assert intent.status is S.INTERPRETED
    assert intent.intent_type == "QUERY_CONCEPT"
    assert intent.target == target
    assert intent.rule == rule
    assert intent.requested_output == ("applications" if rule in {"query.how_used", "query.applications"}
                                       else "explanation")


def test_a_calculation_with_nothing_known_says_what_it_needs():
    _, intent = _one("Calculate the output voltage")
    assert intent.status is S.INCOMPLETE and any("values you know" in m for m in intent.missing)


@pytest.mark.parametrize(
    ("text", "intent_type", "parameters", "risk", "confirm"),
    [
        ("Close Notepad", "CLOSE_APPLICATION", (("application", "Notepad"),), RiskLevel.MEDIUM, False),
        ("launch google chrome", "OPEN_APPLICATION", (("application", "Chrome"),), RiskLevel.LOW, False),
        ("Open report.pdf", "OPEN_FILE", (("path", "report.pdf"),), RiskLevel.LOW, False),
        ("Create a file called notes.txt", "CREATE_FILE", (("path", "notes.txt"),), RiskLevel.LOW, False),
        ("Create a folder named results", "CREATE_FOLDER", (("path", "results"),), RiskLevel.LOW, False),
        ("copy a.txt to b.txt", "COPY_FILE", (("source", "a.txt"), ("destination", "b.txt")), RiskLevel.LOW, False),
        ("move a.txt to archive", "MOVE_FILE", (("source", "a.txt"), ("destination", "archive")), RiskLevel.MEDIUM, False),
        ("rename a.txt to b.txt", "RENAME_FILE", (("source", "a.txt"), ("new_name", "b.txt")), RiskLevel.MEDIUM, False),
        ("Delete notes.txt", "DELETE_FILE", (("path", "notes.txt"),), RiskLevel.HIGH, True),
        ('type "hello world"', "TYPE_TEXT", (("text", "hello world"),), RiskLevel.LOW, False),
        ("press enter", "PRESS_KEY", (("keys", "enter"),), RiskLevel.MEDIUM, False),
        ("press ctrl + s", "HOTKEY", (("keys", "ctrl+s"),), RiskLevel.MEDIUM, False),
        ("Please take a screenshot.", "TAKE_SCREENSHOT", (), RiskLevel.LOW, False),
        ("Create a project called amplifier", "CREATE_PROJECT", (("name", "amplifier"),), RiskLevel.MEDIUM, False),
        ("Search the web for MOSFET datasheets", "WEB_SEARCH", (("query", "MOSFET datasheets"),), RiskLevel.MEDIUM, True),
    ],
)
def test_actions_are_action_requests_with_declared_risk(text, intent_type, parameters, risk, confirm):
    result, intent = _one(text)
    assert result.status is S.INTERPRETED and intent.intent_type == intent_type
    assert intent.parameters == parameters
    assert (intent.risk_level, intent.requires_confirmation) == (risk, confirm)
    assert intent.command is None and intent.action is not None


def test_an_unknown_application_is_kept_as_said_and_marked_unknown():
    _, intent = _one("Open Excel")
    assert intent.target == "Excel" and intent.entities[0].known is False
    assert any("application name table" in note for note in intent.notes)


def test_a_project_request_without_a_name_is_incomplete():
    result, intent = _one("Create a new project")
    assert result.status is S.INCOMPLETE and intent.missing == ("the project's name",)


# ---------------------------------------------------------------- ambiguity


def test_the_project_is_ambiguous_and_nothing_is_chosen():
    result, intent = _one("Open the project.")
    assert result.status is S.AMBIGUOUS and intent.intent_type == "UNRESOLVED_REFERENCE"
    assert intent.action is None and intent.command is None


def test_a_partial_application_name_lists_every_candidate():
    result, intent = _one("open note")
    assert result.status is S.AMBIGUOUS and intent.candidates == ("Notepad", "Notepad++")
    assert intent.action is None


def test_where_did_you_get_this_needs_its_reference():
    result, intent = _one("Where did you get this?")
    assert result.status is S.INCOMPLETE and intent.intent_type == "SOURCE_QUERY"
    assert intent.command is None and "not remembered" in intent.missing[0]


def test_resolving_an_application_is_exact_or_lists_candidates():
    assert resolve_application("CALC") == ("Calculator", ())
    assert resolve_application("notepad") == ("Notepad", ())
    assert resolve_application("no such app") == (None, ())


# --------------------------------------------------------- compound requests


def test_application_control_and_calculation_stay_distinct():
    """Section 237: "Open Calculator and calculate 123 × 456"."""
    result = interpret("Open Calculator and calculate 123 × 456")
    first, second = result.intents
    assert result.status is S.INTERPRETED
    assert (first.intent_type, first.target, first.intent_id) == ("OPEN_APPLICATION", "Calculator", "intent-1")
    assert (second.intent_type, second.task_classes[0], second.intent_id) == ("CALCULATE", "CALCULATION", "intent-2")
    assert second.command == ("calculate", "value", "--formula", "value = 123 × 456")


def test_and_inside_a_calculation_does_not_split_it():
    result = interpret("Calculate P given P = V * I, V = 2 V and I = 3 A")
    (intent,) = result.intents
    assert intent.command[-4:] == ("--input", "V=2 V", "--input", "I=3 A")


# --------------------------------------------------- unrecognised, invalid, deterministic


def test_text_outside_the_grammar_is_unrecognised_with_examples():
    result = interpret("Frobnicate the widget")
    assert result.status is S.UNRECOGNIZED and result.intents == () and result.examples


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_empty_or_over_long_text_is_an_invalid_request(text):
    with pytest.raises(InvalidInputError):
        interpret(text)


def test_interpretation_is_deterministic_and_names_its_grammar():
    first = to_json(interpret("Open Chrome and calculate 2 * 3"))
    assert to_json(interpret("Open Chrome and calculate 2 * 3")) == first
    result = interpret("Open Chrome")
    assert (result.grammar, result.grammar_version) == ("RUDRA request grammar", "3")  # version 3: more question shapes


def test_task_classes_are_section_95s_thirteen_plus_knowledge_reasoning():
    assert len(TASK_CLASSES) == 14 and TASK_CLASSES[-1] == "KNOWLEDGE_REASONING"
    assert "APPLICATION_CONTROL" in TASK_CLASSES and "SOURCE_QUERY" in TASK_CLASSES


def test_the_interpreter_writes_and_reads_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    interpret("Delete notes.txt")
    interpret("Open Chrome and calculate 2 * 3")
    assert list(Path(tmp_path).iterdir()) == []
