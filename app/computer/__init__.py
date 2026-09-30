"""Phase 15 computer control: the Windows adapter behind the action engine's port (ADR 0047).

    windows  `WindowsPlatform`: documented Windows APIs through `ctypes` (D-08), no
             dependency - windows and processes, launching, closing by a normal close
             request, safe file operations, `SendInput`, screenshots

It decides nothing: whether a step may run is the permission engine's (`app.security`),
and whether it succeeded is the action's postcondition check (`app.actions`). The
reasoning engine may never import it (section 117).
"""
