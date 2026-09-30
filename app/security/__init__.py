"""Phase 15 security: the permission engine (ADR 0047 P15-6; sections 16, 106-107).

    permission  `decide(plan, confirmed=...)`: LOW permitted, MEDIUM only with the user's
                explicit confirmation, HIGH refused; a dry run needs no permission
                `decide_documented(plan, confirmed=...)`: a documented procedure's steps
                - live only confirmed, and only LOW (ADR 0048 P16-6)

It judges plans and executes nothing.
"""

from app.security.permission import (
    POLICY_NAME,
    POLICY_VERSION,
    Decision,
    PermissionDecision,
    all_permitted,
    decide,
    decide_documented,
)

__all__ = ["POLICY_NAME", "POLICY_VERSION", "Decision", "PermissionDecision", "all_permitted", "decide", "decide_documented"]
