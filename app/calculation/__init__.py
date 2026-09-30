"""Phase 11 calculation engine (Part 5 sections 202-203; ADRs 0041-0043).

**Status: IMPLEMENTED (Batches A and B); the `calculate` command is Batch C.**

    numbers    exact decimal literals; the "=" / "≈" display rule (ADR 0042 P11-13, P11-14)
    units      SI dimension vectors, the closed unit table, prefixes, exact conversion,
               coherent units (P11-15, P11-16)
    formulas   the formula grammar and its whitelist parser (P11-12; N1)
    evaluate   exact evaluation of one parsed expression with dimensional checks
    requests   the structured request and its checks (ADR 0043 P11-28; N2)
    results    the typed result and the returned trace, with canonical JSON (P11-22)
    scope      P9-5 authorisation and scope, P9-23 lifecycle, P9-22 order, re-applied
    admission  the one admitted stored equation: its checks, provenance and conflicts
               (P11-19)
    engine     backward formula resolution, methods, cycles, missing inputs,
               assumptions and the answer (P11-17, P11-18, P11-21, P11-23)

Calculation is structured-input only, deterministic, and writes nothing (ADR 0041 P11-6).
It performs no symbolic solving (N1) and uses no dependency beyond the standard library
(N8). It never imports `app.reasoning`, `app.query` or `app.extraction` (ADR 0043 P11-26).
"""

from app.calculation.engine import ENGINE_NAME, ENGINE_VERSION, CalculationEngine
from app.calculation.evaluate import EvaluationError, EvaluationProblem, evaluate
from app.calculation.formulas import (
    GRAMMAR_NAME,
    GRAMMAR_VERSION,
    Formula,
    FormulaError,
    FormulaProblem,
    is_symbol,
    parse_formula,
)
from app.calculation.numbers import DISPLAY_RULE, Displayed, NumberError, display, parse_number
from app.calculation.requests import (
    CalculationAssumption,
    CalculationInput,
    CalculationRequest,
    CalculationScope,
    CheckedRequest,
)
from app.calculation.results import (
    AnswerStatus,
    CalculationResult,
    MethodState,
    Origin,
    RefusalReason,
    SymbolState,
    Value,
    to_json,
)
from app.calculation.units import (
    UNIT_TABLE_NAME,
    UNIT_TABLE_VERSION,
    ConversionError,
    Dimension,
    Quantity,
    UnitError,
    UnitProblem,
    coherent_unit,
    parse_quantity,
    parse_unit,
    quantity,
    to_unit,
)

__all__ = [
    "AnswerStatus",
    "CalculationAssumption",
    "CalculationEngine",
    "CalculationInput",
    "CalculationRequest",
    "CalculationResult",
    "CalculationScope",
    "CheckedRequest",
    "ConversionError",
    "DISPLAY_RULE",
    "Dimension",
    "Displayed",
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "EvaluationError",
    "EvaluationProblem",
    "Formula",
    "FormulaError",
    "FormulaProblem",
    "GRAMMAR_NAME",
    "GRAMMAR_VERSION",
    "MethodState",
    "NumberError",
    "Origin",
    "Quantity",
    "RefusalReason",
    "SymbolState",
    "UNIT_TABLE_NAME",
    "UNIT_TABLE_VERSION",
    "UnitError",
    "UnitProblem",
    "Value",
    "coherent_unit",
    "display",
    "evaluate",
    "is_symbol",
    "parse_formula",
    "parse_number",
    "parse_quantity",
    "parse_unit",
    "quantity",
    "to_json",
    "to_unit",
]
