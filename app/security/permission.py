"""The permission engine (ADR 0047 P15-6; Part 1 section 16; Part 3 sections 106-107).

Every step of a plan is judged before anything runs, and the whole plan runs only when
every step is permitted - a partly permitted plan runs nothing, so a request is never
half carried out without the user knowing:

    LOW      permitted: the step is part of the user's own request (section 106's examples:
             open an application or a file, read, a screenshot, typing the user's text)
    MEDIUM   permitted only with the user's explicit confirmation (`--confirm`); otherwise
             refused, saying what would happen
    HIGH     refused: no HIGH-risk action is enabled in Phase 15 (section 107)

A dry run changes nothing, so it needs no permission; its steps are marked so. The
engine never widens a request (section 108): it judges the steps it is given, which come
only from the user's request - never from a document (section 109). A documented procedure
the user names is judged by `decide_documented` (ADR 0048 P16-6).
"""

from dataclasses import dataclass
from enum import StrEnum

from app.actions.results import ExecutionPlan
from app.models.enums import RiskLevel


class Decision(StrEnum):
    PERMITTED = "PERMITTED"
    REFUSED = "REFUSED"
    #: A dry run changes nothing; no permission is needed.
    NOT_REQUIRED = "NOT_REQUIRED"


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    step: int
    action: str
    risk_level: RiskLevel
    decision: Decision
    reason: str


POLICY_NAME = "RUDRA permission policy"
POLICY_VERSION = "1"


def decide(plan: ExecutionPlan, *, confirmed: bool) -> tuple[PermissionDecision, ...]:
    """One decision per step, with its reason."""
    decisions = []
    for step in plan.steps:
        if not plan.live:
            decision, reason = Decision.NOT_REQUIRED, "a dry run on the simulated computer changes nothing"
        elif step.risk_level is RiskLevel.LOW:
            decision, reason = Decision.PERMITTED, "LOW risk, and part of the user's own request (section 106)"
        elif step.risk_level is RiskLevel.MEDIUM and confirmed:
            decision, reason = Decision.PERMITTED, "MEDIUM risk, and the user confirmed it (--confirm)"
        elif step.risk_level is RiskLevel.MEDIUM:
            decision = Decision.REFUSED
            reason = (f"MEDIUM risk: {step.action} would be performed ({step.expected}); it needs the "
                      "user's explicit confirmation - run the request again with --confirm")
        else:
            decision, reason = Decision.REFUSED, "HIGH risk: no HIGH-risk action is enabled (section 107)"
        decisions.append(PermissionDecision(step.number, step.action, step.risk_level, decision, reason))
    return tuple(decisions)


def decide_documented(plan: ExecutionPlan, *, confirmed: bool) -> tuple[PermissionDecision, ...]:
    """One decision per step of a documented procedure the user named (ADR 0048 P16-6).

    Its steps come from a document (section 109): the document supplies their text as
    knowledge, and only the user's explicit, confirmed request makes them actions. Live,
    every step needs `--confirm`, and only LOW-risk steps are permitted - a MEDIUM or HIGH
    documented step is refused, because one flag for the whole run is not a per-step
    confirmation of a consequential action a document supplied. A dry run needs none.
    """
    decisions = []
    for step in plan.steps:
        if not plan.live:
            decision, reason = Decision.NOT_REQUIRED, "a dry run on the simulated computer changes nothing"
        elif step.risk_level is not RiskLevel.LOW:
            decision = Decision.REFUSED
            reason = (f"{step.risk_level.value} risk: {step.action} is a documented step, and a documented "
                      f"procedure runs only LOW-risk steps live (ADR 0048 P16-6)")
        elif not confirmed:
            decision = Decision.REFUSED
            reason = ("the step comes from a document (section 109): a documented procedure runs live only "
                      "with --confirm, after you have reviewed its steps")
        else:
            decision = Decision.PERMITTED
            reason = "LOW risk, from the documented procedure the user named and confirmed (section 109)"
        decisions.append(PermissionDecision(step.number, step.action, step.risk_level, decision, reason))
    return tuple(decisions)


def all_permitted(decisions: tuple[PermissionDecision, ...]) -> bool:
    return all(d.decision is not Decision.REFUSED for d in decisions)
