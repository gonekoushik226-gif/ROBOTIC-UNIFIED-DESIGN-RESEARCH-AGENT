"""Reading an answer file: untrusted input, compared and never believed (ADR 0044 P12-12).

An answer file is the canonical JSON a `calculate` or `reason` command printed with
`--json`. It may have been edited, truncated or produced by something else, so it is
treated as untrusted input (ADR 0023; Part 4 section 123):

- at most `MAX_BYTES`, UTF-8, valid JSON, a JSON object;
- recognised only as a **calculation answer** (its engine is the RUDRA calculation engine)
  or a **reasoning answer** (its rule is the requirement-set rule);
- every field this phase reads is type-checked; anything else is ignored;
- only the **recorded request** is used to act (it is run again), and the **recorded
  results** are only compared with what the run produces. Identifiers are re-read from
  the database; nothing in the file is executed.

A file that fails any of this is an invalid request (`InvalidInputError`, exit code 2).
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.calculation import CalculationAssumption, CalculationInput, CalculationRequest, CalculationScope
from app.core.errors import InvalidInputError
from app.reasoning import Admission, Assumption, ReasoningRequest, ReasoningScope, UserInput

MAX_BYTES = 10 * 2**20
CALCULATION_ENGINE = "RUDRA calculation engine"
REASONING_RULE = "REQUIREMENT_SET"


class AnswerKind(StrEnum):
    CALCULATION = "CALCULATION"
    REASONING = "REASONING"


@dataclass(frozen=True, slots=True)
class RecordedAnswer:
    """An answer file's kind, its rebuilt request, and its recorded content (unbelieved)."""

    kind: AnswerKind
    request: CalculationRequest | ReasoningRequest
    recorded: dict


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="verification.answer", data_changed=False, retry_safe=True,
        next_options=(
            'python -m app calculate I --formula "I = V / R" --input "V=10 V" --input "R=5 Ω" --json > answer.json',
            "python -m app provenance --answer answer.json",
        ),
    )


def _expect(value: object, kind: type | tuple[type, ...], where: str) -> object:
    if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
        raise refuse(f"The answer file's {where} is malformed.", f"Expected {kind}, found {type(value).__name__}.")
    return value


def _field(mapping: dict, key: str, kind: type | tuple[type, ...], where: str) -> object:
    if key not in mapping:
        raise refuse(f"The answer file has no {where}.{key}.", "It is not a complete RUDRA answer.")
    return _expect(mapping[key], kind, f"{where}.{key}")


def read_answer(path: Path) -> RecordedAnswer:
    """Read and recognise an answer file; raise `InvalidInputError` for anything else."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise refuse(f"The answer file {str(path)!r} cannot be read.", str(exc)) from exc
    if size > MAX_BYTES:
        raise refuse(f"The answer file is {size} bytes.", f"The limit is {MAX_BYTES} bytes.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise refuse("The answer file is not readable JSON.", str(exc)) from exc
    data = _expect(data, dict, "top level")
    versions = data.get("versions")
    if isinstance(versions, dict) and versions.get("engine") == CALCULATION_ENGINE:
        return RecordedAnswer(AnswerKind.CALCULATION, _calculation_request(data), data)
    if data.get("rule") == REASONING_RULE:
        return RecordedAnswer(AnswerKind.REASONING, _reasoning_request(data), data)
    raise refuse(
        "The file is not a RUDRA calculation or reasoning answer.",
        "Only the JSON printed by 'calculate --json' or 'reason --json' can be verified; ask "
        "about a stored item by its identifier instead.",
    )


def _strings(values: object, where: str) -> tuple[str, ...]:
    return tuple(_expect(v, str, where) for v in _expect(values, list, where))  # type: ignore[union-attr]


def _calculation_request(data: dict) -> CalculationRequest:
    request = _field(data, "request", dict, "answer")
    pairs = {}
    for name in ("inputs", "assumptions"):
        pairs[name] = tuple(
            (_field(_expect(item, dict, f"request.{name}"), "symbol", str, f"request.{name}"),
             _field(item, "value", str, f"request.{name}"))
            for item in _field(request, name, list, "request")  # type: ignore[union-attr]
        )
    try:
        scope = CalculationScope(_field(request, "scope", str, "request"))
    except ValueError:
        raise refuse("The answer file's request.scope is not a scope.", f"Got {request.get('scope')!r}.") from None
    built = CalculationRequest(
        _field(request, "target", str, "request"),  # type: ignore[arg-type]
        formulas=_strings(_field(request, "formulas", list, "request"), "request.formulas"),
        inputs=tuple(CalculationInput(s, v) for s, v in pairs["inputs"]),  # type: ignore[arg-type]
        assumptions=tuple(CalculationAssumption(s, v) for s, v in pairs["assumptions"]),  # type: ignore[arg-type]
        admissions=_strings(_field(request, "admissions", list, "request"), "request.admissions"),
        scope=scope,
    )
    try:
        built.check()
    except InvalidInputError as exc:
        raise refuse("The answer file's recorded request is not a valid calculation request.",
                     exc.report.summary) from exc
    for step in _field(data, "steps", list, "answer"):  # type: ignore[union-attr]
        step = _expect(step, dict, "steps")
        _field(step, "text", str, "step")
        _field(step, "symbol", str, "step")
        _field(step, "number", int, "step")
        result = _field(step, "result", dict, "step")
        _field(result, "exact", str, "step.result")
        _field(result, "dimension", list, "step.result")
        for given in _field(step, "inputs", list, "step"):  # type: ignore[union-attr]
            given = _expect(given, dict, "step.inputs")
            _field(given, "symbol", str, "step.inputs")
            value = _field(given, "value", dict, "step.inputs")
            _field(value, "exact", str, "step.inputs.value")
            _field(value, "dimension", list, "step.inputs.value")
    _field(data, "status", str, "answer")
    _field(data, "symbols", list, "answer")
    return built


def _reasoning_request(data: dict) -> ReasoningRequest:
    request = _field(data, "request", dict, "answer")
    mode = _field(request, "mode", str, "request")
    try:
        scope = ReasoningScope(_field(request, "scope", str, "request"))
    except ValueError:
        raise refuse("The answer file's request.scope is not a scope.", f"Got {request.get('scope')!r}.") from None

    def optional(item: dict, key: str, where: str) -> str | None:
        value = item.get(key)
        return None if value is None else _expect(value, str, f"{where}.{key}")  # type: ignore[return-value]

    inputs = tuple(
        UserInput(_field(_expect(i, dict, "request.inputs"), "node", str, "request.inputs"),  # type: ignore[arg-type]
                  optional(i, "value", "request.inputs"))
        for i in _field(request, "inputs", list, "request")  # type: ignore[union-attr]
    )
    admissions = tuple(
        Admission(_field(_expect(a, dict, "request.admissions"), "node", str, "request.admissions"),  # type: ignore[arg-type]
                  _field(a, "knowledge_id", str, "request.admissions"))  # type: ignore[arg-type]
        for a in _field(request, "admissions", list, "request")  # type: ignore[union-attr]
    )
    assumptions = tuple(
        Assumption(_field(_expect(a, dict, "request.assumptions"), "node", str, "request.assumptions"),  # type: ignore[arg-type]
                   optional(a, "statement", "request.assumptions"))
        for a in _field(request, "assumptions", list, "request")  # type: ignore[union-attr]
    )
    options = {"inputs": inputs, "admissions": admissions, "assumptions": assumptions, "scope": scope}
    if mode == "TARGET":
        built = ReasoningRequest.of_target(_field(request, "target", str, "request"), **options)  # type: ignore[arg-type]
    elif mode == "FORWARD":
        built = ReasoningRequest.forward(**options)
    else:
        raise refuse("The answer file's request.mode is not TARGET or FORWARD.", f"Got {mode!r}.")
    try:
        built.check()
    except InvalidInputError as exc:
        raise refuse("The answer file's recorded request is not a valid reasoning request.",
                     exc.report.summary) from exc
    parts = [_expect(m, dict, "methods") for m in _field(data, "methods", list, "answer")]  # type: ignore[union-attr]
    forward = data.get("forward")
    if forward is not None:
        parts.append(_expect(forward, dict, "forward"))
        _strings(_field(forward, "derived", list, "forward"), "forward.derived")
    for part in parts:
        _field(part, "nodes", list, "part")
        for step in _field(part, "steps", list, "part"):  # type: ignore[union-attr]
            step = _expect(step, dict, "steps")
            _field(step, "concept_id", str, "step")
            _strings(_field(step, "requirements", list, "step"), "step.requirements")
            _strings(_field(step, "relationships", list, "step"), "step.relationships")
    _field(data, "status", str, "answer")
    return built
