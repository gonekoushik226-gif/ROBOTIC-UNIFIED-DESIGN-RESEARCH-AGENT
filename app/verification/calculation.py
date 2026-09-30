"""Verifying a recorded calculation answer (ADR 0044 P12-9, P12-10).

Three checks, each reported with what it found:

1. **REPRODUCTION** — the answer's recorded request is run again through the calculation
   engine on current data; the new answer must equal the recorded one in status, result
   (exact value, unit, dimension), missing symbols, every symbol's state, every step
   (symbol, formula, inputs, exact result) and the admission. An answer that admitted a
   stored equation needs `knowledge.db`; without it the check is `INCONCLUSIVE`.
2. **INDEPENDENT** — every recorded step is evaluated again from its recorded inputs by
   `app.verification.independent`, separate code with separate arithmetic.
3. **CHAIN** — every input a step takes from an earlier step equals that step's result,
   and the answer's result is its target's value.

A direct answer (the target supplied by the request) adds a `DIRECT_ANSWER` check,
`INCONCLUSIVE`: the value is the request's own input, and nothing independent confirms
it. The outcome is `FAILED` if any check failed, `INCONCLUSIVE` if any was inconclusive,
otherwise `VERIFIED` — including a reproduced *cannot determine*, verified as an answer.

**Source provenance** (P12-9): the admitted stored equation's provenance, re-read from the
database with its quote and file checks; or, when the answer used no stored source,
*"Provenance unavailable."* with that reason. No citation is ever made for a formula or
value the request supplied.
"""

from app.calculation import CalculationEngine
from app.calculation.results import as_plain as calculation_plain
from app.core.errors import InvalidInputError, StorageError
from app.provenance import (
    UNAVAILABLE_TEXT,
    Check,
    CheckStatus,
    ProvenanceScope,
    ProvenanceService,
    ProvenanceStatus,
    combined,
)
from app.storage.repository import Repository
from app.verification.answers import RecordedAnswer
from app.verification.independent import IndependentError, Operand, agrees, evaluate, exact_to_decimal
from app.verification.results import AnswerVerification, Exposure

NO_STORED_SOURCE = (
    f"{UNAVAILABLE_TEXT} Every formula and input of this answer was supplied by the request "
    "(USER_INPUT or ASSUMPTION); no stored source was used, and no citation is made."
)


def _fingerprint(data: dict) -> dict:
    """What must be identical between a recorded answer and its reproduction."""
    result = data.get("result")
    admitted = data.get("admitted")
    refused = data.get("refused")
    return {
        "status": data.get("status"),
        "result": None if result is None else (result["exact"], result["unit"], tuple(result["dimension"])),
        "missing": [(m["symbol"], tuple(m["required_by"])) for m in data.get("missing", ())],
        "symbols": [(s["symbol"], s["state"]) for s in data.get("symbols", ())],
        "steps": [
            (s["symbol"], s["text"], s["result"]["exact"], tuple(s["result"]["dimension"]),
             tuple((i["symbol"], i["value"]["exact"]) for i in s["inputs"]))
            for s in data.get("steps", ())
        ],
        "admitted": None if not admitted else admitted["knowledge"]["id"],
        "refused": None if not refused else (refused["knowledge_id"], refused["reason"]),
    }


def _reproduce(answer: RecordedAnswer, repository: Repository | None) -> tuple[Check, dict | None]:
    request = answer.request
    if request.admissions and repository is None:
        return Check("REPRODUCTION", "answer", CheckStatus.INCONCLUSIVE,
                     "the answer admits a stored equation and knowledge.db cannot be read, so it "
                     "cannot be run again"), None
    try:
        again = CalculationEngine(repository if request.admissions else None).calculate(request)
    except InvalidInputError as exc:
        return Check("REPRODUCTION", "answer", CheckStatus.FAILED,
                     f"the recorded request is no longer valid on current data: {exc.report.summary}"), None
    except StorageError as exc:
        return Check("REPRODUCTION", "answer", CheckStatus.INCONCLUSIVE,
                     f"knowledge.db cannot be read as this build reads it: {exc.report.summary}"), None
    fresh = calculation_plain(again)
    try:
        recorded = _fingerprint(answer.recorded)
    except (KeyError, TypeError, AttributeError) as exc:
        return Check("REPRODUCTION", "answer", CheckStatus.FAILED,
                     f"the recorded answer is incomplete or malformed ({type(exc).__name__}: {exc})"), fresh
    produced = _fingerprint(fresh)
    differing = [key for key in produced if produced[key] != recorded.get(key)]
    if differing:
        return Check("REPRODUCTION", "answer", CheckStatus.FAILED,
                     "running the recorded request again gives a different answer in: "
                     + ", ".join(differing)), fresh
    return Check("REPRODUCTION", "answer", CheckStatus.VERIFIED,
                 "running the recorded request again on current data gives the recorded answer"), fresh


def _independent(steps: list[dict]) -> list[Check]:
    checks = []
    for step in steps:
        subject = f"step {step['number']}"
        try:
            inputs = {
                given["symbol"]: Operand(exact_to_decimal(given["value"]["exact"]),
                                         tuple(int(d) for d in given["value"]["dimension"]))
                for given in step["inputs"]
            }
            found = evaluate(step["text"], inputs)
            ok, detail = agrees(found, step["result"]["exact"], tuple(int(d) for d in step["result"]["dimension"]))
        except (IndependentError, ValueError, TypeError) as exc:
            ok, detail = False, f"the independent evaluator could not re-evaluate it: {exc}"
        checks.append(Check("INDEPENDENT", subject,
                            CheckStatus.VERIFIED if ok else CheckStatus.FAILED, f"`{step['text']}`: {detail}"))
    return checks


def _chain(data: dict, steps: list[dict]) -> Check | None:
    result = data.get("result")
    if not steps and result is None:
        return None
    by_number = {step["number"]: step for step in steps}
    problems = []
    for step in steps:
        for given in step["inputs"]:
            source = given.get("from_step")
            if source is None:
                continue
            earlier = by_number.get(source)
            if earlier is None or earlier["result"]["exact"] != given["value"]["exact"]:
                problems.append(f"step {step['number']} takes {given['symbol']} from step {source}, "
                                "whose recorded result differs")
    target = data.get("request", {}).get("target")
    symbol = next((s for s in data.get("symbols", ()) if s.get("symbol") == target), None)
    if result is not None:
        value = None if symbol is None else symbol.get("value")
        if value is None or value.get("exact") != result.get("exact"):
            problems.append("the recorded result is not the target's recorded value")
        if symbol is not None and symbol.get("state") == "DERIVED" and not any(
                s["symbol"] == target and s["result"]["exact"] == result.get("exact") for s in steps):
            problems.append("no recorded step calculates the target's value")
    if problems:
        return Check("CHAIN", "answer", CheckStatus.FAILED, "; ".join(problems))
    return Check("CHAIN", "answer", CheckStatus.VERIFIED,
                 "every step's inputs from earlier steps, and the result, are consistent")


def verify_calculation(answer: RecordedAnswer, repository: Repository | None) -> AnswerVerification:
    """Verify one recorded calculation answer (module docstring). Reads only."""
    data = answer.recorded
    steps = data["steps"]
    reproduction, fresh = _reproduce(answer, repository)
    checks = [reproduction, *_independent(steps)]
    chain = _chain(data, steps)
    if chain is not None:
        checks.append(chain)
    target = answer.request.target
    symbol = next((s for s in data.get("symbols", ()) if isinstance(s, dict) and s.get("symbol") == target), None)
    if symbol is not None and symbol.get("state") == "AVAILABLE" and data.get("status") == "CALCULATED":
        checks.append(Check("DIRECT_ANSWER", target, CheckStatus.INCONCLUSIVE,
                            "the value is the request's own input; nothing independent confirms it"))
    status = combined(tuple(checks))
    exposure = _exposure(answer, fresh, repository, status)
    return AnswerVerification(
        kind=answer.kind.value,
        status=status,
        message=_message(status, checks, len(steps)),
        answer_status=str(data.get("status")),
        scope=answer.request.scope.value,
        checks=tuple(checks),
        exposure=exposure,
        database_opened=repository is not None and bool(answer.request.admissions),
        notes=("Nothing was written: the verification is returned only (ADR 0044 P12-3).",
               "The answer file was read as untrusted input: its request was run again and its "
               "results compared, never believed (P12-12)."),
    )


def _message(status: CheckStatus, checks: list[Check], steps: int) -> str:
    if status is CheckStatus.VERIFIED:
        return (f"Verification VERIFIED: the answer was reproduced on current data, and its {steps} "
                "step(s) agree with an independent re-evaluation.")
    first = next(c for c in checks if c.status is status)
    return f"Verification {status.value}: {first.kind} ({first.subject_id}) - {first.detail}."


def _exposure(answer: RecordedAnswer, fresh: dict | None, repository: Repository | None,
              status: CheckStatus) -> Exposure:
    data = answer.recorded
    basis = fresh if fresh is not None else data
    admitted = basis.get("admitted") if isinstance(basis, dict) else None
    used = admitted["knowledge"]["id"] if isinstance(admitted, dict) else None
    sources = ()
    if used is None:
        source_status, message = ProvenanceStatus.UNAVAILABLE, NO_STORED_SOURCE
    elif repository is None:
        source_status = ProvenanceStatus.UNAVAILABLE
        message = (f"{UNAVAILABLE_TEXT} The answer used the stored equation {used}, but knowledge.db "
                   "cannot be read, so its evidence cannot be shown.")
    else:
        try:
            item = ProvenanceService(repository).of_item(used, ProvenanceScope(answer.request.scope.value))
        except StorageError as exc:
            source_status = ProvenanceStatus.UNAVAILABLE
            message = f"{UNAVAILABLE_TEXT} knowledge.db cannot be read as this build reads it: {exc.report.summary}"
        else:
            sources = (item,)
            source_status = (ProvenanceStatus.AVAILABLE if item.status is ProvenanceStatus.AVAILABLE
                             else ProvenanceStatus.UNAVAILABLE)
            message = item.message
    steps = data["steps"]
    return Exposure(
        source_status=source_status,
        source_message=message,
        sources=sources,
        pages=tuple(page for item in sources for page in item.pages),
        relevant_knowledge=() if used is None else (used,),
        derivation=tuple(
            f"Step {s['number']}: {s['symbol']} by `{s['text']}` - formula source "
            + ("the request" if not s.get("knowledge_id") else f"stored equation {s['knowledge_id']}")
            for s in steps
        ),
        calculation=tuple(
            f"Step {s['number']}: {s.get('substitution', '')} -> {s['symbol']} "
            f"{s['result'].get('relation', '=')} {s['result'].get('displayed', '')} {s['result'].get('unit', '')}"
            f" (exact {s['result']['exact']})".replace("  ", " ")
            for s in steps
        ),
        assumptions=tuple(
            f"{a.get('symbol')} = {a.get('text')} (ASSUMPTION)"
            for a in data.get("assumptions", ()) if isinstance(a, dict)
        ),
        verification_status=status,
    )
