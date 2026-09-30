"""Phase 15 orchestration: the request pipeline (ADR 0047 P15-3; sections 17, 116, 211).

    pipeline    `Pipeline(platform).run(text)`: interpret, plan, validate, permission,
                execute, verify, report - every stage recorded
    procedures  `ProcedureRunner`: a documented procedure the user named, run as section
                112's sequence and recorded when live (Phase 16, ADR 0048)
    workflows   a documented workflow from a declared manual, constructed for a request and
                never executed (Phase 17, ADR 0049)

`live_platform()` gives the Windows adapter; it is the only place the orchestration layer
reaches a concrete adapter (the composition root of computer control).
"""

import json

from app.actions.results import as_plain
from app.orchestration.pipeline import UNAVAILABLE, Outcome, Pipeline, PipelineReport, Stage, to_request
from app.orchestration.procedures import ProcedureOutcome, ProcedureRun, ProcedureRunner, StepMapping


def live_platform():
    """The live Windows computer behind the platform port."""
    from app.computer.windows import WindowsPlatform

    return WindowsPlatform()


def to_json(report: object) -> str:
    return json.dumps(as_plain(report), sort_keys=True, ensure_ascii=False, indent=2)


__all__ = [
    "UNAVAILABLE", "Outcome", "Pipeline", "PipelineReport", "ProcedureOutcome", "ProcedureRun",
    "ProcedureRunner", "Stage", "StepMapping", "live_platform", "to_json", "to_request",
]
