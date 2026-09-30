"""Phase 12 answer verification (ADR 0044 P12-9 ... P12-12).

    answers      reading an answer file: untrusted input, compared and never believed
    independent  a second, separate evaluator for calculation steps (Decimal, 50 digits)
    calculation  verifying a calculation answer: reproduction, independent re-check, chain
    reasoning    verifying a reasoning answer: reproduction, a re-check of every step
    results      the verification report and section 204's exposures

`verify(answer, repository)` returns `VERIFIED`, `FAILED` or `INCONCLUSIVE` with every
check and what it found. `INCONCLUSIVE` is never success (section 137). Reads only;
nothing is written (P12-3).
"""

from app.storage.repository import Repository
from app.verification.answers import AnswerKind, RecordedAnswer, read_answer
from app.verification.calculation import verify_calculation
from app.verification.reasoning import verify_reasoning
from app.verification.results import AnswerVerification, Exposure


def needs_database(answer: RecordedAnswer) -> bool:
    """Reasoning always reads knowledge.db; a calculation only for its admitted equation."""
    return answer.kind is AnswerKind.REASONING or bool(answer.request.admissions)


def verify(answer: RecordedAnswer, repository: Repository | None) -> AnswerVerification:
    """Verify a recorded answer; `repository` is None when knowledge.db cannot be read."""
    if answer.kind is AnswerKind.CALCULATION:
        return verify_calculation(answer, repository)
    return verify_reasoning(answer, repository)


__all__ = [
    "AnswerKind",
    "AnswerVerification",
    "Exposure",
    "RecordedAnswer",
    "needs_database",
    "read_answer",
    "verify",
]
