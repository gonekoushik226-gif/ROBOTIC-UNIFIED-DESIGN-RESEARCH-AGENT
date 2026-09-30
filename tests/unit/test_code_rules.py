"""Code rules the specification states directly.

Part 4 section 134 forbids hiding errors:

    except:
        pass

Part 3 section 91 requires a real calculation engine rather than evaluated text,
which also means untrusted strings must never reach `eval` or `exec`. That risk
arrives with documents in Phase 4; the rule is enforced from the start so it never
has to be retrofitted.

These checks read the source with `ast`, so they cost nothing at runtime and cannot
be forgotten during review.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
BANNED_CALLS = {"eval", "exec", "compile"}


def _files() -> list[Path]:
    return sorted(APP.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_bare_except_clauses():
    violations = [
        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
        for path in _files()
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]
    assert not violations, (
        "bare 'except:' hides failures (Part 4 section 134): " + ", ".join(violations)
    )


def test_no_exception_handler_silently_passes():
    violations: list[str] = []
    for path in _files():
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [stmt for stmt in node.body if not _is_docstring(stmt)]
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not violations, (
        "a handler whose whole body is 'pass' swallows the failure "
        "(Part 4 section 134): " + ", ".join(violations)
    )


def test_no_dynamic_evaluation_of_strings():
    violations: list[str] = []
    for path in _files():
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in BANNED_CALLS
            ):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} calls {node.func.id}()"
                )
    assert not violations, (
        "calculations must run through the calculation engine, never through "
        "evaluated text: " + ", ".join(violations)
    )


def test_every_module_has_a_docstring():
    """Part 4 section 175 requires the architecture to be documented as it is built."""
    missing = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _files()
        if ast.get_docstring(_parse(path)) is None
    ]
    assert not missing, "modules without a docstring: " + ", ".join(missing)


def test_no_print_statements_outside_the_interface_layer():
    """Output belongs to the interface; everything else reports through logging."""
    violations: list[str] = []
    for path in _files():
        relative = path.relative_to(PROJECT_ROOT)
        if relative.parts[1] == "ui":
            continue
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                violations.append(f"{relative}:{node.lineno}")
    assert not violations, (
        "use the logger, not print(), outside app/ui: " + ", ".join(violations)
    )


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(
        stmt.value.value, str
    )
