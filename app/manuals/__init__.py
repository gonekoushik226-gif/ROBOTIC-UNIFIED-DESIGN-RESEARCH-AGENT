"""Phase 17 application documentation (ADR 0049; Part 5 sections 214-215).

    declarations  the user's declaration that a document is an application's manual
    detectors     menu paths, shortcuts, constraints, file formats, inputs (pure)
    stage         the manual stage: those items from a declared manual's stored pages
    library       a declared manual's seven kinds, and its workflows for a request

A document never declares itself a manual (section 109; ADR 0023): only the user does.
Only declared manuals are used for a request (P17-10).
"""

from app.manuals.declarations import Declaration, declaration_of, declarations, declare, same_application
from app.manuals.library import Manual, ManualItem, ManualLibrary, WorkflowMatch, is_menu_path, workflow_inputs
from app.manuals.stage import METHOD_PREFIX, ManualStage, StageReport

__all__ = [
    "METHOD_PREFIX",
    "Declaration",
    "Manual",
    "ManualItem",
    "ManualLibrary",
    "ManualStage",
    "StageReport",
    "WorkflowMatch",
    "declaration_of",
    "declarations",
    "declare",
    "is_menu_path",
    "same_application",
    "workflow_inputs",
]
