"""Phase 14 reusable action engine (ADR 0046; Part 5 sections 208-209).

    ports      the platform port: what an action may ask of the computer
    safety     confined paths and the closed key vocabulary
    png        a minimal PNG writer and reader (screenshots)
    simulated  a deterministic in-memory computer, for dry runs and tests
    results    plans, step results and reports (sections 103, 114, 116)
    catalogue  section 208's nineteen actions, one implementation each
    engine     `ActionEngine`: plan, then run through the port, verifying every step

The reasoning engine may never import this package (section 117). Deciding whether a
step is allowed is the permission engine's (Phase 15); in Phase 14 every run is a dry run
on the simulated computer.
"""

from app.actions.catalogue import CATALOGUE, ActionDefinition
from app.actions.engine import DRY_RUN_NOTE, ActionEngine
from app.actions.ports import FocusedText, LaunchOutcome, Platform, WindowInfo
from app.actions.results import (
    ExecutionPlan,
    ExecutionReport,
    PlanStep,
    StepResult,
    StepStatus,
    to_json,
)
from app.actions.simulated import SimulatedPlatform

__all__ = [
    "CATALOGUE",
    "DRY_RUN_NOTE",
    "ActionDefinition",
    "ActionEngine",
    "ExecutionPlan",
    "ExecutionReport",
    "FocusedText",
    "LaunchOutcome",
    "Platform",
    "PlanStep",
    "SimulatedPlatform",
    "StepResult",
    "StepStatus",
    "WindowInfo",
    "to_json",
]
