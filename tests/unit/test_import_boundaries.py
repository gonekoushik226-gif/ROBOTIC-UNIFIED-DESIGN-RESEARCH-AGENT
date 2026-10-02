"""Architectural boundary enforcement.

Part 1 section 24 requires modularity and forbids tight coupling; Part 3 section 117
forbids the reasoning engine from executing computer operations directly. Those are
easy rules to state and easy to erode one import at a time, so they are checked
mechanically here with the standard library's `ast` module - no dependency, and it
fails the build rather than a code review.

Rules are written for the whole architecture, including packages that do not exist
yet. A rule about an absent package is skipped, and `test_rule_coverage_is_visible`
prints which rules are dormant so the skipping never goes unnoticed.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import PROJECT_ROOT

APP = PROJECT_ROOT / "app"

#: package -> the app packages it is allowed to import from.
#: A package may always import from itself.
ALLOWED: dict[str, set[str]] = {
    # The package itself: app/__init__.py and the app/__main__.py entry point,
    # which is allowed to reach the interface layer because wiring is its job.
    "app": {"app.version", "app.config", "app.core", "app.ui"},
    # Leaf: constants only, depends on nothing.
    "app.version": set(),
    # Pure schema. Must stay free of I/O and of core logic.
    "app.config": {"app.version"},
    # Foundation: configuration, errors, paths, logging, wiring.
    "app.core": {"app.version", "app.config"},
    # Interfaces may use the foundation but hold no logic of their own. Widened in
    # Phase 5 by app.documents and app.extraction (decision D-42/D-45, ADR 0021),
    # and for Phase 6 by app.classification (decision P6-11, ADR 0030), and for
    # Phase 7 by app.knowledge, whose ConceptService the read-only lookup exposes
    # (ADR 0031, I7-E): Part 4 section 140 makes the interface a client of the
    # architecture, so a client importing a service is the arrow pointing the right
    # way. The reverse (app.core -> app.ui) stays forbidden below. Widened for
    # Phase 8 by app.deduplication, which holds the merge, review and edition logic
    # the new commands present (ADR 0034, P8-23), for Phase 9 by app.query,
    # whose engine the `query` and `index` commands present (ADR 0037, P9-32), and for
    # Phase 10 by app.reasoning, whose engine the `reason` command presents (ADR 0040,
    # P10-26, P10-28), and for Phase 11 by app.calculation, whose engine the `calculate`
    # command will present (ADR 0043 P11-26, P11-29, P11-30 edit 2).
    "app.ui": {
        "app.version", "app.config", "app.core", "app.models", "app.storage",
        "app.documents", "app.extraction", "app.classification", "app.knowledge",
        "app.deduplication", "app.query", "app.reasoning", "app.calculation",
        # Phase 12 (ADR 0044 P12-14, P12-16): the `provenance` command presents both.
        "app.provenance", "app.verification",
        # Phase 13 (ADR 0045 P13-14): the `interpret` command presents it.
        "app.nlu",
        # Phase 14 (ADR 0046 P14-12): the `act` command presents the engine and registry.
        "app.actions", "app.applications",
        # Phase 15 (ADR 0047 P15-12): the `do` command presents the pipeline, and `act
        # --execute` the permission engine.
        "app.orchestration", "app.security",
        # Phase 16 (ADR 0048 P16-11): the `procedure` command presents procedural memory.
        "app.procedures",
        # Phase 17 (ADR 0049 P17-12): `extract --manual` and the `manual` command.
        "app.manuals",
        # Phase 18 (ADR 0050 P18-11): the `research` command.
        "app.internet",
        # Phase 19 (ADR 0051 P19-10): the `diagram` command.
        "app.diagrams",
        # Phase 20 (ADR 0052 P20-8): the `voice` command.
        "app.voice",
        # The desktop window's update notice, and its optional AI assistance.
        "app.updates", "app.providers",
        # The `solve` command: choosing and chaining the stored equations of a calculation question.
        "app.solving", "app.inventory",
    },
    # The update notice: standard library and the version constants only.
    "app.updates": {"app.version"},
    # Domain models: pure data. They need the error and result types, and nothing
    # else from the foundation - see FORBIDDEN_MODULES below.
    "app.models": {"app.version", "app.core"},
    # Persistence adapters. The only package allowed to contain SQL.
    "app.storage": {"app.version", "app.core", "app.models"},
    # Document ingestion. Needs storage for rows and models for types; holds no
    # SQL of its own and never reaches into the knowledge layer - Phase 4 stops
    # at stage 8 (ADR 0016).
    "app.documents": {"app.version", "app.models", "app.core", "app.storage"},
    "app.knowledge": {"app.version", "app.models", "app.core", "app.storage"},
    # Knowledge extraction (Phase 5, decision D-42, ADR 0021). Calls app.knowledge
    # to store correctly; deliberately NOT app.documents - segments come from
    # app.storage, so a parser swap (ADR 0014) cannot ripple into extraction.
    # Widened for Phase 8 by app.deduplication: stage 15 is called inline from the
    # extraction writer (ADR 0033 P8-11, ADR 0034 P8-23).
    "app.extraction": {
        "app.version", "app.models", "app.core", "app.storage", "app.knowledge",
        "app.deduplication",
    },
    # Knowledge organisation (Phase 6, decision P6-11, ADR 0030). Writes edges through
    # app.knowledge so the edge rules keep applying; reads stored Phase 5 output
    # through app.storage - never raw text, so neither the parser package nor the
    # extractor (ADR 0026, P6-17; forbidden pairs below). Dormant until Phase 6
    # creates the package.
    "app.classification": {"app.version", "app.models", "app.core", "app.storage", "app.knowledge"},
    # Knowledge deduplication (Phase 8, decision P8-23, ADR 0034). Reads stored data
    # through app.storage - never raw text - so neither the parser package nor the
    # extractor (forbidden pairs below).
    "app.deduplication": {"app.version", "app.models", "app.core", "app.storage", "app.knowledge"},
    # The query engine (Phase 9, decision P9-32, ADR 0037). Reads stored data through
    # app.storage and app.knowledge; reuses app.deduplication's `review` for the
    # number of sources (ADR 0036 P9-18). Never the parser or the extractor: it reads
    # what was stored, not raw text.
    "app.query": {
        "app.version", "app.models", "app.core", "app.storage", "app.knowledge",
        "app.deduplication",
    },
    # Dependency-based reasoning (Phase 10, decision U11(b), ADR 0040 P10-26). Widened by
    # app.storage, the way every package that reads stored knowledge reads it; never
    # app.query, whose scope and provenance rules it re-applies instead. Never the
    # action or computer packages (forbidden pairs below, section 117).
    "app.reasoning": {"app.version", "app.models", "app.core", "app.knowledge", "app.storage"},
    # The calculation engine (Phase 11, ADR 0043 P11-26, P11-30 edit 1). Widened by
    # app.storage only, because a request may admit one stored equation (N2 = (b)); never
    # app.knowledge (it resolves symbols, not concepts), app.reasoning, app.query or
    # app.extraction.
    "app.calculation": {"app.version", "app.models", "app.core", "app.storage"},
    # Provenance of stored items (Phase 12, ADR 0044 P12-14, P12-16): reads through
    # app.storage only, re-applying P9-5 and P9-23 with parity tests.
    "app.provenance": {"app.version", "app.models", "app.core", "app.storage"},
    # Answer verification (Phase 12, ADR 0044 P12-14, P12-16): re-runs calculation and
    # reasoning answers and cites through app.provenance. Nothing depends on it but the
    # interface, so no cycle is possible.
    "app.verification": {
        "app.version", "app.models", "app.core", "app.storage", "app.provenance",
        "app.calculation", "app.reasoning",
    },
    # Natural-language interpretation (Phase 13, ADR 0045 P13-14): the language layer
    # interprets and never acts, so it may import no storage and no engine (section 207).
    "app.nlu": {"app.version", "app.models", "app.core"},
    # --- Packages from later phases. Rules declared now, enforced when they exist. ---
    # The action engine (Phase 14, ADR 0046 P14-12): widened by app.applications, the
    # registry it resolves applications from (sections 99-100). The reasoning engine may
    # still never import it (the forbidden pair below, section 117).
    "app.actions": {"app.version", "app.models", "app.core", "app.applications"},
    # The application registry (Phase 14, ADR 0046 P14-3): data only.
    "app.applications": {"app.version", "app.models", "app.core"},
    # The Windows adapter behind the action engine's port (Phase 15, ADR 0047 P15-2, P15-12).
    "app.computer": {"app.version", "app.models", "app.core", "app.actions", "app.applications"},
    # The permission engine (Phase 15, ADR 0047 P15-6): judges plans; executes nothing.
    "app.security": {"app.version", "app.models", "app.core", "app.actions"},
    # The request pipeline (Phase 15, ADR 0047 P15-3): coordinates; implements no rule itself.
    # Widened for Phase 16 by app.procedures, whose documented procedures it runs on the
    # user's named request (ADR 0048 P16-7, P16-11).
    "app.orchestration": {
        "app.version", "app.models", "app.core", "app.nlu", "app.actions", "app.applications",
        "app.security", "app.computer", "app.procedures",
        # Phase 17 (ADR 0049 P17-9, P17-12): documented workflows for a request.
        "app.manuals",
    },
    # Procedural memory (Phase 16, ADR 0048 P16-11): the store - retrieval, steps, parameters,
    # history and recording - reading through app.storage, citing through app.provenance.
    "app.procedures": {"app.version", "app.models", "app.core", "app.storage", "app.provenance"},
    # Application documentation (Phase 17, ADR 0049 P17-12): the declaration, the manual stage
    # over stored segments, and a manual's seven kinds - never the parser or the extractor.
    "app.manuals": {"app.version", "app.models", "app.core", "app.storage", "app.procedures", "app.provenance"},
    # Controlled Internet (Phase 18, ADR 0050 P18-11): local first through the query engine,
    # the user's named site, external provenance; rule C1 from the deduplication rules.
    "app.internet": {"app.version", "app.models", "app.core", "app.storage", "app.query", "app.nlu",
                     "app.deduplication"},
    # Diagram generation (Phase 19, ADR 0051 P19-10): reads stored knowledge through the query
    # engine; draws; writes no database row.
    "app.diagrams": {"app.version", "app.models", "app.core", "app.storage", "app.query", "app.nlu"},
    # Voice (Phase 20, ADR 0052 P20-8): the speech bridge and the command grammar from the
    # registry; the transcript is handed to the pipeline by the interface, as typed text is.
    "app.voice": {"app.version", "app.models", "app.core", "app.applications"},
    "app.providers": {"app.version", "app.models", "app.core"},
    # Choosing the stored equations a calculation needs (after the interactive pass of the release):
    # reads them through app.storage, calculates with app.calculation's exact engine and checks the
    # result with app.verification's independent evaluator. It imports no query, reasoning or
    # extraction code - it reads what was stored, as the calculation engine does.
    "app.solving": {"app.version", "app.models", "app.core", "app.storage", "app.calculation", "app.verification"},
    # What RUDRA knows, as a person reads it: reads stored knowledge through app.storage and asks
    # app.solving which stored equations a calculation can use. Nothing writes through it.
    "app.inventory": {"app.version", "app.models", "app.core", "app.storage", "app.calculation", "app.solving"},
}

#: Pairs that must never be connected, whatever else is allowed.
FORBIDDEN_PAIRS: tuple[tuple[str, str, str], ...] = (
    (
        "app.reasoning",
        "app.actions",
        "Part 3 section 117: reasoning may produce an action plan as data, but it "
        "must not execute anything. Authorisation sits between them.",
    ),
    (
        "app.reasoning",
        "app.computer",
        "Part 3 section 117: the reasoning engine must not touch the operating system.",
    ),
    (
        "app.core",
        "app.ui",
        "Part 4 section 140: the interface is a client of the architecture, never the "
        "other way round.",
    ),
    (
        "app.config",
        "app.core",
        "The configuration schema must stay pure data so it can be inspected and "
        "tested without touching the filesystem.",
    ),
    (
        "app.extraction",
        "app.documents",
        "ADR 0021: extraction reads segments through app.storage. Importing the "
        "parser package would let a parser swap (ADR 0014) ripple into extraction.",
    ),
    (
        "app.models",
        "app.storage",
        "Models are pure data. Knowing how they are persisted would couple the "
        "conceptual model to SQLite, which Part 1 section 4 forbids.",
    ),
    (
        "app.classification",
        "app.documents",
        "ADR 0030 / ADR 0026 P6-17: classification reads stored Phase 5 output "
        "through app.storage, never raw text or the parser package.",
    ),
    (
        "app.classification",
        "app.extraction",
        "ADR 0030: classification organises what extraction stored; it must not "
        "reuse the extractor or re-read text (ADR 0026, P6-17).",
    ),
    (
        "app.deduplication",
        "app.documents",
        "ADR 0034 P8-23: deduplication reads stored data through app.storage, never "
        "raw text or the parser package.",
    ),
    (
        "app.deduplication",
        "app.extraction",
        "ADR 0034 P8-23: the extractor calls stage 15, never the other way round.",
    ),
)

#: Finer-grained bans: a package may import a module's package but not that module.
#: This keeps `app.models` restricted to the error and result types rather than
#: giving it the whole foundation.
FORBIDDEN_MODULES: tuple[tuple[str, str, str], ...] = (
    ("app.models", "app.core.config", "Models must not read configuration."),
    ("app.models", "app.core.paths", "Models must not know about the filesystem."),
    ("app.models", "app.core.logging_setup", "Models must not log; they return results."),
    ("app.models", "app.core.services", "Models must not reach the composition root."),
    ("app.models", "app.core.environment", "Models must not inspect the machine."),
)


def _python_files() -> list[Path]:
    return sorted(APP.rglob("*.py"))


def _package_of(path: Path) -> str:
    """The top-level app package a file belongs to, e.g. 'app.core'."""
    relative = path.relative_to(PROJECT_ROOT).with_suffix("")
    parts = relative.parts
    if len(parts) == 2:
        # app/__init__.py and app/__main__.py belong to the package itself;
        # app/version.py is its own leaf module.
        return "app" if parts[1].startswith("__") else f"app.{parts[1]}"
    return f"{parts[0]}.{parts[1]}"


def _imported_app_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app"):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # Relative imports would hide the dependency from this check.
            assert node.level == 0, f"{path}: use absolute imports, not relative ones"
            if node.module and node.module.startswith("app"):
                found.add(node.module)
    return found


def _top_package(module: str) -> str:
    parts = module.split(".")
    if len(parts) <= 1:
        return "app"
    return f"{parts[0]}.{parts[1]}"


def test_source_files_exist_to_check():
    assert _python_files(), "no Python files found under app/"


def test_no_package_imports_beyond_its_allowance():
    violations: list[str] = []
    for path in _python_files():
        package = _package_of(path)
        allowance = ALLOWED.get(package)
        if allowance is None:
            violations.append(
                f"{path}: package {package} has no declared import allowance. "
                "Add one to ALLOWED so the boundary is explicit."
            )
            continue
        for module in _imported_app_modules(path):
            target = _top_package(module)
            if target in (package, "app"):
                continue
            if target not in allowance:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}: {package} imports {module}, "
                    f"but may only import from {sorted(allowance) or 'nothing'}"
                )
    assert not violations, "\n".join(violations)


def test_forbidden_modules_are_not_imported():
    """Module-level bans, finer than the package-level allowance table."""
    violations: list[str] = []
    for path in _python_files():
        package = _package_of(path)
        imported = _imported_app_modules(path)
        for source, module, why in FORBIDDEN_MODULES:
            if package != source:
                continue
            if any(m == module or m.startswith(module + ".") for m in imported):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}: {source} must not import "
                    f"{module}. {why}"
                )
    assert not violations, "\n".join(violations)


def test_only_the_storage_package_contains_sql():
    """Part 1 section 4: the database must be replaceable.

    That only holds while SQL stays behind the repository layer, so no other
    package may contain SQL statements.
    """
    markers = ("SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE")
    violations: list[str] = []
    for path in _python_files():
        if _package_of(path) == "app.storage":
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)} contains {marker!r}")
    assert not violations, (
        "SQL outside app/storage couples the architecture to SQLite: "
        + ", ".join(violations)
    )


def test_forbidden_pairs_are_not_connected():
    violations: list[str] = []
    for path in _python_files():
        package = _package_of(path)
        imported = {_top_package(m) for m in _imported_app_modules(path)}
        for source, target, why in FORBIDDEN_PAIRS:
            if package == source and target in imported:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}: {source} must not import "
                    f"{target}. {why}"
                )
    assert not violations, "\n".join(violations)


def test_rule_coverage_is_visible(capsys):
    """Report which rules are dormant, so silence is never mistaken for compliance."""
    present = {_package_of(p) for p in _python_files()}
    dormant = sorted(set(ALLOWED) - present)
    with capsys.disabled():
        if dormant:
            print(
                "\n  import-boundary rules awaiting their package: "
                + ", ".join(dormant)
            )
    # Every package that exists must have a rule; dormant rules are expected.
    undeclared = present - set(ALLOWED)
    assert not undeclared, f"packages without an import rule: {sorted(undeclared)}"


# --------------------------------------------------- third-party confinement


def test_pypdf_is_imported_only_by_its_adapter():
    """Decision D-03 / ADR 0014: the parser must stay replaceable.

    ADR 0014 records that `pymupdf` may replace `pypdf` if extraction quality on a
    real textbook proves inadequate, and says that swap is "one adapter". That is
    only true while nothing else imports the library, so it is checked rather than
    trusted.
    """
    import ast

    offenders = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name == "pypdf" or name.startswith("pypdf.") for name in names):
                relative = path.relative_to(PROJECT_ROOT).as_posix()
                if relative != "app/documents/pypdf_adapter.py":
                    offenders.append(relative)
    assert not offenders, (
        "pypdf may only be imported by app/documents/pypdf_adapter.py (ADR 0014); "
        f"found it in: {sorted(set(offenders))}"
    )


def test_the_only_runtime_dependency_is_pypdf():
    """Part 1 section 15. RUDRA had zero runtime dependencies before Phase 4."""
    import ast
    import sys

    stdlib = set(sys.stdlib_module_names)
    external = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root and root not in stdlib and root != "app":
                    external.add(root)
    assert external == {"pypdf"}, (
        "RUDRA's runtime dependencies must be exactly {'pypdf'} (ADR 0014); "
        f"found {sorted(external)}"
    )
