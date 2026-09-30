"""The interpreter's closed vocabularies (ADR 0045 P13-4, P13-6, P13-10).

- **Applications:** the applications Phase 0 found installed on this machine
  (`DEVELOPMENT_STATE.md` §3), each with the names a user says for it. A name is matched
  exactly (case-folded); a name that only begins the names of applications is
  ambiguous and lists them; any other name is kept as said and marked unknown. Whether an
  application can actually be launched is Phases 14–15's application registry.
- **Risk levels** as section 106's examples place them, and which intents need
  confirmation (section 107). Declared here; authorised by the permission engine later.
- **Task classes:** section 95's thirteen plus `KNOWLEDGE_REASONING` (section 207).
"""

from app.models.enums import RiskLevel, TaskClass

#: Section 207's added class (section 95: "The system may add additional classes").
KNOWLEDGE_REASONING = "KNOWLEDGE_REASONING"
TASK_CLASSES: tuple[str, ...] = (*(c.value for c in TaskClass), KNOWLEDGE_REASONING)

#: canonical application name -> the names a user says for it (case-folded).
APPLICATIONS: dict[str, tuple[str, ...]] = {
    "Chrome": ("chrome", "google chrome"),
    "Word": ("word", "microsoft word", "ms word", "winword"),
    "Notepad": ("notepad",),
    "Calculator": ("calculator", "calc", "windows calculator"),
    "Notepad++": ("notepad++", "notepad plus plus"),
    "Visual Studio Code": ("visual studio code", "vs code", "vscode"),
    "Keil µVision": ("keil", "keil uvision", "keil µvision", "uvision", "µvision"),
    "MATLAB": ("matlab",),
}

_ALIAS: dict[str, str] = {alias: name for name, aliases in APPLICATIONS.items() for alias in aliases}


def resolve_application(said: str) -> tuple[str | None, tuple[str, ...]]:
    """(the canonical name, candidates): an exact name, or the applications it may mean."""
    key = " ".join(said.casefold().split())
    if key in _ALIAS:
        return _ALIAS[key], ()
    if len(key) >= 3:
        candidates = sorted({name for alias, name in _ALIAS.items()
                             if alias.startswith(key) or f" {key}" in f" {alias}"})
        if candidates:
            return None, tuple(candidates)
    return None, ()


#: intent type -> (risk level, requires confirmation). Section 106's examples; section 107.
RISK: dict[str, tuple[RiskLevel, bool]] = {
    "OPEN_APPLICATION": (RiskLevel.LOW, False),
    "CLOSE_APPLICATION": (RiskLevel.MEDIUM, False),
    "OPEN_FILE": (RiskLevel.LOW, False),
    "CREATE_FILE": (RiskLevel.LOW, False),
    "CREATE_FOLDER": (RiskLevel.LOW, False),
    "COPY_FILE": (RiskLevel.LOW, False),
    "SAVE_FILE": (RiskLevel.MEDIUM, False),
    "MOVE_FILE": (RiskLevel.MEDIUM, False),
    "RENAME_FILE": (RiskLevel.MEDIUM, False),
    "DELETE_FILE": (RiskLevel.HIGH, True),
    "TYPE_TEXT": (RiskLevel.LOW, False),
    "PRESS_KEY": (RiskLevel.MEDIUM, False),
    "HOTKEY": (RiskLevel.MEDIUM, False),
    "PASTE": (RiskLevel.MEDIUM, False),
    "TAKE_SCREENSHOT": (RiskLevel.LOW, False),
    "CREATE_PROJECT": (RiskLevel.MEDIUM, False),
    "WEB_SEARCH": (RiskLevel.MEDIUM, True),
    "IMAGE_REQUEST": (RiskLevel.LOW, False),
}

#: Phrasings the grammar recognises, shown with an UNRECOGNIZED answer.
EXAMPLES: tuple[str, ...] = (
    "Open Chrome.",
    "Close Notepad.",
    "What is voltage?",
    "Find everything about MOSFETs in my books.",
    "What does X require?",
    "Calculate I given I = V / R, V = 10 V and R = 5 Ω.",
    "Calculate 123 × 456.",
    "Where did you get K-00000001?",
    "Create a file called notes.txt.",
    "Take a screenshot.",
    "Create a project called amplifier.",
)
