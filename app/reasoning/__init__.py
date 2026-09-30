"""Phase 10 dependency-based reasoning (Part 5 sections 200-201; ADRs 0038-0040).

Reasoning is structured-input only (section 4; I-4): it takes a `ReasoningRequest`,
reads `knowledge.db` through `app.storage` and `app.knowledge` (ADR 0040 P10-26), and
writes nothing - derivations are returned, never persisted (U5(a)), and no
relationship is written (U10a(i)). It performs no numeric, unit, dimensional or
symbolic work (U6(a)), and never imports `app.query`, `app.actions` or
`app.computer` (section 117).

**The modules - the foundation (Phase 10 Batch A) and the engine (Batch B):**

    requests    the structured request and its checks (ADR 0040 P10-27)
    results     the node-state, origin and block vocabularies, evidence and
                provenance, the request's initial availability, the engine's
                result and trace, canonical JSON
    scope       source scope, lifecycle and ordering (P9-5, P9-22, P9-23 re-applied)
    provenance  evidence and provenance of a stored item; a superseded item's pointer
    inputs      the request's initial availability (OI-1 = Option B)
    context     one request's reads, scope verdicts and withheld counts
    graph       the stored dependency graph and its cycles
    conflicts   stored conflict information attached to a node (P10-19)
    engine      the requirement-set rule, backward and forward reasoning, node
                states, paths, methods, missing dependencies (ADR 0039)

The `reason` command (Batch C; ADR 0040 P10-28) presents this engine's result from
`app.ui.cli`; it holds no reasoning of its own.
"""

from app.reasoning.engine import RULE, RULE_VERSION, ReasoningEngine, reason
from app.reasoning.inputs import initial_availability, resolve_node
from app.reasoning.requests import (
    Admission,
    Assumption,
    ReasoningMode,
    ReasoningRequest,
    ReasoningScope,
    UserInput,
)
from app.reasoning.results import (
    AdmittedItem,
    AnswerStatus,
    Availability,
    Block,
    BlockReason,
    DerivationStep,
    ForwardResult,
    InitialAvailability,
    Method,
    NodeResult,
    NodeState,
    Origin,
    ReasoningResult,
    RefusalReason,
    RefusedAdmission,
    StatedAssumption,
    SuppliedInput,
    Withheld,
    to_json,
)

__all__ = [
    "RULE",
    "RULE_VERSION",
    "Admission",
    "AdmittedItem",
    "AnswerStatus",
    "Assumption",
    "Availability",
    "Block",
    "BlockReason",
    "DerivationStep",
    "ForwardResult",
    "InitialAvailability",
    "Method",
    "NodeResult",
    "NodeState",
    "Origin",
    "ReasoningEngine",
    "ReasoningMode",
    "ReasoningRequest",
    "ReasoningResult",
    "ReasoningScope",
    "RefusalReason",
    "RefusedAdmission",
    "StatedAssumption",
    "SuppliedInput",
    "UserInput",
    "Withheld",
    "initial_availability",
    "reason",
    "resolve_node",
    "to_json",
]
