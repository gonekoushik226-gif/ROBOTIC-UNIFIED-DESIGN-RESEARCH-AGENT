"""Choosing and chaining the stored equations that answer a calculation question.

    rearrange  writing a stored equation for another one of its quantities
    normalize  reading a stored equation's printed text as a formula for the engine
    library    the equations and named quantities the knowledge base holds, within scope
    planner    which equations connect what is known to what is asked, and in what order
    solver     names -> quantities, the plan, the exact calculation, confirmation, verification

The calculation itself is `app.calculation`'s exact engine and the check on it is
`app.verification`'s independent evaluator; this package decides *which formulas* they are
given, from the documents, so that nobody has to name an equation. No model, no embedding,
no search service: rules over the stored equations and variables, the same answer every time.
"""

from app.solving.solver import (
    Binding,
    Given,
    GivenReport,
    LibrarySummary,
    Missing,
    Route,
    SolveRequest,
    SolveResult,
    Status,
    StepReport,
    solve,
)

__all__ = [
    "Binding", "Given", "GivenReport", "LibrarySummary", "Missing", "Route", "SolveRequest", "SolveResult",
    "Status", "StepReport", "solve",
]
