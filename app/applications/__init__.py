"""The application registry (Part 3 sections 99-100; P6 sections 26-27; ADR 0046 P14-3).

Application-specific data, kept apart from action logic: which applications RUDRA knows,
the names a user says for each, how each is launched (methods in order), how it is
detected running, how a launch is verified, and its known limitations. No action code
names an application; `OPEN_APPLICATION` reads everything it needs from here.

The entries are the applications Phase 0 found on this machine (`DEVELOPMENT_STATE.md`
§3). Nothing here claims more than the machine showed: MATLAB is a Chrome web app, and
the registry says so.
"""

from app.applications.registry import (
    APPLICATIONS,
    REGISTRY_NAME,
    REGISTRY_VERSION,
    Application,
    Detection,
    LaunchKind,
    LaunchMethod,
    find,
)

__all__ = [
    "APPLICATIONS",
    "REGISTRY_NAME",
    "REGISTRY_VERSION",
    "Application",
    "Detection",
    "LaunchKind",
    "LaunchMethod",
    "find",
]
