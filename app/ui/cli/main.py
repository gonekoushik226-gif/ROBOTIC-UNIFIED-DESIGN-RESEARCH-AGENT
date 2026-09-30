"""Command-line entry point.

Commands
--------
  start    Run the startup sequence and exit (the default).
  env      Print the environment report.
  config   Print every effective setting and where it came from.
  paths    Print the directory layout and each directory's storage class.
  db       Open the knowledge database, apply pending migrations, report it.
  extract  Ingest a PDF (if new) and extract knowledge from it (Phase 5).
  classify Organise one explicitly named extraction run (Phase 6).
  lookup   Read one concept and its provenance by exact id or exact name,
           read-only (Phase 7).
  review   Read one knowledge object's conflicts, equivalence assessments and
           sources by exact id, read-only (Phase 8).
  edition  Record the user's declaration that two ingested documents are
           editions of one work (Phase 8).
  merge    Supersede in place the exact duplicates stored before Phase 8; only
           when asked (Phase 8).
  query    Structured retrieval - concept, exact, page or keyword mode - read-only;
           the answer trace is returned, never stored (Phase 9).
  index    Build or rebuild the derived keyword index from knowledge.db; writes
           only data/indexes/index.db (Phase 9).
  reason   Dependency-based reasoning over stored REQUIRES / DEPENDS_ON
           relationships, read-only (Phase 10).
  provenance
           "Where did you get this?": the provenance of a stored item, or the
           verification of a calculate/reason answer; read-only (Phase 12).
  act      A parameterised action (section 208) through the action engine: a dry run
           on the simulated computer, or with --execute live, after the permission check.
  do       A request carried out on this computer: interpret, plan, permission,
           execute, verify, report (Phase 15).
  procedure
           Procedural memory: list and show the documented procedures RUDRA stores, and
           run one you name - dry, or live with --confirm, recorded (Phase 16).
  manual   Application manuals you declared (extract --manual): their menus, commands,
           workflows, shortcuts, parameters, constraints and file formats (Phase 17).
  research Local knowledge first; the Internet only through a website you name with
           --site, recorded and labelled external (Phase 18).
  diagram  A structure diagram of one concept (SVG and its specification) from stored
           knowledge only; unknowns marked, never invented (Phase 19).
  voice    Speech to text through Windows' own engine, then exactly what 'do' does with
           that text; voice never authorizes (Phase 20).
  ask      One request, typed or spoken, through the whole architecture: knowledge and
           actions joined, answered with sources and status (Phase 22).
  source   A document's source file: its status, and deleting RUDRA's copy while the
           knowledge and its provenance stay (Part 7; Phase 23).
  interpret
           Natural-language interpretation: a request's structured intents and the
           command it maps to, as data; nothing is run (Phase 13).
  calculate
           Exact, unit-aware calculation from a structured request - its formulas,
           inputs and assumptions, and at most one admitted stored equation;
           read-only, and knowledge.db is opened only for an admission (Phase 11).
  version  Print version information.

Exit codes are derived from the failure category, so a script can tell a
configuration mistake from a storage problem without parsing text.

This layer only presents results. It performs no reasoning, no calculation and no
knowledge work, and it must never become the place where such logic lives
(Part 4 section 140).
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import IntEnum
from pathlib import Path
from typing import Sequence

from app.config.schema import FIELDS
from app.core.errors import (
    FailureCategory,
    FailureReport,
    InvalidInputError,
    RudraError,
    StorageError,
    unexpected,
)
from app.core.services import StartupResult, start_application, stop_application
from app.version import APP_FULL_NAME, APP_NAME, PHASE, VERSION


class ExitCode(IntEnum):
    """Process exit codes."""

    OK = 0
    INVALID_INPUT = 2
    MISSING_INFORMATION = 3
    RESOURCE_LIMIT = 4
    STORAGE = 5
    #: A computer action's objective was not achieved (ADR 0047 P15-10).
    ACTION_FAILED = 6
    #: An authorized page could not be retrieved within the limits (ADR 0050 P18-12).
    EXTERNAL_FAILED = 7
    #: The speech engine could not be used (ADR 0052 P20-9).
    SPEECH_UNAVAILABLE = 8
    UNKNOWN_ERROR = 70


_EXIT_BY_CATEGORY = {
    FailureCategory.INVALID_INPUT: ExitCode.INVALID_INPUT,
    FailureCategory.MISSING_INFORMATION: ExitCode.MISSING_INFORMATION,
    FailureCategory.MISSING_SOURCE: ExitCode.MISSING_INFORMATION,
    FailureCategory.RESOURCE_LIMIT: ExitCode.RESOURCE_LIMIT,
    FailureCategory.DATABASE_FAILURE: ExitCode.STORAGE,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code; never raises."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    started: StartupResult | None = None
    code: ExitCode = ExitCode.UNKNOWN_ERROR
    try:
        started = start_application(
            project_root=_project_root(args),
            config_path=Path(args.config) if args.config else None,
        )
        handler = _COMMANDS[args.command]
        code = handler(started, args)
    except RudraError as exc:
        _print_failure(exc.report)
        code = _EXIT_BY_CATEGORY.get(exc.report.category, ExitCode.UNKNOWN_ERROR)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        code = ExitCode.UNKNOWN_ERROR
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        _print_failure(unexpected(exc, stage=f"cli.{args.command}"))
        code = ExitCode.UNKNOWN_ERROR
    finally:
        if started is not None:
            stop_application(started.context, exit_code=int(code))

    return int(code)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description=f"{APP_NAME} ({APP_FULL_NAME}) - {PHASE}",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="start",
        choices=(
            "start", "env", "config", "paths", "db", "extract", "classify", "lookup",
            "review", "edition", "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "act", "do", "procedure", "manual", "research", "diagram", "voice", "ask", "source", "version",
        ),
        help="What to do. Defaults to 'start'.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help=(
            "For 'extract': a PDF path, or a document id with --re-extract. "
            "For 'classify': a document id, accepted only when it has exactly one run. "
            "For 'lookup': an exact concept id, e.g. CPT-00000001. "
            "For 'review': an exact knowledge-object id, e.g. K-00000001. "
            "For 'edition': the document id being declared an edition of --work. "
            "For 'query': an exact concept, knowledge-object, relationship or document id. "
            "For 'reason': the target - a concept id or an exact concept name. "
            "For 'calculate': the target symbol, e.g. I. "
            "For 'provenance': any stored item's identifier, e.g. K-00000001. "
            "For 'interpret': the request, as quoted text. "
            "For 'act': the action, e.g. OPEN_APPLICATION. "
            "For 'do': the request, as quoted text. "
            "For 'procedure': a procedure identifier, e.g. PRC-00000001. "
            "For 'manual': a document identifier, e.g. DOC-00000001. "
            "For 'research': the question, as quoted text. "
            "For 'diagram': the request, as quoted text. "
            "For 'ask': the request, as quoted text. "
            "For 'source': a document identifier, e.g. DOC-00000001."
        ),
    )
    parser.add_argument(
        "--name",
        default=None,
        help=(
            "For 'lookup' and 'query': an exact concept name, matched after the D-30 "
            "normalisation (NFKC, casefold, whitespace collapse). Every concept "
            "answering to it is shown; none is chosen."
        ),
    )
    query = parser.add_argument_group(
        "query",
        "Exactly one mode: --name, an identifier, --document with --page, or --keyword. "
        "Filters may be repeated; different filters combine with AND, the values of one "
        "filter with OR (ADR 0036 P9-23).",
    )
    query.add_argument("--keyword", default=None, help="Keyword mode: one term, matched as a phrase.")
    query.add_argument("--prefix", action="store_true", help="Keyword mode: match the term's last word as a prefix.")
    query.add_argument("--document", default=None, help="Page mode: the document id.")
    query.add_argument("--page", default=None, help="Page mode: the page number.")
    query.add_argument(
        "--scope", default="my-books",
        help=(
            "For 'query', 'reason', 'calculate', 'provenance', 'procedure', 'research' and 'diagram': my-books (USER_PROVIDED or LOCAL sources, "
            "AUTHORIZED; the default) or authorized."
        ),
    )
    for flag, what in (
        ("--knowledge-type", "a knowledge type, e.g. DEFINITION"),
        ("--lifecycle", "a lifecycle status; replaces the default (all but DELETED and ARCHIVED)"),
        ("--certainty", "a certainty state"),
        ("--relation-type", "a relation type, e.g. USES"),
        ("--origin", "EXPLICIT or INFERRED"),
        ("--in-document", "a document id the evidence must come from"),
        ("--in-run", "an extraction run id the evidence must come from"),
        ("--run-status", "the status of the evidence's run, e.g. COMPLETED"),
        ("--extractor-version", "the extractor version of the evidence's run"),
        ("--source-category", "the category of the evidence's source"),
        ("--on-page", "the page the evidence is on"),
    ):
        query.add_argument(flag, action="append", default=None, help=f"Filter: {what}.")
    query.add_argument("--exclude-superseded", action="store_true",
                       help="Leave out superseded objects (listed and labelled by default).")
    query.add_argument("--no-widen", action="store_true",
                       help="Concept mode: no widening over stored equivalence data (D2).")
    reason = parser.add_argument_group(
        "reason",
        "A structured reasoning request (ADR 0040 P10-27): a target, or --forward. A node is "
        "a concept id or an exact concept name. Only what the request supplies or admits is "
        "available; stored knowledge is never available merely because it exists (OI-1).",
    )
    reason.add_argument("--forward", action="store_true",
                        help="Forward mode: everything that follows from what the request makes available.")
    reason.add_argument("--input", action="append", default=None, metavar="NODE[=VALUE]",
                        help="A node the request supplies as USER_INPUT; a value is carried verbatim. "
                             "For 'calculate': SYMBOL=VALUE, a number with at most one unit (R1=10 Ω).")
    reason.add_argument("--admit", action="append", default=None, metavar="NODE=K-ID",
                        help="Admit one stored knowledge object as a node's basis for being available. "
                             "For 'calculate': K-ID, the one stored equation the request admits.")
    reason.add_argument("--assume", action="append", default=None, metavar="NODE[=STATEMENT]",
                        help="A node assumed available; labelled as an assumption and never stored. "
                             "For 'calculate': SYMBOL=VALUE, labelled ASSUMPTION.")
    calculate = parser.add_argument_group(
        "calculate",
        "A structured calculation request (ADRs 0041-0043): a target symbol, --formula, "
        "--input SYMBOL=VALUE, --assume SYMBOL=VALUE, at most one --admit K-ID, and --scope. "
        "Exact rationals, SI units, no symbolic solving; results are PENDING verification and "
        "never stored. 'reason' refuses --formula (Phase 11 Step 0 decision (a)).",
    )
    calculate.add_argument("--formula", action="append", default=None, metavar="SYMBOL = EXPRESSION",
                           help="For 'calculate': a formula, its target alone on the left, every "
                                "operator explicit (+ - * / ^, parentheses).")
    provenance = parser.add_argument_group(
        "provenance",
        "'Where did you get this?' (ADR 0044): an identifier, or --answer with the JSON a "
        "calculate or reason command printed. Read-only; nothing is invented or written.",
    )
    act = parser.add_argument_group("act", "A parameterised action (ADR 0046); in Phase 14 a dry run.")
    act.add_argument("--param", action="append", default=None, metavar="NAME=VALUE",
                     help="For 'act' and 'procedure': one parameter, e.g. application=Notepad.")
    act.add_argument("--execute", action="store_true",
                     help="For 'act' and 'procedure': run live on this computer after the permission check.")
    act.add_argument("--confirm", action="store_true",
                     help="For 'act' and 'do': confirm a MEDIUM-risk step (section 107); for 'procedure': "
                          "confirm running a documented procedure live (section 109).")
    act.add_argument("--dry-run", action="store_true",
                     help="For 'do' and 'procedure': run on the simulated computer; nothing changes.")
    provenance.add_argument("--answer", default=None, metavar="FILE",
                            help="For 'provenance': verify this answer file and expose its sources.")
    parser.add_argument(
        "--re-extract",
        action="store_true",
        help="For 'extract': run a new extraction over an already-ingested document id.",
    )
    parser.add_argument(
        "--delete-file",
        action="store_true",
        help="For 'source': delete RUDRA's preserved copy of the document (with --confirm); the knowledge stays.",
    )
    parser.add_argument(
        "--audio",
        default=None,
        metavar="FILE",
        help="For 'voice': the WAV file to recognize, or with --say the new WAV file to write (Phase 20).",
    )
    parser.add_argument(
        "--listen",
        action="store_true",
        help="For 'voice': open the microphone for one utterance of at most 10 seconds; nothing is kept.",
    )
    parser.add_argument(
        "--say",
        default=None,
        metavar="TEXT",
        help="For 'voice': speak TEXT into the new WAV file --audio names.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="FOLDER",
        help="For 'diagram': the folder for the SVG and its specification (default: data/cache/diagrams).",
    )
    parser.add_argument(
        "--site",
        default=None,
        metavar="URL",
        help=(
            "For 'research': authorize this one website (its host and directory) for this request; "
            "the page is retrieved, recorded and labelled external (Phase 18)."
        ),
    )
    parser.add_argument(
        "--manual",
        default=None,
        metavar="APPLICATION",
        help=(
            "For 'extract': declare the document the manual of this application, and extract its "
            "menus, commands, workflows, shortcuts, parameters, constraints and file formats (Phase 17)."
        ),
    )
    parser.add_argument(
        "--run",
        default=None,
        help="For 'classify': the extraction run to classify, e.g. RUN-00000001.",
    )
    parser.add_argument(
        "--work",
        default=None,
        help="For 'edition': the document id of the work the edition belongs to.",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="For 'edition': the user's label for the declared edition, e.g. \"2nd edition\".",
    )
    parser.add_argument(
        "--work-label",
        default=None,
        help=(
            "For 'edition': the user's label for the work's own edition. Needed the "
            "first time a work is named; a recorded label is never rewritten."
        ),
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help=(
            "Project root. Defaults to the directory containing the app package "
            "(for the packaged RUDRA.exe, the directory containing the executable)."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Configuration file to use instead of config/rudra.toml.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON instead of text."
    )
    return parser


def _project_root(args: argparse.Namespace) -> Path:
    if args.project_root:
        return Path(args.project_root).expanduser().resolve()
    if getattr(sys, "frozen", False):
        # The packaged program (windows/build.py): installed, the user's data lives in
        # %LOCALAPPDATA%\RUDRA, apart from the program folder; a portable copy keeps it
        # beside the executable (app.core.paths.packaged_project_root).
        from app.core.paths import packaged_project_root

        return packaged_project_root(Path(sys.executable))
    # app/ui/cli/main.py -> app/ui/cli -> app/ui -> app -> project root
    return Path(__file__).resolve().parents[3]


def _cmd_start(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Phase 1 acceptance sequence, then a clean exit."""
    context, report = started.context, started.report
    if args.json:
        print(
            json.dumps(
                {
                    "started": True,
                    "version": VERSION,
                    "phase": PHASE,
                    "project_root": str(context.paths.project_root),
                    "config_file": (
                        str(context.loaded_config.config_file)
                        if context.loaded_config.file_present
                        else None
                    ),
                    "directories_created": [str(p) for p in context.directory_report.created],
                    "directories_existing": [str(p) for p in context.directory_report.existing],
                    "log_file": str(context.logging_report.log_file),
                    "audit_file": str(context.logging_report.audit_file),
                    "environment": report.to_dict(),
                },
                indent=2,
            )
        )
        return ExitCode.OK

    print(f"{APP_NAME} {VERSION}  ({PHASE})")
    print(f"  Project root : {context.paths.project_root}")
    print(
        "  Configuration: "
        + (
            str(context.loaded_config.config_file)
            if context.loaded_config.file_present
            else "built-in defaults (no config file)"
        )
    )
    print(
        f"  Directories  : {len(context.directory_report.created)} created, "
        f"{len(context.directory_report.existing)} already present"
    )
    print(f"  Log file     : {context.logging_report.log_file}")
    print(f"  Audit channel: {context.logging_report.audit_file}")
    print()
    print(report.to_text())

    problems = report.problems()
    if problems:
        print()
        print("Attention:")
        for item in problems:
            print(f"  [{item.status}] {item.component}: {item.impact}")
            if item.recommendation != "-":
                print(f"          {item.recommendation}")

    print()
    print("Startup complete. Available: PDF ingestion and knowledge extraction, queries,")
    print("reasoning, calculation, provenance, permission-checked computer actions and")
    print("procedures, application manuals, controlled Internet research, diagrams, voice,")
    print("'ask' and a desktop window (python -m app.ui.gui).")
    print("Several are only partially implemented; there is no OCR or language model.")
    print("See docs/LIMITATIONS.md. Commands: python -m app --help.")
    return ExitCode.OK


def _cmd_env(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    report = started.report
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return ExitCode.OK
    print(report.to_text())
    print()
    for item in report.components:
        print(f"{item.component}")
        print(f"  Detected      : {item.detected}")
        print(f"  Required      : {item.required}")
        print(f"  Available     : {_availability(item.available)}")
        print(f"  Impact        : {item.impact}")
        print(f"  Recommendation: {item.recommendation}")
    return ExitCode.OK


def _availability(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def _cmd_config(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    context = started.context
    effective: dict[str, object] = {}
    for spec in FIELDS:
        holder = (
            context.config
            if spec.section is None
            else getattr(context.config, spec.section)
        )
        effective[spec.dotted] = getattr(holder, spec.name)

    if args.json:
        print(
            json.dumps(
                {
                    "config_file": (
                        str(context.loaded_config.config_file)
                        if context.loaded_config.file_present
                        else None
                    ),
                    "settings": {
                        key: {"value": value, "origin": context.config_sources[key]}
                        for key, value in effective.items()
                    },
                },
                indent=2,
            )
        )
        return ExitCode.OK

    source = (
        str(context.loaded_config.config_file)
        if context.loaded_config.file_present
        else "not present - using built-in defaults"
    )
    print(f"Configuration file: {source}")
    print("Precedence: built-in defaults -> config file -> RUDRA_* environment variables")
    print()
    width = max(len(spec.dotted) for spec in FIELDS)
    for spec in FIELDS:
        value = effective[spec.dotted]
        print(f"{spec.dotted.ljust(width)}  = {value!r}")
        print(f"{' ' * width}    from {context.config_sources[spec.dotted]}")
        print(f"{' ' * width}    override with {spec.env_var}")
        print(f"{' ' * width}    {spec.description}")
    return ExitCode.OK


def _cmd_paths(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    specs = started.context.paths.managed_directories()
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "key": spec.key,
                        "path": str(spec.path),
                        "storage_class": str(spec.storage_class),
                        "purpose": spec.purpose,
                        "first_used": spec.first_used,
                        "exists": spec.path.is_dir(),
                    }
                    for spec in specs
                ],
                indent=2,
            )
        )
        return ExitCode.OK

    print("RUDRA keeps each kind of data in its own directory with its own lifecycle.")
    print("Deleting a rebuildable directory never destroys authoritative data;")
    print("deleting a source file never deletes the knowledge extracted from it.")
    print()
    for spec in specs:
        marker = "present" if spec.path.is_dir() else "MISSING"
        print(f"{spec.key}  [{spec.storage_class}]  ({marker}, used from {spec.first_used})")
        print(f"  {spec.path}")
        print(f"  {spec.purpose}")
    return ExitCode.OK


def _cmd_db(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Open the knowledge database, migrate it if needed, and report what is there.

    Migration is applied here rather than during `start`, so creating or changing
    a database is always something the user asked for explicitly. The sequence
    follows Part 4 section 151: detect, validate, back up, apply, verify.
    """
    # ENTITIES, not ALL_ENTITIES: the latter is frozen at the Part 5 section 184
    # twenty-two so that phase stays provable by counting (decision D-29), and
    # reporting it here would under-report the database by three tables.
    from app.models import ENTITIES
    from app.storage import (
        CODE_SCHEMA_VERSION,
        DatabaseRole,
        IdAllocator,
        Repository,
        applied_migrations,
        connect,
        database_path,
        migrate,
        read_metadata,
    )

    context = started.context
    path = database_path(context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE)
    try:
        report = migrate(connection, database_path=path)
        metadata = read_metadata(connection)
        repository = Repository(connection)
        counts = {entity.__name__: repository.count(entity) for entity in ENTITIES}
        history = applied_migrations(connection)
        counters = IdAllocator(connection).counters()
        connection.commit()
    finally:
        connection.close()

    if args.json:
        print(
            json.dumps(
                {
                    "database": str(path),
                    "created": report.created,
                    "schema_version": report.version_after,
                    "expected_schema_version": CODE_SCHEMA_VERSION,
                    "migrations_applied_now": list(report.applied),
                    "backup": str(report.backup_path) if report.backup_path else None,
                    "integrity": report.integrity,
                    "instance_id": metadata.get("instance_id"),
                    "row_counts": counts,
                    "next_identifiers": counters,
                    "migration_history": [dict(row) for row in history],
                },
                indent=2,
            )
        )
        return ExitCode.OK

    print(f"Database   : {path}")
    if report.created:
        print(f"             created, schema version {report.version_after}")
    elif report.applied:
        print(
            f"             migrated {report.version_before} -> {report.version_after}"
        )
        if report.backup_path:
            print(f"             backup taken: {report.backup_path}")
    else:
        print(f"             already at schema version {report.version_after}")
    print(f"Integrity  : {report.integrity}")
    print(f"Instance   : {metadata.get('instance_id', 'unknown')}")
    print(f"Tables     : {len(counts)} entity tables")
    print()
    stored = {name: n for name, n in counts.items() if n}
    if stored:
        width = max(len(name) for name in stored)
        for name, number in sorted(stored.items()):
            print(f"  {name.ljust(width)}  {number}")
    else:
        print("  No entities stored yet.")
        print()
        print("No document has been ingested. To ingest a PDF and extract knowledge:")
        print("  python -m app extract <path-to-pdf>")
    return ExitCode.OK


def _cmd_extract(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Ingest a document if it is new, then run one extraction over it (Phase 5).

    One command, two modes, and nothing more (decision D-45, ADR 0021):

        python -m app extract <path-to-document>
        python -m app extract --re-extract <DOC-id>

    Any supported format is accepted (PDF, Word, PowerPoint, Excel, EPUB, HTML, Markdown,
    text, CSV, RTF, and PNG/JPEG/TIFF images through OCR; docs/FORMATS.md). The format is
    identified by the file's bytes; PDF pages without usable text are read by OCR when
    Windows' OCR engine is available.

    The first matches Part 5 section 170's own flow - "Import one PDF -> Extract".
    It calls the Phase 4 pipeline unchanged; no ingestion logic lives here. A PDF
    that was already extracted is **not** extracted again silently - that would
    create a second run the user did not ask for. `--re-extract` is the explicit
    request, recorded with the `USER_REQUESTED` trigger (Part 2 section 62).

    The run is committed as `RUNNING` before any work, and the knowledge in a
    second commit, so a crash leaves an honest unfinished run and no half-written
    knowledge (Part 6 section 19). On failure the knowledge is rolled back and the
    run is recorded `FAILED`.
    """
    from app.documents import IngestionPipeline, PypdfParser
    from app.documents.readers import DocumentReader
    from app.extraction import CATEGORIES, ExtractionPipeline
    from app.models.enums import ExtractionTrigger
    from app.storage import DatabaseRole, Repository, connect, database_path, migrate
    from app.storage import queries

    if not args.target:
        raise InvalidInputError.of(
            "Nothing to extract from.",
            "The extract command needs a document path, or a document id with --re-extract.",
            stage="cli.extract",
            missing=("target",),
            data_changed=False,
            retry_safe=True,
            next_options=(
                "python -m app extract <path-to-document>",
                "python -m app extract --re-extract <DOC-id>",
            ),
        )

    if args.manual is not None:
        from app.manuals.declarations import check_name

        check_name(args.manual)  # refused before anything is ingested or extracted (ADR 0049 P17-2)

    context = started.context
    path = database_path(context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE)
    try:
        migration = migrate(connection, database_path=path)
        connection.commit()
        repository = Repository(connection)

        ingestion = None
        if args.re_extract:
            document_id = args.target
            trigger = ExtractionTrigger.USER_REQUESTED
        else:
            ingestion = IngestionPipeline(
                repository, DocumentReader(PypdfParser()), context.paths.documents_dir
            ).ingest(Path(args.target))
            connection.commit()
            document_id = ingestion.document.id
            previous = queries.runs_for_document(connection, document_id)
            if previous:
                print(f"Document   : {document_id} (already ingested)")
                print(f"             already extracted {len(previous)} time(s); "
                      f"latest run {previous[-1].id} is {previous[-1].status}.")
                if args.manual is not None:
                    _print_manual_stage(*_manual_after_extract(connection, document_id, args.manual))
                    return ExitCode.OK
                print("Nothing was changed. To extract again, run:")
                print(f"  python -m app extract --re-extract {document_id}")
                return ExitCode.OK
            trigger = ExtractionTrigger.FIRST_EXTRACTION

        if args.manual is not None:
            from app.manuals.declarations import check_declaration

            check_declaration(repository, document_id, args.manual)
        pipeline = ExtractionPipeline(repository, layout_dir=context.paths.documents_dir.parent / "extracted")
        run = pipeline.start(document_id, trigger=trigger)
        connection.commit()
        try:
            report = pipeline.execute(run)
            connection.commit()
        except Exception:
            connection.rollback()
            pipeline.mark_failed(run)
            connection.commit()
            raise
        manual = None if args.manual is None else _manual_after_extract(connection, document_id, args.manual)
    finally:
        connection.close()

    if migration.applied:
        print(f"Database   : migrated {migration.version_before} -> {migration.version_after}"
              + (f"; backup {migration.backup_path}" if migration.backup_path else ""))
    if ingestion is not None:
        state = "already ingested" if ingestion.already_ingested else "ingested now"
        units = {"PDF": "pages", "PPTX": "slides", "XLSX": "sheet parts", "IMAGE": "images/pages"}.get(
            ingestion.document.source_type, "sections")
        print(f"Document   : {document_id} ({state}, {ingestion.document.source_type}, "
              f"{ingestion.document.page_count} {units})")
        if ingestion.pages_ocr:
            print(f"OCR        : {len(ingestion.pages_ocr)} location(s) read by OCR "
                  f"({', '.join(str(n) for n in ingestion.pages_ocr[:12])}"
                  f"{', ...' if len(ingestion.pages_ocr) > 12 else ''}); that text is marked OCR and uncertain")
        for note in ingestion.notes:
            print(f"Note       : {note}")
    else:
        print(f"Document   : {document_id}")
    run = report.run
    print(f"Run        : {run.id}  run {run.run_number}  trigger {run.trigger}  "
          f"extractor v{run.extractor_version}")
    print(f"Status     : {run.status}")
    print(f"Pages      : {report.pages_examined} examined; {report.question_bank_pages} were "
          f"entirely question-bank material and yielded no claims")
    print(f"             {report.masked_characters:,} of {report.total_characters:,} characters "
          "masked from claim extraction (questions, options, example stems)")
    print()
    print(f"  {'category':<14}{'found':>7}{'stored':>8}{'linked':>8}")
    for category in CATEGORIES:
        print(f"  {category:<14}{report.found[category]:>7}{report.stored[category]:>8}"
              f"{report.linked.get(category, 0):>8}")
    print("  (linked: stored as a new source occurrence of an identical existing object -")
    print("   no new object and no new companion row; stage 15, ADR 0033 P8-12)")
    print()
    print(f"Relations  : {report.defined_by_edges} DEFINED_BY (concept -> its definition), "
          f"{report.semantic_edges} other stated relations - all EXPLICIT, all with evidence")
    print(f"Properties : {report.has_property_edges} property sentences recorded as HAS_PROPERTY "
          "(concept -> property), only for \"the property of X is ...\" with X defined here")
    _print_stage15(report)
    print(f"Equations  : {report.numeric_equations_skipped} numeric substitutions from worked "
          "solutions seen and not stored")
    if report.issues:
        print("Issues     : recorded, not ignored (Part 2 section 60)")
        for issue_type, count in report.issues.items():
            print(f"  {issue_type:<28}{count:>6}")
    print(f"Document status: {report.document_status_before} -> {report.document_status_after}"
          "  (Phase 5 never writes PROCESSED; see ADR 0024)")
    print("Ingestion  : not fully ingested - not indexed (Phase 9). PROCESSED describes parse")
    print("             and extraction health only (ADR 0032, P8-5/P8-6).")
    if manual is not None:
        _print_manual_stage(*manual)
    return ExitCode.OK


def _print_stage15(report) -> None:
    """Stage 15's results for one run (ADR 0034, P8-28)."""
    linked = sum(report.linked.values())
    print(f"Stage 15   : {linked} items linked to identical stored knowledge; the rest created "
          "as new objects and compared (deterministic; no score, no threshold)")
    outcomes = ("EXACT_DUPLICATE", "POSSIBLE_DUPLICATE", "CONTRADICTORY")
    counts = ", ".join(f"{o} {report.assessments.get(o, 0)}" for o in outcomes)
    print(f"Assessments: {counts}")
    if report.conflicts:
        print(f"Conflicts  : {len(report.conflicts)} created by rule C1 (numeric-only difference); "
              "cause UNDETERMINED; both claims kept ACTIVE")
        for conflict_id, claim_a, claim_b in report.conflicts:
            print(f"  {conflict_id}  {claim_a} vs {claim_b}   review: python -m app review {claim_b}")
    else:
        print("Conflicts  : none created")
    print(f"Concepts   : {report.concept_equivalences} POSSIBLE_EQUIVALENT concept records "
          "(never merged; each also a DUPLICATE_CONCEPT issue)")


def _cmd_classify(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Organise one explicitly named extraction run (Phase 6, ADRs 0026-0030).

        python -m app classify --run <RUN-id>
        python -m app classify <DOC-id>      # only when the document has one run

    One command, two input forms, nothing more (decision P6-11): no batch mode, no
    `--all`, no directory walk, no output formats, no classification of several
    runs at once, and no flag that picks a run automatically - Phase 6 has no
    current-run policy (ADR 0029). Selection, inference and the views all live in
    `app.classification`; this function only presents what they return.

    Opening the database follows the other commands: `migrate()` runs first, so
    the first `classify` on an existing database applies migration 0005 and takes
    its backup (ADR 0027).

    Nothing is claimed beyond what was committed (Part 4 section 137). A `PARTIAL`
    input run is stated as such, with its issue counts (decision P6-3b).
    """
    from app.classification import (
        AREA_STATUS,
        Classifier,
        hierarchy_view,
        organisation_view,
        select_run,
    )
    from app.storage import DatabaseRole, Repository, connect, database_path, migrate

    _tolerate_unencodable_output()
    context = started.context
    path = database_path(context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE)
    try:
        migration = migrate(connection, database_path=path)
        connection.commit()
        if migration.applied:
            print(f"Database   : migrated {migration.version_before} -> "
                  f"{migration.version_after}"
                  + (f"; backup {migration.backup_path}" if migration.backup_path else ""))
        repository = Repository(connection)
        run = select_run(repository, run_id=args.run, document_id=args.target)
        report = Classifier(repository).classify(run)
        hierarchy = hierarchy_view(repository, run.id)
        organisation = organisation_view(repository, run.id)
    finally:
        connection.close()

    run = report.run
    print(f"Input run  : {run.id}  document {run.document_id}  run {run.run_number}  "
          f"extractor v{run.extractor_version}")
    print(f"Run status : {run.status}")
    if report.partial:
        print("             This run is PARTIAL: it discarded some of the knowledge it found.")
        print("             The organisation below covers only the knowledge this run kept;")
        print("             what it discarded is absent from it.")
    if report.issue_counts:
        print("Run issues : recorded by that extraction run (Part 2 section 60)")
        for issue_type, count in report.issue_counts.items():
            print(f"  {issue_type:<28}{count:>6}")
    else:
        print("Run issues : none recorded by that extraction run")
    print(f"Rule       : {report.rule} v{report.rule_version} - a stored DEFINITION of concept X "
          "names concept Y of the same run")
    print("             -> one INFERRED RELATED_TO edge per pair, each with a recorded basis")
    print(f"Read       : {report.concepts} concepts, {report.names} names, "
          f"{report.definitions_read} definitions of this run")
    print(f"             {len(report.definitions_ambiguous)} definitions skipped: direction "
          "ambiguous (several DEFINED_BY edges or occurrences)")
    print(f"             {len(report.definitions_outside_run)} definitions skipped: defining "
          "concept is not a concept of this run")
    print(f"Mentions   : {report.mentions} accepted; {report.own_name_matches} of the defining "
          f"concept's own names skipped; {report.ambiguous_mentions} ambiguous names skipped")
    print(f"             {report.unmappable_mentions} skipped: their exact source text "
          "could not be proved")
    print(f"Committed  : {report.edges_created} new INFERRED RELATED_TO edges, "
          f"{report.basis_rows_added} new basis rows")
    print(f"             {report.bases_already_recorded} bases were already recorded "
          "(nothing written for them)")
    print(f"             {report.pairs_skipped_explicit} pairs skipped: a source already "
          "states RELATED_TO (EXPLICIT edge)")
    print()
    _print_hierarchy(hierarchy, organisation)
    print()
    _print_organisation(organisation)
    print()
    print("Part 5 section 192 areas in Phase 6 (ADR 0026):")
    for area, status in AREA_STATUS:
        print(f"  {area:<27}{status}")
    return ExitCode.OK


def _tolerate_unencodable_output() -> None:
    """Concept names are document text; never let one crash the report.

    Redirected output on Windows uses the ANSI code page, which cannot encode every
    character a PDF yields (the acceptance document has U+FFFD in a concept name).
    Replacing such a character on screen changes nothing that is stored.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")


def _edge_label(edge, names: dict[str, str]) -> str:
    """How one stored edge is backed: EXPLICIT with evidence, INFERRED with a basis."""
    relation = edge.relationship
    if edge.origin == "EXPLICIT":
        places = ", ".join(
            f"{o.id} p.{o.page_number}" if o.page_number is not None else o.id
            for o in edge.occurrences
        ) or "no occurrence recorded"
        return f"{relation.relation_type}, EXPLICIT - stated: {places}"
    if not edge.bases:
        return f"{relation.relation_type}, INFERRED - no basis recorded"
    parts = []
    for basis in edge.bases:
        inference, occurrence = basis.inference, basis.occurrence
        where = inference.basis_occurrence_id or "?"
        if occurrence is not None and occurrence.page_number is not None:
            where += f" p.{occurrence.page_number}"
        who = ", ".join(names.get(c, c) for c in basis.mentioning_concept_ids) or "?"
        # The stored text is the source's own (I6-B) and may hold a line break;
        # it is shown on one line, and stored unchanged.
        shown = " ".join((inference.matched_text or "?").split())
        parts.append(f"{inference.rule} v{inference.rule_version}: the definition of {who} "
                     f"({where}) names \"{shown}\"")
    return f"{relation.relation_type}, INFERRED - " + "; ".join(parts)


def _print_hierarchy(hierarchy, organisation) -> None:
    """V1, with each node's related concepts from V2 beneath its children.

    This is Part 5 section 193's example shape - Parent, Child, Child, Related
    concept - drawn from stored edges only, every line labelled with its real
    relation type and whether a source stated it or RUDRA inferred it.
    """
    names = organisation.concept_names
    related: dict[str, list] = {}
    for edge in organisation.group("Related concepts"):
        a, b = edge.relationship.from_concept_id, edge.relationship.to_concept_id
        related.setdefault(a, []).append((b, edge))
        related.setdefault(b, []).append((a, edge))

    print("V1 Hierarchy - stored PARENT_OF / COMPOSED_OF / PART_OF / INSTANCE_OF edges only;")
    print("   nothing here is inferred. Related concepts (V2) are listed under each node.")
    if not hierarchy.roots:
        print("  No hierarchy edge is stored among this run's concepts.")
        return

    def show(node, prefix: str, last: bool, depth: int) -> None:
        name = node.concept.canonical_name
        if depth == 0:
            print(f"  {name}")
        else:
            branch = "`-- " if last else "|-- "
            print(f"  {prefix}{branch}{name}    [{_edge_label(node.via, names)}]")
        inner = prefix + ("" if depth == 0 else ("    " if last else "|   "))
        extras = sorted(
            related.get(node.concept.id, ()), key=lambda item: (names.get(item[0], ""), item[0])
        )
        entries = [("child", child) for child in node.children]
        entries += [("related", item) for item in extras]
        for index, (kind, item) in enumerate(entries):
            final = index == len(entries) - 1
            if kind == "child":
                show(item, inner, final, depth + 1)
            else:
                other, edge = item
                branch = "`-- " if final else "|-- "
                print(f"  {inner}{branch}{names.get(other, other)}    [{_edge_label(edge, names)}]")

    for root in hierarchy.roots:
        show(root, "", True, 0)
    for cycle in hierarchy.cycles:
        print("  Cycle reported (traversal stopped): " + " -> ".join(names.get(c, c) for c in cycle))


def _print_organisation(organisation) -> None:
    """V2: every group, empty or not, each edge labelled EXPLICIT or INFERRED."""
    names = organisation.concept_names
    print("V2 Organisation - EXPLICIT: a source states it. INFERRED: RUDRA inferred it,")
    print("   and the basis says from what. Phase 6 infers only related concepts.")
    for group, edges in organisation.groups:
        if not edges:
            print(f"  {group}: none stored among this run's concepts")
            continue
        print(f"  {group}: {len(edges)}")
        for edge in edges:
            relation = edge.relationship
            left = names.get(relation.from_concept_id, relation.from_concept_id)
            right = names.get(relation.to_concept_id, relation.to_concept_id)
            joiner = "--" if relation.relation_type == "RELATED_TO" else "->"
            print(f"    {left} {joiner} {right}")
            print(f"        {_edge_label(edge, names)}")


# ---------------------------------------------------------------- lookup (Phase 7)


def _cmd_lookup(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Read one concept and its provenance by exact id or exact name (ADR 0031).

        python -m app lookup <CPT-id>
        python -m app lookup --name "<name>"

    Read-only by construction (I7-B): the database is opened with
    `read_only=True` (SQLite `mode=ro` plus `query_only`), a lookup never migrates
    and never commits, and a database whose schema version differs from this
    build's is refused rather than migrated.

    An exact lookup, not a query engine (P7-1): identity, or equality of the
    D-30-normalised name - no search, ranking, traversal or natural language
    (Phases 9 and 13). Every concept answering to a name is shown and none is
    chosen (I7-D; Part 3 sections 73 and 97). No match is an answer, not a
    failure: it is stated, and the exit code is 3.
    """
    from app.knowledge import ConceptService
    from app.storage import (
        CODE_SCHEMA_VERSION,
        DatabaseRole,
        Repository,
        connect,
        database_path,
        schema_version,
    )

    _tolerate_unencodable_output()
    request = _lookup_request(args)
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        version = schema_version(connection)
        _require_readable_schema(version, CODE_SCHEMA_VERSION, path)
        repository = Repository(connection)
        service = ConceptService(repository)
        if request["by"] == "id":
            found = service.retrieve(request["value"])
            views = [] if found is None else [found]
        else:
            views = [
                view
                for view in (
                    service.retrieve(concept.id) for concept in service.resolve(request["value"])
                )
                if view is not None
            ]
        matches = [_lookup_match(repository, view) for view in views]
    finally:
        connection.close()

    result = {
        "lookup": request,
        "database": {"path": str(path), "read_only": True, "schema_version": version},
        "match_count": len(matches),
        "matches": matches,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_lookup(result)
    return ExitCode.OK if matches else ExitCode.MISSING_INFORMATION


def _lookup_request(args: argparse.Namespace) -> dict[str, str]:
    """Validate the lookup's input before anything is opened (I7-A)."""
    from app.models import EntityKind, is_valid_id, normalize_alias

    examples = (
        "python -m app lookup CPT-00000001",
        'python -m app lookup --name "Voltage"',
    )
    target, name = args.target, args.name
    if target is not None and name is not None:
        raise InvalidInputError.of(
            "A lookup takes a concept id or --name, not both.",
            f"Both were given: {target!r} and --name {name!r}. Exactly one form per call.",
            stage="cli.lookup",
            data_changed=False,
            retry_safe=True,
            next_options=examples,
        )
    if target is None and name is None:
        raise InvalidInputError.of(
            "Nothing to look up.",
            "A lookup needs an exact concept id, or --name with an exact concept name.",
            stage="cli.lookup",
            data_changed=False,
            retry_safe=True,
            next_options=examples,
        )
    if target is not None:
        if not is_valid_id(target, kind=EntityKind.CONCEPT):
            kind = "an identifier of another kind" if is_valid_id(target) else "not a RUDRA identifier"
            raise InvalidInputError.of(
                f"{target!r} is not a concept identifier.",
                f"A concept identifier looks like CPT-00000001; {target!r} is {kind}.",
                stage="cli.lookup",
                data_changed=False,
                retry_safe=True,
                next_options=examples,
            )
        return {"by": "id", "value": target}
    try:
        normalized = normalize_alias(name)
    except ValueError as exc:
        raise InvalidInputError.of(
            "The name to look up is empty.",
            "An exact-name lookup needs at least one character that is not whitespace.",
            stage="cli.lookup",
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=examples,
        ) from exc
    return {"by": "name", "value": name, "normalized": normalized}


def _require_readable_schema(version: int, expected: int, path: Path) -> None:
    """Refuse a database this build cannot read as it is (I7-B). Never migrate it."""
    if version == expected:
        return
    if version == 0:
        summary = "That file is not an initialised RUDRA knowledge database."
        options: tuple[str, ...] = ("Check that --project-root names the intended project.",)
    elif version < expected:
        summary = "The knowledge database is older than this build, and a lookup never migrates."
        options = (
            "Migrate it explicitly with: python -m app db  (this writes to the database).",
            "For the live database, first make a fresh byte-identical, read-back-verified "
            "off-SSD backup (D-15; ADR 0031, P7-8).",
        )
    else:
        summary = "The knowledge database is newer than this build."
        options = ("Use the RUDRA version that last migrated it.",)
    raise StorageError.of(
        summary,
        f"Its schema version is {version}; this build reads version {expected}.",
        stage="cli.lookup.schema",
        detail=str(path),
        data_changed=False,
        retry_safe=True,
        next_options=options,
    )


def _lookup_match(repository, view) -> dict:
    """One concept's stored data and provenance, as plain values (I7-C).

    Everything comes from existing retrieval: `ConceptService.retrieve` for the
    concept, the `evidence` view for provenance, and each INFERRED edge's recorded
    basis (ADR 0027). Nothing is inferred, ranked or traversed here. The preserved
    file is checked for presence only; its content is not re-hashed.
    """
    from app.models import Concept, Document, ExtractionRun, KnowledgeObject, SourceOccurrence
    from app.storage import queries, to_row

    connection = repository.connection
    run_ids: set[str] = set()
    document_ids: set[str] = set()

    def note(run_id: str | None, document_id: str | None) -> None:
        if run_id:
            run_ids.add(run_id)
        if document_id:
            document_ids.add(document_id)

    def evidence(subject_id: str) -> list[dict]:
        rows = [dict(row) for row in queries.evidence_for(connection, subject_id)]
        for row in rows:
            note(row.get("extraction_run_id"), row.get("document_id"))
        return rows

    def other_end(relation) -> dict:
        if relation.from_concept_id == view.concept.id:
            side, other_concept, other_knowledge = (
                "from", relation.to_concept_id, relation.to_knowledge_id,
            )
        else:
            side, other_concept, other_knowledge = (
                "to", relation.from_concept_id, relation.from_knowledge_id,
            )
        if other_concept is not None:
            entity = repository.get(Concept, other_concept)
            kind, identifier = "concept", other_concept
        else:
            entity = repository.get(KnowledgeObject, other_knowledge)
            kind, identifier = "knowledge_object", other_knowledge
        return {
            "this_concept_is": side,
            "kind": kind,
            "id": identifier,
            "name": None if entity is None else entity.canonical_name,
        }

    def bases(relation) -> list[dict]:
        found = []
        for inference in queries.inferences_for_relationship(connection, relation.id):
            occurrence = (
                repository.get(SourceOccurrence, inference.basis_occurrence_id)
                if inference.basis_occurrence_id
                else None
            )
            if occurrence is not None:
                note(occurrence.extraction_run_id, occurrence.document_id)
            found.append(
                {
                    "inference": to_row(inference),
                    "basis_occurrence": None if occurrence is None else to_row(occurrence),
                }
            )
        return found

    occurrences = evidence(view.concept.id)
    definitions = [
        {
            "knowledge": to_row(item.knowledge),
            "relationship": to_row(item.relationship),
            "evidence": evidence(item.knowledge.id),
        }
        for item in view.definitions
    ]
    equations = [
        {
            "equation": to_row(equation),
            "evidence": evidence(equation.knowledge_id) if equation.knowledge_id else [],
        }
        for equation in view.equations
    ]
    relationships = [
        {
            "relationship": to_row(relation),
            "other": other_end(relation),
            "evidence": evidence(relation.id),
            "bases": bases(relation),
        }
        for relation in view.relationships
    ]
    prerequisites = [
        {"relationship": to_row(relation), "other": other_end(relation)}
        for relation in view.prerequisites
    ]

    runs = []
    for run_id in sorted(run_ids):
        run = repository.get(ExtractionRun, run_id)
        if run is not None:
            runs.append(to_row(run))
            document_ids.add(run.document_id)
    documents = []
    for document_id in sorted(document_ids):
        document = repository.get(Document, document_id)
        if document is not None:
            documents.append(
                {
                    "document": to_row(document),
                    "preserved_file_present": Path(document.file_path).is_file(),
                }
            )

    return {
        "concept": to_row(view.concept),
        "aliases": [to_row(alias) for alias in view.aliases],
        "occurrences": occurrences,
        "definitions": definitions,
        "equations": equations,
        "relationships": relationships,
        "prerequisites": prerequisites,
        "runs": runs,
        "documents": documents,
    }


def _one_line(text: object) -> str:
    """Stored text on one line for the screen; the stored value is unchanged."""
    return " ".join(str(text).split())


def _print_lookup(result: dict) -> None:
    """The text form of a lookup: the same data as --json, arranged for reading."""
    request, database = result["lookup"], result["database"]
    if request["by"] == "id":
        print(f"Lookup     : concept id {request['value']}")
    else:
        print(
            f"Lookup     : exact name \"{request['value']}\" "
            f"(normalised \"{request['normalized']}\")"
        )
    print(f"Database   : {database['path']}")
    print(
        f"             opened read-only; schema version {database['schema_version']}; "
        "a lookup never migrates or writes"
    )
    count = result["match_count"]
    print(f"Matches    : {count}")
    if count == 0:
        absent = (
            "no concept has this id" if request["by"] == "id" else "no concept answers to this name"
        )
        print(f"             {absent}. Absence is the answer; nothing was guessed.")
        return
    if count > 1:
        print(f"             {count} concepts answer to this name; none is chosen.")
    for index, match in enumerate(result["matches"], start=1):
        print()
        print(f"--- Match {index} of {count} ---")
        _print_lookup_match(match)


def _print_evidence_row(row: dict, indent: str, label: str = "") -> None:
    page = "-" if row["page_number"] is None else row["page_number"]
    span = "-" if row["char_start"] is None else f"{row['char_start']}-{row['char_end']}"
    print(
        f"{indent}{label}{row['id']}  {row['document_id']} p.{page}"
        f"  segment {row['segment_id'] or '-'}  chars {span}  run {row['extraction_run_id'] or '-'}"
    )
    print(f"{indent}{' ' * len(label)}    \"{_one_line(row['evidence_text'] or '')}\"")


def _print_lookup_match(match: dict) -> None:
    concept = match["concept"]
    print(f"Concept    : {concept['id']}  \"{concept['canonical_name']}\"  [{concept['lifecycle_status']}]")
    if concept["context"]:
        print(f"             context: {_one_line(concept['context'])}")
    if concept["description"]:
        print(f"             description: {_one_line(concept['description'])}")

    print(f"Aliases    : {len(match['aliases'])}")
    for alias in match["aliases"]:
        print(
            f"  {alias['id']}  \"{alias['alias']}\"  normalised \"{alias['normalized_alias']}\""
            f"  [{alias['lifecycle_status']}]"
        )

    print(f"Occurrences: {len(match['occurrences'])}")
    for row in match["occurrences"]:
        _print_evidence_row(row, "  ")

    print(f"Definitions: {len(match['definitions'])}")
    for item in match["definitions"]:
        knowledge, relation = item["knowledge"], item["relationship"]
        print(
            f"  {knowledge['id']}  [{knowledge['knowledge_type']}, {knowledge['certainty']}, "
            f"{knowledge['lifecycle_status']}]  via {relation['id']} {relation['relation_type']} "
            f"{relation['origin']}"
        )
        print(f"      \"{_one_line(knowledge['statement'])}\"")
        for row in item["evidence"]:
            _print_evidence_row(row, "      ", "evidence ")

    print(f"Equations  : {len(match['equations'])}")
    for item in match["equations"]:
        equation = item["equation"]
        print(f"  {equation['id']}  [{equation['lifecycle_status']}]  \"{_one_line(equation['expression'])}\"")
        for row in item["evidence"]:
            _print_evidence_row(row, "      ", "evidence ")

    print(f"Relationships: {len(match['relationships'])}")
    for item in match["relationships"]:
        relation, other = item["relationship"], item["other"]
        arrow = "->" if other["this_concept_is"] == "from" else "<-"
        print(
            f"  {relation['id']}  {relation['relation_type']} {relation['origin']} "
            f"[{relation['lifecycle_status']}]  {arrow} {other['kind']} {other['id']} \"{other['name']}\""
        )
        for row in item["evidence"]:
            _print_evidence_row(row, "      ", "stated ")
        for basis in item["bases"]:
            inference, occurrence = basis["inference"], basis["basis_occurrence"]
            where = (
                "basis occurrence not found"
                if occurrence is None
                else f"{occurrence['id']} {occurrence['document_id']} p.{occurrence['page_number']}"
                f" run {occurrence['extraction_run_id']}"
            )
            print(
                f"      basis  {inference['id']}  {inference['rule']} v{inference['rule_version']}: "
                f"{where}; matched \"{inference['matched_text']}\""
            )

    print(f"Prerequisites: {len(match['prerequisites'])}")
    for item in match["prerequisites"]:
        relation, other = item["relationship"], item["other"]
        print(
            f"  {relation['id']}  {relation['relation_type']} {relation['origin']}  "
            f"{other['kind']} {other['id']} \"{other['name']}\""
        )

    print(f"Runs       : {len(match['runs'])}")
    for run in match["runs"]:
        print(
            f"  {run['id']}  document {run['document_id']}  run {run['run_number']}  "
            f"{run['trigger']}  extractor v{run['extractor_version']}  status {run['status']}"
        )

    print(f"Documents  : {len(match['documents'])}")
    for item in match["documents"]:
        document = item["document"]
        print(f"  {document['id']}  \"{document['original_filename']}\"")
        print(f"      sha256 {document['file_hash']}")
        print(f"      stored at {document['file_path']}")
        print(f"      preserved file present: {'yes' if item['preserved_file_present'] else 'NO'}")


# ------------------------------------------------------------ review (Phase 8)


def _cmd_review(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Read one knowledge object's conflicts, assessments and sources (ADR 0034, P8-26).

        python -m app review <K-id>

    Read-only by construction, as `lookup` is (I7-B): opened with `read_only=True`,
    never migrated, never committed, and a database at another schema version is
    refused. Exact, by identifier: no listing, search, ranking or traversal. A
    non-`K` identifier is refused before any database is opened.

    Exit codes follow Phase 7: 0 the object exists (also when nothing names it - that
    is stated); 2 not a knowledge-object identifier; 3 no such object; 5 the database
    cannot be read as it is; 70 anything unexpected.
    """
    from app.deduplication import review_knowledge
    from app.storage import (
        CODE_SCHEMA_VERSION,
        DatabaseRole,
        Repository,
        connect,
        database_path,
        schema_version,
    )

    _tolerate_unencodable_output()
    knowledge_id = _review_request(args)
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        version = schema_version(connection)
        _require_reviewable_schema(version, CODE_SCHEMA_VERSION, path)
        review = review_knowledge(Repository(connection), knowledge_id)
    finally:
        connection.close()

    result: dict = {
        "review": {"knowledge_id": knowledge_id},
        "database": {"path": str(path), "read_only": True, "schema_version": version},
        "found": review is not None,
    }
    if review is not None:
        result.update(_review_payload(review))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_review(result)
    return ExitCode.OK if review is not None else ExitCode.MISSING_INFORMATION


def _review_request(args: argparse.Namespace) -> str:
    """Validate the review's input before anything is opened."""
    from app.models import EntityKind, is_valid_id

    example = ("python -m app review K-00000001",)
    target = args.target
    if target is None:
        raise InvalidInputError.of(
            "Nothing to review.",
            "A review needs an exact knowledge-object identifier.",
            stage="cli.review",
            data_changed=False,
            retry_safe=True,
            next_options=example,
        )
    if not is_valid_id(target, kind=EntityKind.KNOWLEDGE_OBJECT):
        kind = "an identifier of another kind" if is_valid_id(target) else "not a RUDRA identifier"
        raise InvalidInputError.of(
            f"{target!r} is not a knowledge-object identifier.",
            f"A knowledge-object identifier looks like K-00000001; {target!r} is {kind}.",
            stage="cli.review",
            data_changed=False,
            retry_safe=True,
            next_options=example,
        )
    return target


def _require_reviewable_schema(version: int, expected: int, path: Path) -> None:
    """Refuse a database this build cannot read as it is. Never migrate it."""
    if version == expected:
        return
    if version == 0:
        summary = "That file is not an initialised RUDRA knowledge database."
        options: tuple[str, ...] = ("Check that --project-root names the intended project.",)
    elif version < expected:
        summary = "The knowledge database is older than this build, and a review never migrates."
        options = (
            "Migrate it explicitly with: python -m app db  (this writes to the database).",
            "For the live database, first make a fresh byte-identical, read-back-verified "
            "off-SSD backup (D-15; ADR 0032, P8-9).",
        )
    else:
        summary = "The knowledge database is newer than this build."
        options = ("Use the RUDRA version that last migrated it.",)
    raise StorageError.of(
        summary,
        f"Its schema version is {version}; this build reads version {expected}.",
        stage="cli.review.schema",
        detail=str(path),
        data_changed=False,
        retry_safe=True,
        next_options=options,
    )


def _review_payload(review) -> dict:
    """One review as plain values: the same data for --json and for text."""
    from app.storage import to_row

    def row(entity) -> dict | None:
        return None if entity is None else to_row(entity)

    return {
        "knowledge": to_row(review.knowledge),
        "evidence": [dict(item) for item in review.evidence],
        "sources": {
            "number_of_sources": review.number_of_sources,
            "source_occurrences": review.source_occurrences,
            "superseded_into": list(review.superseded_into),
        },
        "conflicts": [
            {
                "conflict": to_row(item.conflict),
                "this_claim": item.this_claim,
                "other": row(item.other),
                "other_evidence": [dict(e) for e in item.other_evidence],
            }
            for item in review.conflicts
        ],
        "assessments": [
            {
                "record": to_row(item.record),
                "this_side": item.this_side,
                "merge_pointer": item.is_merge_pointer,
                "other": row(item.other),
                "linked_occurrence": row(item.linked_occurrence),
                "other_evidence": [dict(e) for e in item.other_evidence],
            }
            for item in review.assessments
        ],
    }


def _print_review(result: dict) -> None:
    database = result["database"]
    print(f"Review     : knowledge object {result['review']['knowledge_id']}")
    print(f"Database   : {database['path']}")
    print(f"             opened read-only; schema version {database['schema_version']}; "
          "a review never migrates or writes")
    if not result["found"]:
        print("Found      : no knowledge object has this id. Absence is the answer; "
              "nothing was guessed.")
        return
    knowledge = result["knowledge"]
    print(f"Object     : {knowledge['id']}  [{knowledge['knowledge_type']}, "
          f"{knowledge['certainty']}, {knowledge['lifecycle_status']}]")
    print(f"             \"{_one_line(knowledge['statement'])}\"")
    pointers = [a for a in result["assessments"] if a["merge_pointer"] and a["this_side"] == "other"]
    for item in pointers:
        print(f"             superseded by merge into {item['record']['canonical_knowledge_id']} "
              f"(pointer {item['record']['id']}); its evidence is kept here unchanged")
    print(f"Evidence   : {len(result['evidence'])}")
    for row in result["evidence"]:
        _print_evidence_row(row, "  ")
    sources = result["sources"]
    print(f"Sources    : number_of_sources {sources['number_of_sources']} (distinct sources) "
          f"across {sources['source_occurrences']} source occurrences")
    if sources["superseded_into"]:
        print("             including the occurrences of "
              + ", ".join(sources["superseded_into"]) + ", superseded into it by merge")
    print("             informational only: more sources is not more correct (section 78)")

    print(f"Conflicts  : {len(result['conflicts'])}")
    for item in result["conflicts"]:
        conflict, other = item["conflict"], item["other"]
        print(f"  {conflict['id']}  cause {conflict['cause']}  [{conflict['lifecycle_status']}]  "
              f"this object is claim {item['this_claim']}")
        if other is not None:
            print(f"      other claim {other['id']}  [{other['knowledge_type']}, "
                  f"{other['lifecycle_status']}]  \"{_one_line(other['statement'])}\"")
        for row in item["other_evidence"]:
            _print_evidence_row(row, "      ", "evidence ")

    print(f"Assessments: {len(result['assessments'])}")
    for item in result["assessments"]:
        record = item["record"]
        run = record["extraction_run_id"] or "no run (merge)"
        print(f"  {record['id']}  {record['outcome']}  rule {record['rule']} "
              f"v{record['rule_version']}  {run}  this object is the {item['this_side']} side")
        other = item["other"]
        if other is not None:
            print(f"      other object {other['id']}  [{other['lifecycle_status']}]  "
                  f"\"{_one_line(other['statement'])}\"")
        occurrence = item["linked_occurrence"]
        if occurrence is not None:
            print(f"      linked occurrence {occurrence['id']}  {occurrence['document_id']} "
                  f"p.{occurrence['page_number']}  run {occurrence['extraction_run_id']}")
        if record["conflict_id"]:
            print(f"      conflict {record['conflict_id']}")
        for row in item["other_evidence"]:
            _print_evidence_row(row, "      ", "evidence ")
    if not result["conflicts"] and not result["assessments"]:
        print("Nothing to review: no conflict and no equivalence assessment names this object.")


# ----------------------------------------------------------- edition (Phase 8)


def _cmd_edition(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Record that two ingested documents are editions of one work (ADR 0034, P8-27).

        python -m app edition <DOC-id> --work <DOC-id> --label "<label>" [--work-label "<label>"]

    The positional document is the edition being declared; `--work` names the
    document it is filed under. Both rows go under the work; the work's own row is
    written the first time it is named, so `--work-label` is needed then. It writes,
    so it opens and migrates the database as the other writing commands do. The
    validation and the writes live in `app.deduplication.editions`.
    """
    from app.deduplication import declare_edition
    from app.models import EntityKind, is_valid_id
    from app.storage import DatabaseRole, Repository, connect, database_path, migrate

    example = ('python -m app edition DOC-00000002 --work DOC-00000001 --label "2nd edition" '
               '--work-label "1st edition"',)
    for option, value in (("the edition", args.target), ("--work", args.work)):
        if not is_valid_id(value, kind=EntityKind.DOCUMENT):
            raise InvalidInputError.of(
                f"{option} must be a document identifier; nothing was written.",
                f"A document identifier looks like DOC-00000001; got {value!r}.",
                stage="cli.edition",
                data_changed=False,
                retry_safe=True,
                next_options=example,
            )
    if args.label is None:
        raise InvalidInputError.of(
            "An edition needs --label; nothing was written.",
            "The label is the user's name for the edition being declared.",
            stage="cli.edition",
            missing=("--label",),
            data_changed=False,
            retry_safe=True,
            next_options=example,
        )

    _tolerate_unencodable_output()
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE)
    try:
        migration = migrate(connection, database_path=path)
        connection.commit()
        try:
            declaration = declare_edition(
                Repository(connection),
                edition_id=args.target,
                work_id=args.work,
                label=args.label,
                work_label=args.work_label,
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()

    if migration.applied:
        print(f"Database   : migrated {migration.version_before} -> {migration.version_after}"
              + (f"; backup {migration.backup_path}" if migration.backup_path else ""))
    work, edition = declaration.work, declaration.edition
    print(f"Work       : {work.id}  \"{work.original_filename}\"")
    print(f"Edition    : {edition.id}  \"{edition.original_filename}\"")
    print(f"Declared   : {len(declaration.written)} document_version rows written (a user declaration)")
    for row in declaration.written:
        print(f"  {row.id}  \"{row.version_label}\"  sha256 {row.file_hash}  ingested {row.ingested_at}")
    print(f"Editions   : {len(declaration.rows)} recorded under {work.id}")
    for row in declaration.rows:
        print(f"  {row.id}  \"{row.version_label}\"")
    print("Unchanged  : no document, segment, occurrence or knowledge row was changed.")
    print("             Knowledge in the editions is compared by stage 15 at extraction.")
    return ExitCode.OK


# ------------------------------------------------------------- merge (Phase 8)


def _cmd_merge(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Supersede in place the exact duplicates stored before Phase 8 (ADR 0034, P8-30).

        python -m app merge

    Explicit only: `extract` and `db` never merge. It takes no target - the groups
    are formed over all ACTIVE knowledge by P8-12's rule. It writes, so it opens and
    migrates the database as the other writing commands do; one merge is one
    transaction. The mechanism lives in `app.deduplication.merge` (ADR 0033 P8-19).
    """
    from app.deduplication import Merger
    from app.storage import DatabaseRole, Repository, connect, database_path, migrate

    if args.target is not None:
        raise InvalidInputError.of(
            "merge takes no target; nothing was merged.",
            f"Got {args.target!r}. A merge covers every ACTIVE exact-duplicate group.",
            stage="cli.merge",
            data_changed=False,
            retry_safe=True,
            next_options=("python -m app merge",),
        )
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE)
    try:
        migration = migrate(connection, database_path=path)
        connection.commit()
        report = Merger(Repository(connection)).merge()
    finally:
        connection.close()

    if args.json:
        print(json.dumps(
            {
                "migrations_applied_now": list(migration.applied),
                "backup": str(migration.backup_path) if migration.backup_path else None,
                "groups": [list(group) for group in report.groups],
                "superseded": report.superseded,
                "companions_superseded": report.companions_superseded,
                "pointer_records": report.pointers,
                "assessments": report.assessments,
                "conflicts": list(report.conflicts),
                "concept_records": report.concept_records,
                "changed": report.changed,
            },
            indent=2,
            sort_keys=True,
        ))
        return ExitCode.OK
    if migration.applied:
        print(f"Database   : migrated {migration.version_before} -> {migration.version_after}"
              + (f"; backup {migration.backup_path}" if migration.backup_path else ""))
    print("Merge      : exact duplicates stored before stage 15 (ADR 0033, P8-19)")
    print(f"Groups     : {len(report.groups)} exact-duplicate groups among ACTIVE knowledge")
    for group in report.groups[:20]:
        print(f"  {group[0]} canonical (smallest counter) <- " + ", ".join(group[1:]))
    if len(report.groups) > 20:
        print(f"  ... and {len(report.groups) - 20} more")
    print(f"Superseded : {report.superseded} knowledge objects and {report.companions_superseded} "
          "companion rows -> SUPERSEDED")
    print(f"Pointers   : {report.pointers} EXACT_DUPLICATE records with no run")
    outcomes = ", ".join(f"{k} {v}" for k, v in report.assessments.items()) or "none"
    print(f"Compared   : {outcomes} (canonical members only, no run)")
    print(f"Conflicts  : {len(report.conflicts)} created by rule C1"
          + (": " + ", ".join(report.conflicts) if report.conflicts else ""))
    print(f"Concepts   : {report.concept_records} POSSIBLE_EQUIVALENT concept records")
    print("Unchanged  : no source occurrence, relationship, relationship occurrence or")
    print("             inference basis was moved, deleted or rewritten; no extraction issue.")
    if not report.changed:
        print("Nothing to merge: no ACTIVE exact duplicates and no unrecorded pair; nothing was written.")
    return ExitCode.OK


_QUERY_EXAMPLES = (
    'python -m app query --name "MOSFET"',
    "python -m app query K-00000001",
    "python -m app query --document DOC-00000001 --page 3",
    'python -m app query --keyword "gate-source voltage" [--prefix]',
)


def _cmd_query(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Structured retrieval through the Phase 9 query engine (ADRs 0035-0037, P9-31).

        python -m app query --name "<name>"          concept mode
        python -m app query <CPT/K/REL/DOC-id>        exact mode
        python -m app query --document <DOC-id> --page <n>
        python -m app query --keyword "<term>" [--prefix]

    Read-only by construction, as `lookup` is (I7-B): `knowledge.db` is opened with
    `read_only=True`, never migrated, and a schema mismatch is refused. Keyword mode
    reads `data/indexes/index.db` only when it is verified fresh; a query never builds
    or repairs the index. The engine holds every rule; this only presents its result,
    and `--json` prints the engine's canonical JSON.

    Exit codes (Phase 7, P9-24): 0 an answer was found; 2 invalid input; 3 nothing
    found, or insufficient authorized information (sections 67, 228, 230); 5 the
    database or the index cannot be used as it is; 70 anything unexpected.
    """
    from app.query import AnswerStatus, QueryEngine, to_json
    from app.storage import DatabaseRole, connect, database_path

    _tolerate_unencodable_output()
    request = _query_request(args)
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    index = database_path(started.context.paths, DatabaseRole.INDEX)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        result = QueryEngine(connection, database_path=path, index_path=index).run(request)
    finally:
        connection.close()
    if args.json:
        print(to_json(result))
    else:
        _print_query(result)
    return ExitCode.OK if result.status is AnswerStatus.FOUND else ExitCode.MISSING_INFORMATION


def _query_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.query", data_changed=False, retry_safe=True,
        next_options=_QUERY_EXAMPLES,
    )


def _query_request(args: argparse.Namespace):
    """Build and check the structured request before anything is opened."""
    from app.models.enums import (
        CertaintyState,
        ExtractionRunStatus,
        KnowledgeType,
        LifecycleStatus,
        RelationshipOrigin,
        RelationType,
        SourceCategory,
    )
    from app.query import QueryFilters, QueryRequest, SourceScope

    modes = [
        name
        for name, given in (
            ("--name", args.name is not None),
            ("an identifier", args.target is not None),
            ("--document/--page", args.document is not None or args.page is not None),
            ("--keyword", args.keyword is not None),
        )
        if given
    ]
    if len(modes) != 1:
        raise _query_refusal(
            "A query takes exactly one mode.",
            "Give one of: --name, an identifier, --document with --page, or --keyword. "
            f"Given: {', '.join(modes) or 'none'}.",
        )

    def choose(value: str, kind, flag: str):
        key = value.strip().upper().replace("-", "_")
        try:
            return kind(key)
        except ValueError:
            allowed = ", ".join(member.value for member in kind)
            raise _query_refusal(f"{value!r} is not a value {flag} accepts.", f"Accepted: {allowed}.") from None

    def many(values, kind, flag: str) -> tuple:
        return tuple(choose(v, kind, flag) for v in values or ())

    def pages(values, flag: str) -> tuple[int, ...]:
        found = []
        for value in values or ():
            try:
                found.append(int(value))
            except ValueError:
                raise _query_refusal(f"{value!r} is not a page number.", f"{flag} takes a whole number.") from None
        return tuple(found)

    filters = QueryFilters(
        knowledge_types=many(args.knowledge_type, KnowledgeType, "--knowledge-type"),
        lifecycle_statuses=many(args.lifecycle, LifecycleStatus, "--lifecycle"),
        certainties=many(args.certainty, CertaintyState, "--certainty"),
        relation_types=many(args.relation_type, RelationType, "--relation-type"),
        origins=many(args.origin, RelationshipOrigin, "--origin"),
        document_ids=tuple(args.in_document or ()),
        run_ids=tuple(args.in_run or ()),
        run_statuses=many(args.run_status, ExtractionRunStatus, "--run-status"),
        extractor_versions=tuple(args.extractor_version or ()),
        source_categories=many(args.source_category, SourceCategory, "--source-category"),
        pages=pages(args.on_page, "--on-page"),
    )
    options = {
        "scope": choose(args.scope, SourceScope, "--scope"),
        "filters": filters,
        "include_superseded": not args.exclude_superseded,
        "widen": not args.no_widen,
    }
    if args.keyword is not None:
        request = QueryRequest.keyword(args.keyword, prefix=args.prefix, **options)
    elif args.name is not None:
        request = QueryRequest.concept(args.name, prefix=args.prefix, **options)
    elif args.target is not None:
        request = QueryRequest.exact(args.target, prefix=args.prefix, **options)
    else:
        if args.document is None or args.page is None:
            raise _query_refusal("A page query needs both --document and --page.", "One of them is missing.")
        (page,) = pages([args.page], "--page")
        request = QueryRequest.page(args.document, page, prefix=args.prefix, **options)
    return request.check()


def _evidence_line(row) -> str:
    where = f"p.{row.page_number}" if row.page_number is not None else "no page"
    if row.char_start is not None:
        where += f" [{row.char_start}-{row.char_end}]"
    run = row.extraction_run_id or "no run"
    return f"{row.id} {row.document_id} {where}, source {row.source_id}, {run}"


def _print_edge(edge, indent: str) -> None:
    relation = edge.relationship
    ends = f"{relation.from_id} --{relation.relation_type.value}--> {relation.to_id}"
    print(f"{indent}{relation.id} {ends} [{relation.lifecycle_status.value}] {edge.label}")
    for row in edge.evidence:
        print(f"{indent}  stated: {_evidence_line(row)}")
    for basis in edge.bases:
        inference = basis.inference
        at = _evidence_line(basis.occurrence) if basis.occurrence is not None else "no occurrence recorded"
        print(f"{indent}  basis {inference.id} rule {inference.rule} v{inference.rule_version}: {at}")


def _print_item(item, indent: str) -> None:
    from app.query.results import KnowledgeItem

    if isinstance(item, KnowledgeItem):
        knowledge = item.knowledge
        print(f"{indent}{knowledge.id} {knowledge.knowledge_type.value} "
              f"[{knowledge.lifecycle_status.value}, {knowledge.certainty.value}] "
              f"\"{_one_line(knowledge.statement)}\"")
        if item.superseded_by:
            print(f"{indent}  SUPERSEDED - stored pointer to canonical {item.superseded_by}")
        if item.superseded_into:
            print(f"{indent}  superseded into it: {', '.join(item.superseded_into)}")
        print(f"{indent}  sources: {item.number_of_sources} (informational, section 78), "
              f"{item.source_occurrences} occurrence(s)")
        for row in item.evidence:
            print(f"{indent}  evidence: {_evidence_line(row)}")
        for record in item.assessments:
            print(f"{indent}  assessment {record.id}: {record.outcome.value} "
                  f"({record.canonical_knowledge_id} / {record.other_knowledge_id or record.linked_occurrence_id})")
    else:
        names = ", ".join(f"{c.id} \"{_one_line(c.canonical_name)}\"" for c in item.neighbours)
        print(f"{indent}{item.edge.relationship.relation_type.value} with {names or 'resolved concepts only'} "
              "(named, not expanded)")
    for link in item.links:
        print(f"{indent}  reached from {link.concept_id} (its {link.concept_end.value} end):")
        _print_edge(link.edge, indent + "    ")


def _print_provenance(provenance, indent: str) -> None:
    for source in provenance.sources:
        print(f"{indent}source {source.id} \"{_one_line(source.name)}\": {source.source_category.value}, "
              f"{source.authorization.value}, stored availability {source.availability.value}")
    for item in provenance.documents:
        document = item.document
        editions = ", ".join(f"{v.version_label} ({v.id})" for v in item.editions) or "no edition declared"
        print(f"{indent}document {document.id} {document.filename}: preserved file present "
              f"{'yes' if item.preserved_file_present else 'no'}; {editions}")
    for run in provenance.runs:
        print(f"{indent}run {run.id} #{run.run_number} of {run.document_id}: {run.status.value}, "
              f"extractor v{run.extractor_version}")


def _print_section(section, indent: str) -> None:
    for resolved in section.concepts:
        concept = resolved.concept
        alias = f" via alias \"{resolved.matched_alias.alias}\"" if resolved.matched_alias else ""
        print(f"{indent}Concept {concept.id} \"{_one_line(concept.canonical_name)}\"{alias}: "
              f"{len(resolved.occurrences)} occurrence(s)")
        for row in resolved.occurrences:
            print(f"{indent}  occurrence: {_evidence_line(row)}")
        for label, found in (("ancestors", resolved.ancestors), ("descendants", resolved.descendants)):
            if found:
                print(f"{indent}  {label} (PARENT_OF): " + ", ".join(f"{c.id} \"{_one_line(c.canonical_name)}\"" for c in found))
        for record in resolved.equivalence_records:
            print(f"{indent}  equivalence record {record.id}: {record.status.value} "
                  f"{record.concept_a_id}/{record.concept_b_id} ({record.basis.value})")
    for group in section.groups:
        print(f"{indent}[{group.name}] {len(group.items)}")
        if group.note:
            print(f"{indent}  {group.note}")
        for item in group.items:
            _print_item(item, indent + "  ")
    for conflict in section.conflicts:
        print(f"{indent}Conflict {conflict.conflict.id}: cause {conflict.conflict.cause.value}; "
              f"resolution: {conflict.resolution}")
        for side, claim in (("A", conflict.claim_a), ("B", conflict.claim_b)):
            if claim.knowledge is None:
                print(f"{indent}  claim {side} {claim.knowledge_id}: withheld (no evidence in scope)")
            else:
                print(f"{indent}  claim {side} {claim.knowledge_id}: \"{_one_line(claim.knowledge.statement)}\"")
                for row in claim.evidence:
                    print(f"{indent}    evidence: {_evidence_line(row)}")
    _print_provenance(section.provenance, indent)


def _print_query(result) -> None:
    """The text form of a query: the facts the JSON holds, arranged for reading."""
    from app.query.results import Provenance

    request, trace = result.request, result.trace
    asked = {
        "CONCEPT": f"concept named \"{request.name}\"",
        "EXACT": f"exact {request.identifier}",
        "PAGE": f"page {request.page_number} of {request.document_id}",
        "KEYWORD": f"keyword \"{request.term}\"" + (" as a prefix" if request.prefix else ""),
    }[request.mode.value]
    print(f"Query      : {asked}; scope {request.scope.value}"
          + (f"; filters: {', '.join(request.filters.active)}" if request.filters.active else ""))
    print(f"Database   : {trace.database.path}")
    print(f"             read-only {'yes' if trace.database.read_only else 'no'}; schema version "
          f"{trace.database.schema_version}; a query never writes or migrates")
    print(f"Index      : {trace.index}")
    print(f"Answer     : {result.status.value} - {result.message}")
    if result.concept is not None:
        _print_section(result.concept, "  ")
    for possible in result.possible_equivalents:
        print(f"Possible   : {possible.concept.id} \"{_one_line(possible.concept.canonical_name)}\" - {possible.label}")
        print(f"             of {', '.join(possible.of_concept_ids)}")
        for record in possible.records:
            print(f"             record {record.id}: {record.status.value} ({record.basis.value})")
        for edge in possible.stated_edges:
            _print_edge(edge, "             ")
        if possible.already_resolved:
            print("             answers to the requested name itself: its knowledge is shown above")
        elif possible.section is not None:
            _print_section(possible.section, "    ")
    if result.equivalence_note:
        print(f"Equivalence: {result.equivalence_note}")
    exact = result.exact
    if exact is not None:
        print(f"Exact      : {exact.identifier} ({exact.kind})")
        if exact.knowledge is not None:
            _print_item(exact.knowledge, "  ")
        if exact.relationship is not None:
            _print_edge(exact.relationship, "  ")
            for end in exact.endpoints:
                print(f"  end {end.id} \"{_one_line(end.canonical_name)}\"")
        if exact.document is not None:
            _print_provenance(
                Provenance(sources=exact.sources, documents=(exact.document,), runs=exact.runs), "  "
            )
        for conflict in exact.conflicts:
            print(f"  conflict {conflict.conflict.id}: {conflict.claim_a.knowledge_id} / "
                  f"{conflict.claim_b.knowledge_id} - {conflict.resolution}")
        _print_provenance(exact.provenance, "  ")
    page = result.page
    if page is not None:
        for segment in page.segments:
            print(f"Segment    : {segment.id} (ordinal {segment.ordinal}, {segment.text_origin.value})")
            print(f"  {_one_line(segment.text)}")
        for occurrence in page.occurrences:
            print(f"  {occurrence.evidence.subject_kind} {occurrence.evidence.subject_id}: "
                  f"\"{_one_line(occurrence.evidence.evidence_text)}\" - {_evidence_line(occurrence.evidence)}")
        _print_provenance(page.provenance, "  ")
    keyword = result.keyword
    if keyword is not None:
        print(f"Searched   : {keyword.expression}")
        for hit in keyword.knowledge:
            print(f"  {hit.knowledge.id} {hit.knowledge.knowledge_type.value} "
                  f"[{hit.knowledge.lifecycle_status.value}] \"{_one_line(hit.knowledge.statement)}\"")
            print(f"    {hit.label}")
            for row in hit.evidence:
                print(f"    evidence: {_evidence_line(row)}")
        for hit in keyword.concepts:
            print(f"  concept {hit.concept.id} \"{_one_line(hit.concept.canonical_name)}\" - alias "
                  + ", ".join(f"\"{a.alias}\"" for a in hit.aliases) + " matches")
            print(f"    {hit.label}")
            for row in hit.occurrences:
                print(f"    occurrence: {_evidence_line(row)}")
        for hit in keyword.pages:
            print(f"  page {hit.segment.page_number} of {hit.segment.document_id} ({hit.segment.id})")
        _print_provenance(keyword.provenance, "  ")
    w = result.withheld
    print(f"Withheld   : evidence - {w.unauthorized_evidence} not authorised, {w.out_of_scope_evidence} "
          f"out of scope, {w.filtered_evidence} filtered; items - {w.items_without_authorized_evidence} "
          f"without authorised evidence, {w.items_without_evidence} without evidence, "
          f"{w.items_filtered_out} filtered out")
    for note in result.notes:
        print(f"Note       : {note}")
    print(f"Trace      : {len(trace.resolved)} resolved concept(s), {len(trace.links)} link(s), "
          f"{len(trace.evidence_ids)} evidence row(s); returned with the answer, never stored")


def _cmd_index(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Build or rebuild the derived keyword index (ADR 0037 P9-26 ... P9-28, P9-31).

        python -m app index

    The only Phase 9 writer, and it writes only `data/indexes/index.db`: `knowledge.db`
    is opened read-only. The build, its section 158 estimate, its read-back
    verification and the atomic replacement all live in `app.query.index`; a failed
    build leaves the previous index as it was. It also reports which documents are
    fully ingested (ADR 0035 P9-10) - reported, never stored.

    On the live project this is a live operation (P9-8) and is run only when the user
    authorises it.
    """
    from app.query import build_index, to_json
    from app.query.index import document_ingestion
    from app.storage import DatabaseRole, connect, database_path

    if args.target is not None:
        raise InvalidInputError.of(
            "index takes no target; nothing was built.",
            f"Got {args.target!r}. The index always covers the whole knowledge database.",
            stage="cli.index",
            data_changed=False,
            retry_safe=True,
            next_options=("python -m app index",),
        )
    _tolerate_unencodable_output()
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    index = database_path(started.context.paths, DatabaseRole.INDEX)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        report = build_index(connection, index)
        documents = document_ingestion(connection, report.status)
    finally:
        connection.close()

    if args.json:
        print(to_json({
            "knowledge_database": {"path": str(path), "read_only": True},
            "index": report,
            "documents": documents,
        }))
        return ExitCode.OK
    status = report.status
    entries = ", ".join(f"{kind} {count}" for kind, count in status.entries)
    print(f"Index      : {report.path}")
    print(f"Built from : {path} (opened read-only; not modified)")
    print(f"Verified   : {status.state.value} - {status.reason}")
    print(f"Marker     : {status.build_marker}")
    print(f"Entries    : {sum(count for _, count in status.entries)} ({entries}); pages {status.pages}; "
          f"{report.skipped_empty} stored row(s) with nothing to index")
    print(f"Tokenizer  : {status.tokenizer}; D-30 normalisation version {status.normalization_version}; "
          f"index format {status.format_version}")
    print(f"Space      : estimated {report.estimate_bytes} bytes, {report.available_bytes} free; "
          f"written {report.size_bytes} bytes")
    print(f"Duration   : {report.seconds:.3f} s")
    for item in documents:
        runs = ", ".join(item.qualifying_runs) or "none"
        print(f"Document   : {item.document_id} {item.filename}: {item.reason} "
              f"(processing {item.processing_status}; COMPLETED v4+ runs: {runs})")
    return ExitCode.OK


def _cmd_reason(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Dependency-based reasoning through the Phase 10 engine (ADRs 0038-0040, P10-28).

        python -m app reason "<target>" --input "<node>[=<value>]" ...
        python -m app reason "<target>" --admit "<node>=K-<id>" --assume "<node>"
        python -m app reason --forward --input "<node>" ...

    A structured request only (section 4; I-4): a target or --forward, and what the
    request makes available - inputs, admitted stored items and assumptions (OI-1 =
    Option B). Read-only by construction, as `lookup` and `query` are: `knowledge.db`
    is opened with `read_only=True`, never migrated, and a schema mismatch is refused;
    the derived index is never opened. Nothing is written - derivations are returned
    only (U5(a)). The engine holds every rule; this only presents its result, and
    `--json` prints the engine's canonical JSON.

    Exit codes (ADR 0040 P10-28; Phase 7, P9-24): 0 answered - determined, or "cannot
    determine" (section 230); 2 invalid request; 3 no concept answers to the target, or
    insufficient authorized information; 5 the database cannot be used as it is; 70
    anything unexpected.
    """
    from app.reasoning import AnswerStatus, ReasoningEngine, to_json
    from app.storage import DatabaseRole, Repository, connect, database_path

    _tolerate_unencodable_output()
    request = _reason_request(args)
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        result = ReasoningEngine(Repository(connection)).reason(request)
    finally:
        connection.close()
    if args.json:
        print(to_json(result))
    else:
        _print_reasoning(result)
    answered = (AnswerStatus.DETERMINED, AnswerStatus.CANNOT_DETERMINE)
    return ExitCode.OK if result.status in answered else ExitCode.MISSING_INFORMATION


_REASON_EXAMPLES = (
    'python -m app reason "X" --input "E"',
    'python -m app reason "X" --input "E" --input "D" --input "B"',
    'python -m app reason "X" --input "E" --admit "D=K-00000001" --assume "B"',
    'python -m app reason --forward --input "E"',
)

#: Flags of other commands `reason` refuses rather than ignores: a filter or option it
#: silently ignored could be read as applied.
_NOT_REASON_FLAGS = (
    ("--name", "name"), ("--keyword", "keyword"), ("--prefix", "prefix"),
    ("--document", "document"), ("--page", "page"), ("--knowledge-type", "knowledge_type"),
    ("--lifecycle", "lifecycle"), ("--certainty", "certainty"),
    ("--relation-type", "relation_type"), ("--origin", "origin"),
    ("--in-document", "in_document"), ("--in-run", "in_run"), ("--run-status", "run_status"),
    ("--extractor-version", "extractor_version"), ("--source-category", "source_category"),
    ("--on-page", "on_page"), ("--exclude-superseded", "exclude_superseded"),
    ("--no-widen", "no_widen"), ("--re-extract", "re_extract"), ("--run", "run"),
    ("--work", "work"), ("--label", "label"), ("--work-label", "work_label"),
    ("--formula", "formula"),  # Phase 11 compatibility, decision (a) at Step 0 (ADR 0043 P11-30)
    ("--answer", "answer"),  # Phase 12, the same rule (ADR 0044 P12-16)
    ("--param", "param"),  # Phase 14, the same rule (ADR 0046 P14-13)
    ("--execute", "execute"), ("--confirm", "confirm"), ("--dry-run", "dry_run"),  # Phase 15 (ADR 0047 P15-13)
    ("--manual", "manual"),  # Phase 17 (ADR 0049 P17-2): only extract takes it
    ("--site", "site"),  # Phase 18 (ADR 0050 P18-3): only research takes it
    ("--out", "out"),  # Phase 19 (ADR 0051 P19-8): only diagram takes it
    ("--audio", "audio"), ("--listen", "listen"), ("--say", "say"),  # Phase 20 (ADR 0052 P20-9): only voice
    ("--delete-file", "delete_file"),  # Phase 23 (ADR 0055): only source takes it
)


def _reason_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.reason", data_changed=False, retry_safe=True,
        next_options=_REASON_EXAMPLES,
    )


def _reason_request(args: argparse.Namespace):
    """Build and check the structured reasoning request before anything is opened."""
    from app.reasoning import Admission, Assumption, ReasoningRequest, ReasoningScope, UserInput

    given = [flag for flag, name in _NOT_REASON_FLAGS if getattr(args, name) not in (None, False)]
    if given:
        raise _reason_refusal(
            f"'reason' does not take {', '.join(given)}.",
            "A reasoning request takes a target or --forward, with --input, --admit, --assume "
            "and --scope; it filters nothing.",
        )
    if args.forward and args.target is not None:
        raise _reason_refusal("A --forward request takes no target.", f"It was given {args.target!r}.")
    if not args.forward and args.target is None:
        raise _reason_refusal("A reasoning request needs a target, or --forward.", "Neither was given.")

    def split(value: str, flag: str, *, required: bool) -> tuple[str, str | None]:
        node, separator, rest = value.partition("=")
        if required and not separator:
            raise _reason_refusal(
                f"{value!r} is not in the form NODE=K-ID.",
                f"{flag} names a node and one knowledge object, e.g. D=K-00000001.",
            )
        return node, (rest if separator else None)

    try:
        scope = ReasoningScope(args.scope.strip().upper().replace("-", "_"))
    except ValueError:
        raise _reason_refusal(
            f"{args.scope!r} is not a value --scope accepts.", "Accepted: my-books, authorized."
        ) from None
    options = {
        "inputs": tuple(UserInput(*split(v, "--input", required=False)) for v in args.input or ()),
        "admissions": tuple(Admission(*split(v, "--admit", required=True)) for v in args.admit or ()),
        "assumptions": tuple(
            Assumption(*split(v, "--assume", required=False)) for v in args.assume or ()
        ),
        "scope": scope,
    }
    if args.forward:
        return ReasoningRequest.forward(**options).check()
    return ReasoningRequest.of_target(args.target, **options).check()


def _print_reasoning(result) -> None:
    """The text form of a reasoning result: the facts the JSON holds, arranged for reading."""
    request, initial = result.request, result.initial
    asked = f'target "{request.target}"' if request.target is not None else "forward"
    print(f"Reasoning  : {asked}; scope {request.scope.value}; rule {result.rule} v{result.rule_version}")
    print(f"Database   : read-only {'yes' if result.read_only_connection else 'no'}; "
          "reasoning never writes or migrates")
    print(f"Answer     : {result.status.value} - {result.message}")
    for given in initial.inputs:
        value = "" if given.value is None else f" = {given.value}"
        print(f'Input      : {given.concept.id} "{_one_line(given.concept.canonical_name)}" USER_INPUT{value}')
    for item in initial.admitted:
        pointer = "" if item.superseded_by is None else f"; SUPERSEDED, pointer to {item.superseded_by}"
        print(f'Admitted   : {item.knowledge.id} for {item.concept.id} "{_one_line(item.concept.canonical_name)}"'
              f" ({item.knowledge.lifecycle_status.value}{pointer})")
        for row in item.evidence:
            print(f"             evidence {_evidence_line(row)}")
    for refused in initial.refused:
        print(f"Refused    : {refused.knowledge.id} for {refused.concept.id} - {refused.reason.value}")
    for stated in initial.assumptions:
        statement = "" if stated.statement is None else f": {stated.statement}"
        print(f'Assumption : {stated.concept.id} "{_one_line(stated.concept.canonical_name)}"{statement}')
    for method in result.methods:
        print(f'Method     : {method.target.id} "{_one_line(method.target.canonical_name)}" - {method.state.value}')
        _print_reasoning_part(method.nodes, method.steps, method.missing, method.cycles, "  ")
        for path in method.paths:
            print("  Path     : " + " -> ".join(f"{_one_line(n.name)} [{n.state.value}]" for n in path.nodes))
        if not method.paths_complete:
            print("  Paths    : the listing stopped at its limit")
    if result.forward is not None:
        forward = result.forward
        print(f"Forward    : from {', '.join(forward.seeds) or 'nothing in scope'}; "
              f"derived {', '.join(forward.derived) or 'nothing'}")
        _print_reasoning_part(forward.nodes, forward.steps, forward.missing, forward.cycles, "  ")
    withheld = result.withheld
    counts = [f"{name.replace('_', ' ')} {getattr(withheld, name)}"
              for name in ("unauthorized_evidence", "out_of_scope_evidence", "concepts",
                           "relationships", "knowledge", "excluded_by_lifecycle")
              if getattr(withheld, name)]
    print(f"Withheld   : {', '.join(counts) if counts else 'nothing'}")
    for note in result.notes:
        print(f"Note       : {note}")


def _print_reasoning_part(nodes, steps, missing, cycles, indent: str) -> None:
    for node in nodes:
        origins = f" ({', '.join(o.value for o in node.origins)})" if node.origins else ""
        print(f'{indent}Node     : {node.concept.id} "{_one_line(node.concept.canonical_name)}" '
              f"{node.state.value}{origins}")
        for link in node.requirements:
            relation = link.relationship
            print(f"{indent}  requires {link.required_id} by {relation.id} "
                  f"{relation.relation_type.value} {relation.origin.value}")
            for row in link.evidence:
                print(f"{indent}    evidence {_evidence_line(row)}")
        if node.withheld_requirements or node.excluded_requirements:
            print(f"{indent}  requirements not usable: {node.withheld_requirements} withheld, "
                  f"{node.excluded_requirements} excluded by lifecycle")
        for block in node.blocks:
            about = "" if block.concept_id is None else f" {block.concept_id} ({block.relationship_id})"
            print(f"{indent}  blocked  {block.reason.value}{about}")
        for item in node.conflicts:
            claims = []
            for claim in (item.claim_a, item.claim_b):
                shown = "withheld" if claim.knowledge is None else f'"{_one_line(claim.knowledge.statement)}"'
                claims.append(f"{claim.knowledge_id} {shown}")
            print(f"{indent}  conflict {item.conflict.id}: {' vs '.join(claims)}; cause "
                  f"{item.conflict.cause.value}; resolution {item.resolution}")
        for item in node.contradictions:
            print(f"{indent}  contradicts {item.relationship.id}")
        if node.conditional_on:
            print(f"{indent}  conditional on the assumption(s) {', '.join(node.conditional_on)}")
    for step in steps:
        conditional = f"; conditional on {', '.join(step.conditional_on)}" if step.conditional_on else ""
        print(f"{indent}Step {step.number:<4}: {step.concept_id} from {', '.join(step.requirements)} by "
              f"{step.rule} v{step.rule_version} [{', '.join(step.relationships)}]{conditional}")
    for gap in missing:
        needed = ", ".join(f"{by.concept_id} ({by.relationship_id})" for by in gap.required_by)
        said = f"required by {needed}" if needed else "- the target itself"
        print(f'{indent}Missing  : {gap.concept.id} "{_one_line(gap.concept.canonical_name)}" {said}')
    for cycle in cycles:
        print(f"{indent}Cycle    : {' -> '.join(cycle)}")


def _cmd_calculate(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Exact, unit-aware calculation through the Phase 11 engine (ADRs 0041-0043, P11-29).

        python -m app calculate I --formula "Rtotal = R1 + R2" --formula "I = V / Rtotal"
            --input "R1=10 Ω" --input "R2=20 Ω" --input "V=10 V"
        python -m app calculate I --formula "Rtotal = R1 + R2" --admit K-00000001 ...

    A structured request only (section 4; I-4). The request is checked before anything
    is opened. **Without an admission, knowledge.db is never opened.** With one, it is
    opened with `read_only=True`, never migrated, and a schema this build does not read
    is refused. The derived index is never opened. Nothing is written: results and the
    trace are returned only (N5 = (a)). The engine holds every rule; this only presents
    its result, and `--json` prints the engine's canonical JSON, the returned trace.

    Exit codes (P11-29): 0 answered - calculated, cannot determine, or conflicting
    (section 230); 2 invalid request; 3 insufficient authorized information (the
    admitted equation was withheld and the target cannot be determined); 5 the database
    cannot be used as it is (only with an admission); 70 anything unexpected.
    """
    from app.calculation import AnswerStatus, CalculationEngine, to_json
    from app.storage import DatabaseRole, Repository, connect, database_path

    _tolerate_unencodable_output()
    request = _calculate_request(args)
    if request.admissions:
        path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
        connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
        try:
            result = CalculationEngine(Repository(connection)).calculate(request)
        finally:
            connection.close()
    else:
        result = CalculationEngine().calculate(request)
    if args.json:
        print(to_json(result))
    else:
        _print_calculation(result)
    if result.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION:
        return ExitCode.MISSING_INFORMATION
    return ExitCode.OK


_CALCULATE_EXAMPLES = (
    'python -m app calculate I --formula "Rtotal = R1 + R2" --formula "I = V / Rtotal" '
    '--input "R1=10 Ω" --input "R2=20 Ω" --input "V=10 V"',
    'python -m app calculate I --formula "Rtotal = R1 + R2" --admit K-00000001 '
    '--input "R1=10 Ω" --input "R2=20 Ω" --input "V=10 V"',
)

#: Flags of other commands `calculate` refuses rather than ignores (P11-29): a filter or
#: option silently ignored could be read as applied.
_NOT_CALCULATE_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name != "formula"),
    ("--forward", "forward"),
)


def _calculate_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.calculate", data_changed=False, retry_safe=True,
        next_options=_CALCULATE_EXAMPLES,
    )


def _calculate_request(args: argparse.Namespace):
    """Build the structured calculation request; its checks run before anything is opened."""
    from app.calculation import (
        CalculationAssumption,
        CalculationInput,
        CalculationRequest,
        CalculationScope,
    )

    given = [flag for flag, name in _NOT_CALCULATE_FLAGS if getattr(args, name) not in (None, False)]
    if given:
        raise _calculate_refusal(
            f"'calculate' does not take {', '.join(given)}.",
            "A calculation request takes a target symbol, with --formula, --input, --assume, "
            "at most one --admit and --scope; it filters nothing.",
        )
    if args.target is None:
        raise _calculate_refusal("A calculation request needs a target symbol.", "None was given.")

    def pair(value: str, flag: str) -> tuple[str, str]:
        symbol, separator, rest = value.partition("=")
        if not separator:
            raise _calculate_refusal(
                f"{value!r} is not in the form SYMBOL=VALUE.",
                f"{flag} names a symbol and its value, e.g. R1=10 Ω.",
            )
        return symbol.strip(), rest.strip()

    try:
        scope = CalculationScope(args.scope.strip().upper().replace("-", "_"))
    except ValueError:
        raise _calculate_refusal(
            f"{args.scope!r} is not a value --scope accepts.", "Accepted: my-books, authorized."
        ) from None
    request = CalculationRequest(
        args.target,
        formulas=tuple(args.formula or ()),
        inputs=tuple(CalculationInput(*pair(v, "--input")) for v in args.input or ()),
        assumptions=tuple(CalculationAssumption(*pair(v, "--assume")) for v in args.assume or ()),
        admissions=tuple(v.strip() for v in args.admit or ()),
        scope=scope,
    )
    request.check()
    return request


def _print_calculation(result) -> None:
    """The text form of a calculation: the facts the JSON holds, arranged for reading."""
    versions, request = result.versions, result.request
    print(f'Calculation : target "{result.target}"; scope {request.scope.value}; '
          f"{versions.engine} v{versions.engine_version}; grammar v{versions.grammar_version}; "
          f"unit table v{versions.unit_table_version}")
    if result.database_opened:
        print(f"Database    : read-only {'yes' if result.read_only_connection else 'no'}; "
              "calculation never writes or migrates")
    else:
        print("Database    : not opened - the request admits no stored equation")
    print(f"Answer      : {result.status.value} - {result.message}")
    for given in (*result.inputs, *result.assumptions):
        label = "Input" if given.origin.value == "USER_INPUT" else "Assumption"
        print(f"{label:<12}: {given.symbol} = {given.value.text} "
              f"({given.origin.value}; given as {given.text!r})")
    if result.admitted is not None:
        item = result.admitted
        knowledge = item.knowledge
        pointer = "" if item.superseded_by is None else f"; SUPERSEDED, pointer to {item.superseded_by}"
        print(f'Admitted    : {knowledge.id} "{_one_line(item.equation.expression)}" - {item.label}; '
              f"lifecycle {knowledge.lifecycle_status.value}{pointer}; knowledge version "
              f"{knowledge.knowledge_version}")
        for row in item.evidence:
            print(f"              evidence {_evidence_line(row)}")
        _print_provenance(item.provenance, "              ")
        if item.not_usable:
            print(f"              not usable: {item.not_usable}")
        for conflict in item.conflicts:
            claims = []
            for claim in (conflict.claim_a, conflict.claim_b):
                shown = "withheld" if claim.knowledge is None else f'"{_one_line(claim.knowledge.statement)}"'
                claims.append(f"{claim.knowledge_id} {shown}")
            print(f"              conflict {conflict.conflict.id}: {' vs '.join(claims)}; "
                  f"resolution {conflict.resolution}")
        for contradiction in item.contradictions:
            print(f"              contradicts {contradiction.relationship.id}")
    if result.refused is not None:
        print(f"Refused     : {result.refused.knowledge_id} - {result.refused.reason.value}; not used")
    for entry in result.formulas:
        source = "the request" if entry.knowledge_id is None else f"stored equation {entry.knowledge_id} (UNCERTAIN)"
        print(f"Formula {entry.number:<4}: {_one_line(entry.text)} - source: {source}")
    for symbol in result.symbols:
        shown = "" if symbol.value is None else f" {symbol.value.relation} {symbol.value.text}".rstrip()
        origins = f" ({', '.join(o.value for o in symbol.origins)})" if symbol.origins else ""
        print(f"Symbol      : {symbol.symbol} {symbol.state.value}{origins}{shown}")
        for method in symbol.methods:
            outcome = "" if method.value is None else f" = {method.value.text} (step {method.step})"
            why = f" - {method.reason}" if method.reason else ""
            print(f"  formula {method.formula:<3}: {method.state.value}{outcome}{why}")
        if symbol.conflicting_values:
            values = "; ".join(f"{v.relation} {v.text}" for v in symbol.conflicting_values)
            print(f"  conflicting values: {values}; resolution not automatically selected")
        if symbol.conditional_on:
            print(f"  conditional on the assumption(s) {', '.join(symbol.conditional_on)}")
    for step in result.steps:
        print(f"Step {step.number:<7}: {step.text}")
        print(f"              {step.substitution}")
        print(f"              {step.symbol} {step.result.relation} {step.result.text} "
              f"(exact {step.result.exact}); dimension check {step.dimension_check}")
        sources = ", ".join(
            f"{i.symbol} {i.origin.value}" + (f" from step {i.from_step}" if i.from_step else "")
            for i in step.inputs
        )
        formula = "the request" if step.knowledge_id is None else f"stored equation {step.knowledge_id}"
        print(f"              formula source {formula}; input sources {sources or 'none'}")
    for gap in result.missing:
        needed = ", ".join(f"`{text}`" for text in gap.required_by) or "the target itself"
        print(f"Missing     : {gap.symbol} — required by {needed}")
    for cycle in result.cycles:
        print(f"Cycle       : {' -> '.join(cycle)}")
    for effect in result.effects:
        print(f"Assumption  : {effect.symbol} affects {', '.join(effect.symbols) or 'nothing reached'}"
              f" (formulas {', '.join(str(f) for f in effect.formulas) or 'none'})")
    withheld = result.withheld
    counts = [f"{name.replace('_', ' ')} {getattr(withheld, name)}"
              for name in ("unauthorized_evidence", "out_of_scope_evidence", "knowledge",
                           "relationships", "excluded_by_lifecycle")
              if getattr(withheld, name)]
    print(f"Withheld    : {', '.join(counts) if counts else 'nothing'}")
    print(f"Verification: {result.verification_status} - not independently verified")
    print(f"Rounding    : {versions.display_rule}")
    for note in result.notes:
        print(f"Note        : {note}")


def _cmd_provenance(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """"Where did you get this?" - the provenance of a stored item or an answer (ADR 0044).

        python -m app provenance K-00000001
        python -m app provenance --answer answer.json

    "This" is always explicit (P12-2): an identifier, or an answer file that `calculate`
    or `reason` printed with --json. For an item, knowledge.db is opened read-only, never
    migrated, and a schema this build does not read is refused; section 49's fields are
    shown as stored, with the quote and file checks. For an answer, the file is untrusted
    input: its recorded request is run again and its results compared (P12-12), and the
    database is opened only when the answer needs it. A citation is never invented: when
    nothing can be shown the answer is "Provenance unavailable." (section 205). Nothing
    is written.

    Exit codes (P12-15): 0 answered, including "Provenance unavailable." and every
    verification outcome; 2 invalid request; 3 not found (no stored item has that
    identifier); 5 the database cannot be used as it is (item provenance only); 70
    anything unexpected.
    """
    from app.provenance import ProvenanceScope, ProvenanceService, ProvenanceStatus
    from app.provenance import to_json as provenance_json
    from app.storage import DatabaseRole, Repository, connect, database_path
    from app.verification import needs_database, read_answer, verify

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_PROVENANCE_FLAGS if getattr(args, name) not in (None, False)]
    if given:
        raise _provenance_refusal(
            f"'provenance' does not take {', '.join(given)}.",
            "Ask about one stored item by its identifier, or about one answer with --answer; "
            "--scope applies to an identifier.",
        )
    if (args.target is None) == (args.answer is None):
        raise _provenance_refusal(
            "Name exactly one thing to ask about: an identifier or --answer FILE.",
            "Both were given." if args.target is not None else "Neither was given.",
        )
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    if args.answer is not None:
        if args.scope.strip().lower() != "my-books":
            raise _provenance_refusal(
                "--scope does not apply to an answer.",
                "An answer is verified in the scope its own request recorded.",
            )
        answer = read_answer(Path(args.answer))
        connection = None
        if needs_database(answer):
            try:
                connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
            except StorageError:
                connection = None  # reported by the verification as INCONCLUSIVE
        try:
            report = verify(answer, None if connection is None else Repository(connection))
        finally:
            if connection is not None:
                connection.close()
        if args.json:
            print(provenance_json(report))
        else:
            _print_verification(report)
        return ExitCode.OK
    try:
        scope = ProvenanceScope(args.scope.strip().upper().replace("-", "_"))
    except ValueError:
        raise _provenance_refusal(
            f"{args.scope!r} is not a value --scope accepts.", "Accepted: my-books, authorized."
        ) from None
    ProvenanceService.check_identifier(args.target)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        item = ProvenanceService(Repository(connection)).of_item(args.target, scope)
    finally:
        connection.close()
    if args.json:
        print(provenance_json(item))
    else:
        _print_item_provenance(item)
    return ExitCode.MISSING_INFORMATION if item.status is ProvenanceStatus.NOT_FOUND else ExitCode.OK


_PROVENANCE_EXAMPLES = (
    "python -m app provenance K-00000001",
    "python -m app provenance CPT-00000001 --scope authorized",
    "python -m app calculate I --formula \"I = V / R\" --input \"V=10 V\" --input \"R=5 Ω\" --json > answer.json",
    "python -m app provenance --answer answer.json",
)

#: Flags of other commands `provenance` refuses rather than ignores (P12-15).
_NOT_PROVENANCE_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name != "answer"),
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _provenance_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.provenance", data_changed=False, retry_safe=True,
        next_options=_PROVENANCE_EXAMPLES,
    )


def _print_citation(citation, indent: str) -> None:
    row = citation.evidence
    print(f'{indent}{row.id} ({row.subject_kind} {row.subject_id}): "{_one_line(row.evidence_text)}"')
    source = citation.source
    print(f"{indent}  source {source.id} \"{_one_line(source.name)}\": {source.source_category.value}, "
          f"{source.authorization.value}, stored availability {source.availability.value}")
    page = "no page recorded" if row.page_number is None else f"p.{row.page_number}"
    span = "no span recorded" if row.char_start is None else f"[{row.char_start}-{row.char_end}]"
    version = row.document_version_id or "not recorded"
    print(f"{indent}  document {row.document_id} {page} {span}; segment {row.segment_id or 'not recorded'}; "
          f"section {row.section or 'not recorded'}; document version {version}")
    run = citation.run
    run_text = ("no extraction run recorded" if run is None else
                f"run {run.id} #{run.run_number}, extractor v{run.extractor_version}, {run.status.value}")
    print(f"{indent}  extracted by {row.extraction_method} at {row.extraction_timestamp}; {run_text}")
    print(f"{indent}  quote check {citation.quote.status.value} - {citation.quote.detail}")


def _print_item_provenance(item, indent: str = "") -> None:
    """The text form of one item's provenance: the facts the JSON holds, for reading."""
    label = indent or ""
    print(f"{label}Provenance  : {item.identifier} ({item.kind}); scope {item.scope}")
    print(f"{label}Answer      : {item.status.value} - {item.message}")
    stored = item.item
    if stored is not None:
        name = (getattr(stored, "statement", None) or getattr(stored, "canonical_name", None)
                or getattr(stored, "expression", None) or getattr(stored, "name", None)
                or getattr(stored, "filename", None))
        if name:
            print(f'{label}Item        : "{_one_line(name)}"')
        if hasattr(stored, "relation_type"):
            print(f"{label}Relation    : {stored.from_id} --{stored.relation_type.value}--> {stored.to_id}")
    if item.origin:
        print(f"{label}Origin      : {item.origin}")
    for citation in item.citations:
        print(f"{label}Evidence    :")
        _print_citation(citation, label + "  ")
    for basis in item.bases:
        inference = basis.inference
        print(f"{label}Basis       : {inference.id} rule {inference.rule} v{inference.rule_version}")
        if basis.citation is not None:
            _print_citation(basis.citation, label + "  ")
    for source in item.sources:
        print(f"{label}Source      : {source.id} \"{_one_line(source.name)}\": {source.source_category.value}, "
              f"{source.authorization.value}, stored availability {source.availability.value}")
    for record in item.documents:
        document = record.document
        editions = ", ".join(f"{v.version_label} ({v.id})" for v in record.editions) or "no edition declared"
        print(f"{label}Document    : {document.id} {document.filename}; stored SHA-256 {document.file_hash}; "
              f"preserved file present {'yes' if record.preserved_file_present else 'no'}; {editions}")
        print(f"{label}              file check {record.file.status.value} - {record.file.detail}")
    for run in item.runs:
        print(f"{label}Run         : {run.id} #{run.run_number}: {run.status.value}, extractor v{run.extractor_version}")
    history = item.history
    if history is not None:
        parts = [f"lifecycle {history.lifecycle_status}"]
        if history.knowledge_version is not None:
            parts.append(f"knowledge version {history.knowledge_version}")
        if history.certainty:
            parts.append(f"certainty {history.certainty}")
        if history.superseded_by:
            parts.append(f"SUPERSEDED, stored pointer to {history.superseded_by}")
        print(f"{label}History     : {'; '.join(parts)}")
        for record_id, outcome, other in history.assessments:
            print(f"{label}              assessment {record_id}: {outcome} with {other}")
    for claim in item.claims:
        print(f"{label}Claim       :")
        _print_item_provenance(claim, label + "    ")
    withheld = item.withheld
    counts = [f"{name.replace('_', ' ')} {getattr(withheld, name)}"
              for name in ("unauthorized_evidence", "out_of_scope_evidence", "bases", "claims")
              if getattr(withheld, name)]
    print(f"{label}Withheld    : {', '.join(counts) if counts else 'nothing'}")
    print(f"{label}Verification: {item.verification.value}")


def _print_verification(report) -> None:
    """The text form of an answer's verification and section 204's exposures."""
    exposure = report.exposure
    print(f"Verification: {report.kind} answer (recorded status {report.answer_status}); "
          f"scope {report.scope}")
    print(f"Database    : {'opened read-only' if report.database_opened else 'not opened'}; "
          "verification never writes")
    print(f"Answer      : {report.status.value} - {report.message}")
    for check in report.checks:
        print(f"Check       : {check.kind} {check.subject_id}: {check.status.value} - {check.detail}")
    print(f"Source      : {exposure.source_status.value} - {exposure.source_message}")
    for item in exposure.sources:
        _print_item_provenance(item, "    ")
    for page in exposure.pages:
        print(f"Page        : {page}")
    for knowledge in exposure.relevant_knowledge:
        print(f"Knowledge   : {knowledge}")
    for line in exposure.derivation:
        print(f"Derivation  : {line}")
    for line in exposure.calculation:
        print(f"Calculation : {line}")
    for line in exposure.assumptions or ("none",):
        print(f"Assumption  : {line}")
    print(f"Verification status: {exposure.verification_status.value}")
    for note in report.notes:
        print(f"Note        : {note}")


def _cmd_interpret(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Natural-language interpretation (ADR 0045): a request's structured intents, as data.

        python -m app interpret "Open Chrome."
        python -m app interpret "Calculate I given I = V / R, V = 10 V and R = 5 Ω"

    Deterministic and rule-based (P13-1); no model, provider or network. It **interprets
    and never acts** (section 207; P13-2): no database or file is opened, and the command
    or action request it generates is printed, never run. Exit codes (P13-14): 0 for every
    status - interpreted, incomplete, ambiguous or unrecognised; 2 invalid request; 70
    anything unexpected.
    """
    from app.nlu import interpret, to_json

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_INTERPRET_FLAGS if getattr(args, name) not in (None, False)]
    if args.scope.strip().lower() != "my-books":
        given.append("--scope")
    if given:
        raise _interpret_refusal(
            f"'interpret' does not take {', '.join(given)}.",
            "Give the request as one quoted text; a scope is stated in the text (\"... in my books\").",
        )
    if args.target is None:
        raise _interpret_refusal("There is no request to interpret.", "Give the request as quoted text.")
    result = interpret(args.target)
    if args.json:
        print(to_json(result))
    else:
        _print_interpretation(result)
    return ExitCode.OK


_INTERPRET_EXAMPLES = (
    'python -m app interpret "Open Chrome."',
    'python -m app interpret "Calculate the drain current."',
    'python -m app interpret "Calculate I given I = V / R, V = 10 V and R = 5 Ω"',
)

#: Flags of other commands `interpret` refuses rather than ignores (P13-14).
_NOT_INTERPRET_FLAGS = (
    *_NOT_REASON_FLAGS,
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _interpret_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.interpret", data_changed=False, retry_safe=True,
        next_options=_INTERPRET_EXAMPLES,
    )


def _print_interpretation(result) -> None:
    """The text form of an interpretation: the facts the JSON holds, for reading."""
    from app.nlu import command_text

    print(f'Interpretation: "{_one_line(result.text)}" - {result.status.value}; '
          f"{result.grammar} v{result.grammar_version}")
    print(f"Answer        : {result.message}")
    for intent in result.intents:
        confirmation = "confirmation required" if intent.requires_confirmation else "no confirmation needed"
        print(f"Intent {intent.intent_id.split('-')[-1]:<7}: {intent.intent_type} - {intent.status.value}; classes "
              f"{', '.join(intent.task_classes)}; risk {intent.risk_level.value}; {confirmation}; rule {intent.rule}")
        if intent.target is not None:
            print(f"  target      : {intent.target}")
        for entity in intent.entities:
            known = "" if entity.known is None else (" (known)" if entity.known else " (not known)")
            print(f'  entity      : {entity.type.value} "{_one_line(entity.text)}" -> {entity.value}{known}')
        for name, value in intent.parameters:
            print(f"  parameter   : {name} = {value}")
        if intent.source_scope:
            print(f"  scope       : {intent.source_scope}")
        if intent.requested_output:
            print(f"  output      : {intent.requested_output}")
        if intent.command is not None:
            print(f"  command     : {command_text(intent.command)}")
        if intent.action is not None:
            parameters = ", ".join(f"{k}={v}" for k, v in intent.action.parameters)
            print(f"  action      : {intent.action.action}({parameters}) - {intent.action.note}")
        for missing in intent.missing:
            print(f"  missing     : {missing}")
        for candidate in intent.candidates:
            print(f"  candidate   : {candidate}")
        for route in intent.routes:
            print(f"  route       : {command_text(route)}")
        for note in intent.notes:
            print(f"  note        : {note}")
    for example in result.examples:
        print(f"Example       : {example}")
    for note in result.notes:
        print(f"Note          : {note}")


def _cmd_act(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """A parameterised action through the Phase 14 engine - a DRY RUN (ADR 0046).

        python -m app act OPEN_APPLICATION --param application=Notepad
        python -m app act CREATE_FILE --param path=C:\temp\notes.txt --param content=hello

    The request is validated and planned (section 103), then run on the **simulated
    computer**: file reads fall through to the real disk, read-only, so preconditions are
    realistic, but nothing is started, sent or written (P6 section 28). The report shows
    each step's preconditions, the simulated execution and the postcondition check, and
    says that no changes were made. Live execution, with the permission check, is Phase 15.

    Exit codes (P14-13): 0 the plan ran (every outcome, including FAILED or BLOCKED, is
    reported); 2 invalid request; 70 anything unexpected.
    """
    from app.actions import ActionEngine, SimulatedPlatform, StepStatus, to_json

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_ACT_FLAGS if getattr(args, name) not in (None, False)]
    if args.scope.strip().lower() != "my-books":
        given.append("--scope")
    if given:
        raise _act_refusal(f"'act' does not take {', '.join(given)}.",
                           "An action takes its name and --param NAME=VALUE pairs.")
    if args.target is None:
        raise _act_refusal("Name the action to run.", "For example OPEN_APPLICATION.")
    params = {}
    for pair in args.param or ():
        name, separator, value = pair.partition("=")
        if not separator or not name.strip():
            raise _act_refusal(f"{pair!r} is not NAME=VALUE.", "Each --param names one parameter and its value.")
        if name.strip() in params:
            raise _act_refusal(f"The parameter {name.strip()} is given twice.", "Give each parameter once.")
        params[name.strip()] = value
    if args.confirm and not args.execute:
        raise _act_refusal("--confirm applies only with --execute.", "A dry run changes nothing and needs no confirmation.")
    if args.execute:
        from app.orchestration import live_platform
        from app.security import all_permitted, decide

        engine = ActionEngine(live_platform())
        plan = engine.plan([(args.target, params)])
        permissions = decide(plan, confirmed=args.confirm)
        if not all_permitted(permissions):
            if args.json:
                print(json.dumps({"status": "REFUSED", "message": "nothing was executed", "permissions": [
                    {"step": d.step, "action": d.action, "decision": d.decision.value, "reason": d.reason}
                    for d in permissions]}, indent=2, ensure_ascii=False))
            else:
                for decision in permissions:
                    print(f"Permission  : step {decision.step} {decision.action}: {decision.decision.value} - {decision.reason}")
                print("Answer      : REFUSED - nothing was executed")
            return ExitCode.ACTION_FAILED
        if not args.json:
            for decision in permissions:
                print(f"Permission  : step {decision.step} {decision.action}: {decision.decision.value} - {decision.reason}")
    else:
        engine = ActionEngine(SimulatedPlatform(disk=True))
        plan = engine.plan([(args.target, params)])
    report = engine.run(plan)
    if args.json:
        print(to_json(report))
    else:
        _print_actions(report)
    if args.execute and report.status not in (StepStatus.VERIFIED, StepStatus.INCONCLUSIVE):
        return ExitCode.ACTION_FAILED
    return ExitCode.OK


_ACT_EXAMPLES = (
    "python -m app act OPEN_APPLICATION --param application=Notepad",
    "python -m app act OPEN_APPLICATION --param application=Calculator",
    "python -m app act CREATE_FOLDER --param path=C:\\temp\\results",
)

#: Flags of other commands `act` refuses rather than ignores (P14-13).
_NOT_ACT_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name not in ("param", "execute", "confirm")),
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _act_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.act", data_changed=False, retry_safe=True, next_options=_ACT_EXAMPLES,
    )


def _print_actions(report) -> None:
    """The text form of an execution report: the facts the JSON holds, for reading."""
    plan = report.plan
    mode = "DRY RUN on the simulated computer - no changes have been made" if report.dry_run else "LIVE"
    print(f"Actions     : {mode}; platform {plan.platform}")
    print(f"Answer      : {report.status.value} - {report.message}")
    for step, result in zip(plan.steps, report.steps):
        parameters = ", ".join(f"{k}={v}" for k, v in step.parameters)
        print(f"Step {step.number:<7}: {step.action}({parameters}) - risk {step.risk_level.value} - {result.status.value}")
        for name, value in step.resolved:
            print(f"  resolved  : {name}: {value}")
        for condition in result.conditions:
            print(f"  condition : {'yes' if condition.satisfied else 'NO '} {condition.text}"
                  + (f" - {condition.detail}" if condition.detail else ""))
        for attempt in result.attempts:
            print(f"  attempt   : {attempt.method} -> {attempt.returned}")
        print(f"  expected  : {result.expected}")
        print(f"  observed  : {result.observed}")
        print(f"  detail    : {result.detail}")
        if result.error:
            print(f"  error     : {result.error}")
    for note in report.notes:
        print(f"Note        : {note}")


def _cmd_do(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """A request carried out on this computer: section 211's pipeline (ADR 0047).

        python -m app do "Open Calculator."
        python -m app do "Close Notepad." --confirm
        python -m app do "Open Chrome." --dry-run

    Interpret -> plan -> validate -> permission -> execute -> verify -> report, every stage
    shown. Text is never executed as an operating-system command: only catalogue actions
    with validated parameters run (section 17). A LOW-risk step of your request is
    permitted; a MEDIUM-risk step needs --confirm; nothing HIGH-risk is enabled. With
    --dry-run the plan runs on the simulated computer and nothing changes. Knowledge
    requests are not run here: their command is shown.

    Exit codes (P15-10): 0 done - every step VERIFIED or INCONCLUSIVE (both reported); 6
    the objective was not achieved (failed verification, blocked, refused); 3 the request
    needs more information or holds no action; 2 invalid request; 70 anything unexpected.
    """
    from app.orchestration import Outcome, to_json

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_DO_FLAGS if getattr(args, name) not in (None, False)]
    if args.scope.strip().lower() != "my-books":
        given.append("--scope")
    if given:
        raise _do_refusal(f"'do' does not take {', '.join(given)}.",
                          "Give the request as quoted text, with --confirm or --dry-run if needed.")
    if args.target is None:
        raise _do_refusal("There is no request to carry out.", "Give it as quoted text, e.g. \"Open Calculator.\"")
    report = _request_pipeline(started, dry_run=args.dry_run).run(args.target, confirmed=args.confirm)
    if args.json:
        print(to_json(report))
    else:
        _print_pipeline(report)
    return {Outcome.DONE: ExitCode.OK, Outcome.NOT_EXECUTED: ExitCode.MISSING_INFORMATION}.get(
        report.outcome, ExitCode.ACTION_FAILED)


_DO_EXAMPLES = (
    'python -m app do "Open Calculator."',
    'python -m app do "Open Notepad." --dry-run',
    'python -m app do "Close Calculator." --confirm',
)

#: Flags of other commands `do` refuses rather than ignores (P15-13).
_NOT_DO_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name not in ("confirm", "dry_run")),
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _do_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.do", data_changed=False, retry_safe=True, next_options=_DO_EXAMPLES,
    )


def _print_pipeline(report) -> None:
    """The text form of a pipeline report: every stage, then the steps."""
    print(f'Request     : "{_one_line(report.text)}" - {"LIVE on this computer" if report.live else "DRY RUN (simulated)"}')
    first, *rest = report.message.split("\n")
    print(f"Answer      : {report.outcome.value} - {first}")
    for line in rest:
        print(f"              {line}")
    for stage in report.stages:
        print(f"{stage.name:<12}: {stage.outcome} - {_one_line(stage.detail)}")
    for workflow in report.workflows:
        print(f"Workflow    : {workflow.procedure_id} \"{_one_line(workflow.name)}\" - the {workflow.application} "
              f"manual, {workflow.document_id}" + ("" if workflow.page is None else f" p.{workflow.page}")
              + f"; {', '.join(workflow.labels)}")
        for step in workflow.steps:
            value = ("" if step.role != "INPUT" else
                     f" - {step.input}: MISSING" if step.value is None else f" - {step.input} = {step.value!r}")
            print(f"  step {step.number:<5}: {step.role:<7} {step.text}{value}")
    for command in report.reported_commands:
        print(f"Command     : {command}  (not run here; run it to answer the request)")
    if report.execution is not None:
        _print_actions(report.execution)


def _cmd_procedure(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Procedural memory: the documented procedures RUDRA stores (ADR 0048).

        python -m app procedure
        python -m app procedure PRC-00000001
        python -m app procedure PRC-00000001 --dry-run --param folder=C:\\temp\\work
        python -m app procedure PRC-00000001 --execute --confirm --param folder=C:\\temp\\work

    Without an identifier: every stored procedure. With one: section 56's fields - its
    labels, steps, parameters, each step's action (or why it cannot run), its source and
    its verification history. With --dry-run it runs on the simulated computer and
    nothing changes. With --execute it runs live - only with --confirm, because its steps
    come from a document (section 109), and only when every step is LOW risk - and the run
    is recorded in its verification history. A procedure runs only because you name it
    here, never because a document or another request says so. A missing parameter is
    answered "Missing information:" and nothing runs.

    knowledge.db is opened read-only, never migrated; a live run that executed opens it
    for writing once, to record the run.

    Exit codes (P16-12): 0 shown, or run and done; 3 not found, missing information, or
    not executable; 6 refused, or the objective was not achieved; 2 invalid request; 5
    the database cannot be used as it is - including a live run that executed but could
    not be recorded (its effects are reported); 70 anything unexpected.
    """
    from app.actions import SimulatedPlatform
    from app.orchestration import ProcedureOutcome, ProcedureRunner, to_json
    from app.procedures import LookupStatus, ProcedureMemory
    from app.provenance import ProvenanceScope
    from app.storage import DatabaseRole, Repository, connect, database_path

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_PROCEDURE_FLAGS if getattr(args, name) not in (None, False)]
    if given:
        raise _procedure_refusal(f"'procedure' does not take {', '.join(given)}.",
                                 "Name a procedure; to run it add --dry-run, or --execute --confirm, "
                                 "with --param NAME=VALUE.")
    try:
        scope = ProvenanceScope(args.scope.strip().upper().replace("-", "_"))
    except ValueError:
        raise _procedure_refusal(f"{args.scope!r} is not a value --scope accepts.",
                                 "Accepted: my-books, authorized.") from None
    if args.dry_run and args.execute:
        raise _procedure_refusal("--dry-run and --execute exclude each other.",
                                 "A dry run changes nothing; --execute runs live.")
    if args.confirm and not args.execute:
        raise _procedure_refusal("--confirm applies only with --execute.",
                                 "A dry run changes nothing and needs no confirmation.")
    running = args.dry_run or args.execute
    if args.param and not running:
        raise _procedure_refusal("--param applies only when running a procedure.",
                                 "Add --dry-run, or --execute --confirm.")
    if running and args.target is None:
        raise _procedure_refusal("Name the procedure to run.", "For example PRC-00000001.")
    supplied = []
    for pair in args.param or ():
        name, separator, value = pair.partition("=")
        if not separator or not name.strip():
            raise _procedure_refusal(f"{pair!r} is not NAME=VALUE.", "Each --param names one parameter and its value.")
        supplied.append((name.strip(), value))
    if args.target is not None:
        ProcedureMemory.check_identifier(args.target)
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        memory = ProcedureMemory(Repository(connection), scope)
        if args.target is None:
            stored = memory.all()
            if args.json:
                print(to_json({"scope": scope.value, "procedures": [_procedure_summary(p) for p in stored]}))
            else:
                _print_procedure_list(scope.value, stored)
            return ExitCode.OK
        lookup = memory.find(args.target)
    finally:
        connection.close()
    if lookup.status is not LookupStatus.FOUND:
        if args.json:
            print(to_json(lookup))
        else:
            print(f"Procedure   : {args.target}")
            print(f"Answer      : {lookup.status.value} - {lookup.message}")
        return ExitCode.MISSING_INFORMATION
    procedure = lookup.procedure
    screenshots = started.context.paths.cache_dir / "screenshots"

    def record(identifier, status, *, expected, observed):
        writer = connect(path, role=DatabaseRole.KNOWLEDGE)
        try:
            return ProcedureMemory(Repository(writer), scope).record(
                identifier, status, expected=expected, observed=observed)
        finally:
            writer.close()

    if not running:
        preview = ProcedureRunner(SimulatedPlatform(), record=record, screenshots=screenshots).preview(procedure)
        if args.json:
            print(to_json({"procedure": procedure, "steps": preview}))
        else:
            _print_procedure(procedure, preview)
        return ExitCode.OK
    if args.execute:
        from app.orchestration import live_platform

        platform = live_platform()
        screenshots.mkdir(parents=True, exist_ok=True)
    else:
        platform = SimulatedPlatform(disk=True)
    run = ProcedureRunner(platform, record=record, screenshots=screenshots).run(
        procedure, tuple(supplied), confirmed=args.confirm)
    if args.json:
        print(to_json(run))
    else:
        _print_procedure_run(run)
    if run.record_error is not None:
        return ExitCode.STORAGE  # the run executed; its record could not be written
    return {ProcedureOutcome.DONE: ExitCode.OK,
            ProcedureOutcome.MISSING_INFORMATION: ExitCode.MISSING_INFORMATION,
            ProcedureOutcome.NOT_EXECUTABLE: ExitCode.MISSING_INFORMATION}.get(run.outcome, ExitCode.ACTION_FAILED)


_PROCEDURE_EXAMPLES = (
    "python -m app procedure",
    "python -m app procedure PRC-00000001",
    "python -m app procedure PRC-00000001 --dry-run --param folder=C:\\temp\\work",
    "python -m app procedure PRC-00000001 --execute --confirm --param folder=C:\\temp\\work",
)

#: Flags of other commands `procedure` refuses rather than ignores (P16-12).
_NOT_PROCEDURE_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name not in ("param", "execute", "confirm", "dry_run")),
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _procedure_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.procedure", data_changed=False, retry_safe=True,
        next_options=_PROCEDURE_EXAMPLES,
    )


def _procedure_summary(procedure) -> dict:
    """One procedure in the list: its fields without the full provenance."""
    return {
        "id": procedure.id, "name": procedure.name, "labels": list(procedure.labels),
        "lifecycle_status": procedure.lifecycle_status, "steps": list(procedure.steps),
        "parameters": list(procedure.parameters), "executable": procedure.executable,
        "reason": procedure.reason, "execution_count": procedure.execution_count,
        "successful_executions": procedure.successful_executions,
        "failed_executions": procedure.failed_executions, "last_verified": procedure.last_verified,
        "source": procedure.source.status.value, "note": procedure.note,
    }


def _print_procedure_list(scope: str, procedures) -> None:
    print(f"Procedures  : {len(procedures)} stored (scope {scope}; DELETED and ARCHIVED not shown)")
    for p in procedures:
        runnable = "executable" if p.executable else f"not executable - {p.reason}"
        print(f'{p.id} "{_one_line(p.name)}" - {", ".join(p.labels)}; {len(p.steps)} step(s); '
              f'parameters {", ".join(p.parameters) or "none"}; {p.note}; {runnable}')


def _print_procedure(procedure, preview) -> None:
    """The text form of one procedure: section 56's fields, for reading."""
    p = procedure
    print(f'Procedure   : {p.id} "{_one_line(p.name)}"')
    print(f"Labels      : {', '.join(p.labels)}; lifecycle {p.lifecycle_status}")
    print(f"Executable  : {'yes' if p.executable else 'no - ' + p.reason}")
    print(f"Parameters  : {', '.join(p.parameters) or 'none'}")
    for step in preview:
        target = (f"{step.action}({', '.join(f'{k}={v}' for k, v in step.parameters)})" if step.action
                  else f"cannot run: {step.problem}")
        print(f'Step {step.number:<7}: "{step.documented}" -> {target}')
    print(f"Source      : {p.source.status.value} - {p.source.message}")
    for citation in p.source.citations:
        _print_citation(citation, "  ")
    print(f"History     : {p.note}")
    print(f"Counts      : {p.execution_count} execution(s), {p.successful_executions} successful, "
          f"{p.failed_executions} failed; last verified {p.last_verified or 'never'}")
    for entry in p.history:
        print(f"  {entry.id} {entry.recorded}: {entry.status}")
        print(f"    expected: {entry.expected}")
        print(f"    observed: {entry.observed}")
    print(f"Limitations : {p.limitations or 'none recorded'}")
    print(f"Application : known version {p.known_application_version or 'not recorded'}")


def _print_procedure_run(run) -> None:
    p = run.procedure
    mode = "LIVE on this computer" if run.live else "DRY RUN (simulated) - nothing is changed or recorded"
    print(f'Procedure   : {p.id} "{_one_line(p.name)}" - {mode}')
    first, *rest = run.message.split("\n")
    print(f"Answer      : {run.outcome.value} - {first}")
    for line in rest:
        print(f"              {line}")
    print(f"Parameters  : {', '.join(f'{k}={v}' for k, v in run.parameters) or 'none'}")
    for step in run.steps:
        target = (f"{step.action}({', '.join(f'{k}={v}' for k, v in step.parameters)})" if step.action
                  else f"cannot run: {step.problem}")
        print(f'Step {step.number:<7}: "{step.text}" -> {target}')
    for decision in run.permissions:
        print(f"Permission  : step {decision.step} {decision.action}: {decision.decision.value} - {decision.reason}")
    if run.execution is not None:
        _print_actions(run.execution)
    if run.recorded:
        print(f"Recorded    : {run.recorded} in the verification history of {p.id}")
    if run.record_error:
        print(f"Recorded    : NOT RECORDED - {run.record_error}")


def _cmd_manual(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Application manuals the user declared, and what RUDRA extracted from them (ADR 0049).

        python -m app manual
        python -m app manual DOC-00000001

    Without an identifier: every declared manual, with its application and the number of
    items of each kind. With one: section 214's seven kinds - menus, commands, workflows,
    shortcuts, parameters, constraints and file formats - each with its identifier and
    page (`python -m app provenance ID` gives the full citation). A document is a manual
    only because you declared it one: python -m app extract FILE.pdf --manual "APPLICATION".
    Read-only.

    Exit codes (P17-11): 0 shown; 3 the document is not a declared manual; 2 invalid
    request; 5 the database cannot be used as it is; 70 anything unexpected.
    """
    from app.manuals import ManualLibrary
    from app.models.identifiers import EntityKind, is_valid_id, parse_id
    from app.orchestration import to_json
    from app.provenance import ProvenanceScope
    from app.storage import DatabaseRole, Repository, connect, database_path

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_MANUAL_FLAGS if getattr(args, name) not in (None, False)]
    if given:
        raise _manual_refusal(f"'manual' does not take {', '.join(given)}.",
                              "Name a declared manual by its document identifier, or nothing to list them.")
    try:
        scope = ProvenanceScope(args.scope.strip().upper().replace("-", "_"))
    except ValueError:
        raise _manual_refusal(f"{args.scope!r} is not a value --scope accepts.",
                              "Accepted: my-books, authorized.") from None
    if args.target is not None and (not is_valid_id(args.target)
                                    or parse_id(args.target)[0] is not EntityKind.DOCUMENT):
        raise _manual_refusal(f"{args.target!r} is not a document identifier.", "For example DOC-00000001.")
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        library = ManualLibrary(Repository(connection), scope)
        if args.target is None:
            manuals = tuple(library.manual(d.document_id) for d in library.declared())
            if args.json:
                print(to_json({"manuals": [_manual_summary(m) for m in manuals]}))
            else:
                print(f"Manuals     : {len(manuals)} declared")
                for m in manuals:
                    counts = _manual_summary(m)["counts"]
                    print(f'{m.declaration.document_id} "{_one_line(m.document)}" - the manual of '
                          f"'{m.declaration.application}': " + ", ".join(f"{k} {v}" for k, v in counts.items()))
            return ExitCode.OK
        manual = library.manual(args.target)
    finally:
        connection.close()
    if manual is None:
        message = f"{args.target} is not a declared application manual."
        if args.json:
            print(to_json({"document_id": args.target, "status": "NOT_DECLARED", "message": message}))
        else:
            print(f"Manual      : {args.target}")
            print(f"Answer      : NOT_DECLARED - {message} Declare one with: "
                  f'python -m app extract --re-extract {args.target} --manual "APPLICATION"')
        return ExitCode.MISSING_INFORMATION
    if args.json:
        print(to_json(manual))
    else:
        _print_manual(manual)
    return ExitCode.OK


_MANUAL_EXAMPLES = (
    "python -m app manual",
    "python -m app manual DOC-00000001",
    'python -m app extract "Application Manual.pdf" --manual "Application name"',
)

#: Flags of other commands `manual` refuses rather than ignores (P17-11).
_NOT_MANUAL_FLAGS = (
    *_NOT_REASON_FLAGS,
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _manual_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.manual", data_changed=False, retry_safe=True,
        next_options=_MANUAL_EXAMPLES,
    )


def _manual_summary(manual) -> dict:
    return {
        "document_id": manual.declaration.document_id, "document": manual.document,
        "application": manual.declaration.application, "declared": manual.declaration.recorded,
        "stage_ran": manual.stage_ran,
        "counts": {"menus": len(manual.menus), "commands": len(manual.commands),
                   "workflows": len(manual.workflows), "shortcuts": len(manual.shortcuts),
                   "parameters": len(manual.parameters), "constraints": len(manual.constraints),
                   "file formats": len(manual.file_formats)},
    }


def _print_manual(manual) -> None:
    """The text form of one declared manual: section 214's seven kinds, for reading."""
    from app.manuals import workflow_inputs

    d = manual.declaration
    print(f'Manual      : {d.document_id} "{_one_line(manual.document)}" - the manual of '
          f"'{d.application}' (declared {d.recorded})")
    print(f"Stage       : {'run' if manual.stage_ran else 'no item found or not run'}")
    print(f"Menus       : {', '.join(manual.menus) or 'none found'}")
    print(f"Commands    : {', '.join(manual.commands) or 'none found'}")
    print(f"Workflows   : {len(manual.workflows)}")
    for w in manual.workflows:
        inputs = workflow_inputs(w)
        print(f'  {w.id} "{_one_line(w.name)}": {" → ".join(w.steps)}'
              + (f" (inputs: {', '.join(inputs)})" if inputs else ""))
    print(f"Shortcuts   : {len(manual.shortcuts)}")
    for item in manual.shortcuts:
        print(f'  {item.statement.removeprefix("Press ")} - "{_one_line(item.quote)}" '
              f"({item.procedure_id or item.knowledge_id}, p.{item.page})")
    print(f"Parameters  : {', '.join(manual.parameters) or 'none found'}")
    print(f"Constraints : {len(manual.constraints)}")
    for item in manual.constraints:
        print(f'  "{_one_line(item.quote)}" ({item.knowledge_id}, p.{item.page})')
    print(f"File formats: {len(manual.file_formats)}")
    for item in manual.file_formats:
        print(f'  {item.name.removeprefix("File format ")} - "{_one_line(item.quote)}" '
              f"({item.knowledge_id}, p.{item.page})")
    print("Provenance  : python -m app provenance ID gives any item's full citation.")


def _manual_after_extract(connection, document_id: str, application: str):
    """Record the user's declaration and run the manual stage once (ADR 0049 P17-2, P17-3),
    in one transaction. Returns whether the declaration was new, and the stage's report."""
    from app.manuals import ManualStage, declare
    from app.storage import Repository

    repository = Repository(connection)
    try:
        _declaration, new = declare(repository, document_id, application)
        report = ManualStage(repository).run(document_id)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return new, report


def _print_manual_stage(new: bool, report) -> None:
    print(f"Manual     : {report.document_id} declared the manual of '{report.application}' "
          f"({'recorded now' if new else 'already recorded'})")
    if not report.ran:
        print(f"Manual stage: {report.message}")
    else:
        found = ", ".join(f"{kind.lower().replace('_', ' ')} {count}" for kind, count in report.found)
        print(f"Manual stage: {found}; {report.created} knowledge object(s), {report.procedures} procedure(s)")
    print(f"             see: python -m app manual {report.document_id}")


def _cmd_research(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Local knowledge first; the Internet only through a site you name (ADR 0050).

        python -m app research "What is a memristor?"
        python -m app research "What is a memristor?" --site https://example.org/memristor.html

    The question is answered from your documents first (scope my-books). When they do not
    answer it, the answer is "Insufficient authorized information." and nothing is
    fetched: RUDRA never searches the Internet on its own (section 124). With --site you
    authorize that one website - the URL's host and directory - for this request: the page
    is retrieved (http or https, at most 2 MiB, text only, nothing of yours sent), the
    sentences naming the subject are recorded with the URL, retrieval time, content hash
    and your authorization, and the answer is labelled AUTHORIZED_EXTERNAL_SOURCE /
    EXTERNAL_INFORMATION. External information is never shown as your own: it appears
    only in scope authorized. A contradiction with a local definition is recorded, and
    both are shown.

    knowledge.db is read only, unless --site stores what the site returned.

    Exit codes (P18-12): 0 answered (locally, or externally, labelled); 3 insufficient
    authorized information, or the site does not answer; 7 the page could not be retrieved
    within the authorization and the limits (nothing stored); 2 invalid request; 5 the
    database cannot be used as it is; 70 anything unexpected.
    """
    from app.internet import Research, ResearchStatus, RetrievalFailed
    from app.orchestration import to_json
    from app.query import SourceScope
    from app.storage import CODE_SCHEMA_VERSION, DatabaseRole, Repository, connect, database_path, migrate
    from app.storage import schema_version

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_RESEARCH_FLAGS if getattr(args, name) not in (None, False)]
    if given:
        raise _research_refusal(f"'research' does not take {', '.join(given)}.",
                                "Ask one question, with --site URL to authorize one website.")
    if args.target is None:
        raise _research_refusal("There is no question.", 'Ask it as quoted text, e.g. "What is a memristor?"')
    try:
        scope = SourceScope(args.scope.strip().upper().replace("-", "_"))
    except ValueError:
        raise _research_refusal(f"{args.scope!r} is not a value --scope accepts.",
                                "Accepted: my-books, authorized.") from None
    if args.site is not None:
        from app.internet import site

        site(args.site)  # refused before any database is opened
    paths = started.context.paths
    path = database_path(paths, DatabaseRole.KNOWLEDGE)
    connection = None
    if args.site is not None:
        new = not path.exists()
        connection = connect(path, role=DatabaseRole.KNOWLEDGE)
        if new:
            migrate(connection, database_path=path)
            connection.commit()
        elif schema_version(connection) != CODE_SCHEMA_VERSION:
            connection.close()
            raise StorageError.of(
                "The knowledge database's schema version is not the one this build reads; research never "
                "migrates.", f"This build reads version {CODE_SCHEMA_VERSION}.", stage="cli.research",
                data_changed=False, retry_safe=True,
                next_options=("Migrate explicitly with: python -m app db  (for the live database make a "
                              "fresh D-15 backup first).",))
    elif path.exists():
        connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        research = Research(None if connection is None else Repository(connection), database_path=path,
                            documents_dir=paths.documents_dir)
        try:
            answer = research.ask(args.target, site_url=args.site, scope=scope)
        except RetrievalFailed as failure:
            if connection is not None:
                connection.rollback()
            if args.json:
                print(to_json({"status": "RETRIEVAL_FAILED", "site": args.site, "reason": failure.reason,
                               "message": "nothing was stored"}))
            else:
                print(f'Question    : "{_one_line(args.target)}"')
                print(f"Answer      : RETRIEVAL_FAILED - {failure.reason}; nothing was stored")
            return ExitCode.EXTERNAL_FAILED
        if connection is not None and args.site is not None:
            connection.commit()
    finally:
        if connection is not None:
            connection.close()
    if args.json:
        print(to_json(answer))
    else:
        _print_research(answer)
    return ExitCode.OK if answer.status in (ResearchStatus.LOCAL, ResearchStatus.EXTERNAL) \
        else ExitCode.MISSING_INFORMATION


_RESEARCH_EXAMPLES = (
    'python -m app research "What is a memristor?"',
    'python -m app research "What is a memristor?" --site https://example.org/memristor.html',
)

#: Flags of other commands `research` refuses rather than ignores (P18-12).
_NOT_RESEARCH_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name != "site"),
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _research_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.research", data_changed=False, retry_safe=True,
        next_options=_RESEARCH_EXAMPLES,
    )


def _print_research(answer) -> None:
    """The text form of a research answer: local first, then the labelled external part."""
    print(f'Question    : "{_one_line(answer.question)}" - subject {answer.subject!r}; scope {answer.scope}')
    print(f"Local       : {answer.local.status} - {_one_line(answer.local.message)}")
    for knowledge_id, statement in answer.local.definitions:
        print(f'  {knowledge_id}: "{_one_line(statement)}"')
    first, *rest = answer.message.split("\n")
    print(f"Answer      : {answer.status.value} - {first}")
    for line in rest:
        print(f"              {line}")
    for name, value in answer.labels:
        print(f"{name:<12}: {value}")
    if answer.record is not None:
        r = answer.record
        print(f"Authorized  : {answer.site} (your --site; recorded as {r.grant_id})")
        print(f"Stored      : document {r.document_id} ({'new' if r.new else 'already stored'}), source "
              f"{r.source_id}; {r.content_type}; SHA-256 {r.sha256}")
        for claim in answer.external:
            print(f'  {claim.knowledge_id}: "{_one_line(claim.statement)}" ({claim.occurrence_id}, '
                  f"[{claim.char_start}-{claim.char_end}])")
    for conflict in answer.conflicts:
        print(f"Conflict    : {conflict.conflict_id} - neither replaces the other")
        print(f'  LOCAL SOURCE    {conflict.local_id}: "{_one_line(conflict.local_statement)}"')
        print(f'  EXTERNAL SOURCE {conflict.external_id}: "{_one_line(conflict.external_statement)}"')
    for knowledge_id, statement, retrieved, document_id in answer.cached:
        print(f'Cached      : {knowledge_id}: "{_one_line(statement)}" - EXTERNAL_INFORMATION from {document_id}, '
              f"retrieved {retrieved}, not checked since")


def _cmd_diagram(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """A structure diagram of one concept, from stored knowledge only (ADR 0051).

        python -m app diagram "Draw a diagram of an 8-bit ripple carry adder"
        python -m app diagram --name "full adder" --out C:\\temp\\diagrams

    Section 144's pipeline: the subject, its stored relationships in scope, the request's
    parameters, a structured specification, an SVG drawn from it alone, and the SVG read
    back and checked against it. Every box is a stored concept and every line a stored,
    evidenced relationship; what the knowledge does not state - a composition's count, how
    a requested "8-bit" maps onto the structure - is marked unknown, never drawn as a value.
    The specification (JSON) is written beside the image (SVG) under data/cache/diagrams,
    or --out FOLDER; files are never overwritten. knowledge.db is only read.

    Exit codes (P19-11): 0 drawn and VERIFIED; 3 insufficient information, ambiguous, or
    not a diagram RUDRA can draw; 6 drawn but its validation FAILED; 2 invalid request; 5
    the database cannot be used as it is; 70 anything unexpected.
    """
    from app.diagrams import draw
    from app.orchestration import to_json
    from app.query import SourceScope
    from app.storage import DatabaseRole, connect, database_path

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_DIAGRAM_FLAGS if getattr(args, name) not in (None, False)]
    if given:
        raise _diagram_refusal(f"'diagram' does not take {', '.join(given)}.",
                               "Give the request as quoted text, or --name CONCEPT.")
    if (args.target is None) == (args.name is None):
        raise _diagram_refusal("Give exactly one of: the request as quoted text, or --name CONCEPT.",
                               "Both were given." if args.target is not None else "Neither was given.")
    try:
        scope = SourceScope(args.scope.strip().upper().replace("-", "_"))
    except ValueError:
        raise _diagram_refusal(f"{args.scope!r} is not a value --scope accepts.",
                               "Accepted: my-books, authorized.") from None
    out_dir = Path(args.out) if args.out else started.context.paths.cache_dir / "diagrams"
    path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True) if path.exists() else None
    try:
        answer = draw(connection, database_path=path, request=args.target or "", out_dir=out_dir,
                      name=args.name, scope=scope)
    finally:
        if connection is not None:
            connection.close()
    if args.json:
        print(to_json(answer))
    else:
        _print_diagram(answer)
    if answer.status != "DRAWN":
        return ExitCode.MISSING_INFORMATION
    return ExitCode.OK if answer.validation.status == "VERIFIED" else ExitCode.ACTION_FAILED


_DIAGRAM_EXAMPLES = (
    'python -m app diagram "Draw a diagram of an 8-bit ripple carry adder"',
    'python -m app diagram --name "full adder"',
)

#: Flags of other commands `diagram` refuses rather than ignores (P19-11).
_NOT_DIAGRAM_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name not in ("name", "out")),
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _diagram_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.diagram", data_changed=False, retry_safe=True,
        next_options=_DIAGRAM_EXAMPLES,
    )


def _print_diagram(answer) -> None:
    print(f'Request     : "{_one_line(answer.request)}"')
    print(f"Answer      : {answer.status} - {answer.message}")
    for candidate in answer.candidates:
        print(f"  candidate : {candidate}")
    spec = answer.specification
    if spec is None:
        return
    print(f"Subject     : {spec.subject}; scope {spec.scope}")
    for node in spec.nodes:
        place = "beside" if node.side else f"level {node.level}"
        print(f"Node        : {node.concept_id} {node.name} ({place})"
              + (f" - defined by {node.definition_id}" if node.definition_id else ""))
    for edge in spec.edges:
        evidence = ", ".join(evidence_id for evidence_id, _ in edge.evidence)
        print(f"Edge        : {edge.relationship_id} {edge.from_id} -{edge.label}-> {edge.to_id} (evidence {evidence})")
    for parameter in spec.parameters:
        print(f"Parameter   : {parameter.text} from the {parameter.origin.lower()} - {parameter.note}")
    for unknown in spec.unknowns:
        print(f"Unknown     : {unknown}")
    for note in spec.notes:
        print(f"Note        : {note}")
    print(f"Validation  : {answer.validation.status} - {answer.validation.detail}")
    print(f"Image       : {answer.svg_path} (SHA-256 {answer.svg_sha256}){' - already drawn' if answer.reused else ''}")
    print(f"Specification: {answer.json_path} (SHA-256 {answer.json_sha256})")
    print(f"Traceable to: {', '.join(spec.identifiers)}")


def _request_pipeline(started: StartupResult, *, dry_run: bool):
    """The request pipeline as `do` and `voice` use it (ADRs 0047, 0049, 0052): the simulated
    computer for a dry run, the live one otherwise, and the declared manuals' workflows -
    the knowledge database opened read-only, and only when a request needs it."""
    from app.actions import SimulatedPlatform
    from app.orchestration import Pipeline

    screenshots = started.context.paths.cache_dir / "screenshots"
    if dry_run:
        platform = SimulatedPlatform(disk=True)
    else:
        from app.orchestration import live_platform

        platform = live_platform()
        screenshots.mkdir(parents=True, exist_ok=True)

    def workflows(names):
        from app.manuals import ManualLibrary
        from app.storage import DatabaseRole, Repository, connect, database_path

        path = database_path(started.context.paths, DatabaseRole.KNOWLEDGE)
        if not path.exists():
            return ()
        connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
        try:
            return ManualLibrary(Repository(connection)).project_workflows(names)
        finally:
            connection.close()

    return Pipeline(platform, screenshots=screenshots, workflows=workflows)


def _cmd_voice(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """Voice: speech to text, then exactly what `do` does with that text (ADR 0052).

        python -m app voice --audio request.wav --dry-run
        python -m app voice --audio request.wav
        python -m app voice --listen
        python -m app voice --say "Open Calculator." --audio new.wav

    The speech is recognized by Windows' own engine with RUDRA's command grammar (its verbs
    and the registered applications) and free dictation as the fallback; the transcript,
    the grammar that matched and the engine's confidence are shown. The transcript then
    goes through the same pipeline as typed text - interpret, plan, validate, permission,
    execute, verify, report. Voice never authorizes: a MEDIUM-risk step still needs the
    typed --confirm, and a spoken "confirm" is only text. --listen opens the microphone for
    one utterance of at most 10 seconds and keeps nothing; nothing ever listens on its own.
    --say writes speech to a new WAV file.

    Exit codes (P20-9): as `do` - 0 done; 6 the objective was not achieved; 3 nothing
    recognized, or the request needs more information; 2 invalid request - plus 8 when the
    speech engine cannot be used; 70 anything unexpected.
    """
    from app.orchestration import Outcome, to_json
    from app.voice import SpeechUnavailable, listen, recognize_file, synthesize

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_VOICE_FLAGS if getattr(args, name) not in (None, False)]
    if args.scope.strip().lower() != "my-books":
        given.append("--scope")
    if args.target is not None:
        given.append("a request as text (use do)")
    if given:
        raise _voice_refusal(f"'voice' does not take {', '.join(given)}.",
                             "Give --audio FILE.wav or --listen, with --dry-run or --confirm if needed; "
                             "or --say TEXT --audio NEW.wav.")
    modes = [m for m, on in (("--audio", args.audio is not None and args.say is None), ("--listen", args.listen),
                             ("--say", args.say is not None)) if on]
    if len(modes) != 1:
        raise _voice_refusal("Give exactly one of --audio FILE, --listen, or --say TEXT --audio NEW.wav.",
                             f"Given: {', '.join(modes) or 'none'}.")
    try:
        if args.say is not None:
            if args.audio is None or args.confirm or args.dry_run:
                raise _voice_refusal("--say takes only --audio NEW.wav.", "It writes speech; it runs nothing.")
            written = synthesize(args.say, Path(args.audio))
            if args.json:
                print(to_json({"status": "WRITTEN", "audio": str(written), "text": args.say}))
            else:
                print(f"Speech      : wrote {written} ({written.stat().st_size} bytes)")
            return ExitCode.OK
        transcript = listen() if args.listen else recognize_file(Path(args.audio))
    except SpeechUnavailable as failure:
        if args.json:
            print(to_json({"status": "SPEECH_UNAVAILABLE", "reason": failure.reason}))
        else:
            print(f"Answer      : SPEECH_UNAVAILABLE - {failure.reason}")
        return ExitCode.SPEECH_UNAVAILABLE
    if not transcript.text:
        if args.json:
            print(to_json({"speech": transcript, "report": None, "message": "nothing was recognized; nothing was executed"}))
        else:
            _print_speech(transcript)
            print("Answer      : NOTHING_RECOGNIZED - nothing was recognized; nothing was executed")
        return ExitCode.MISSING_INFORMATION
    report = _request_pipeline(started, dry_run=args.dry_run).run(transcript.text, confirmed=args.confirm)
    if args.json:
        print(to_json({"speech": transcript, "report": report}))
    else:
        _print_speech(transcript)
        _print_pipeline(report)
    return {Outcome.DONE: ExitCode.OK, Outcome.NOT_EXECUTED: ExitCode.MISSING_INFORMATION}.get(
        report.outcome, ExitCode.ACTION_FAILED)


_VOICE_EXAMPLES = (
    "python -m app voice --audio request.wav --dry-run",
    "python -m app voice --listen",
    'python -m app voice --say "Open Calculator." --audio new.wav',
)

#: Flags of other commands `voice` refuses rather than ignores (P20-9).
_NOT_VOICE_FLAGS = tuple((flag, name) for flag, name in _NOT_DO_FLAGS if name not in ("audio", "listen", "say"))


def _voice_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.voice", data_changed=False, retry_safe=True, next_options=_VOICE_EXAMPLES,
    )


def _print_speech(transcript) -> None:
    confidence = "not given" if transcript.confidence is None else f"{transcript.confidence:.2f}"
    grammar = {"commands": "RUDRA's command grammar", "dictation": "free dictation (less reliable)"}.get(
        transcript.grammar, "none")
    print(f"Speech      : {transcript.audio}")
    print(f'Transcript  : "{transcript.text}" - {grammar}; confidence {confidence}; {transcript.recognizer}')


def _cmd_ask(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """One request through RUDRA's whole architecture (ADR 0054; section 223).

        python -m app ask "What is resistance?"
        python -m app ask "Open Calculator and calculate 123 × 456" --dry-run
        python -m app ask --audio request.wav --dry-run

    Text - or speech, recognized as `voice` does - is interpreted into structured requests;
    each knowledge request is answered by the same command that answers it alone (query,
    reason, calculate, provenance, diagram), each action request goes through the same
    pipeline as `do` (permission, execution, verification). The answer follows section 227:
    Answer, Basis, Reasoning, Calculation, Assumptions, Sources, Status, Missing information;
    "I cannot determine this from the currently authorized information." when it cannot
    (section 228); "Conflict detected ... Resolution: Not automatically selected." when
    sources disagree (section 229); "Unknown." when nothing stored answers (section 230).
    An Internet search is never run from here: `research ... --site` asks for it.

    Exit codes (P22-10): 0 every part carried out or answered; 3 a part needs information
    from you; 6 an action part failed or was refused; 2 invalid request; 8 the speech engine
    cannot be used; 70 anything unexpected.
    """
    import json as _json

    from app.nlu import InterpretationStatus, interpret
    from app.orchestration import answers, to_json

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_ASK_FLAGS if getattr(args, name) not in (None, False)]
    if given:
        raise _ask_refusal(f"'ask' does not take {', '.join(given)}.",
                           "Ask as quoted text, or with --audio FILE.wav or --listen; add --dry-run or --confirm "
                           "for actions and --scope for knowledge.")
    sources = [s for s, on in (("text", args.target is not None), ("--audio", args.audio is not None),
                                ("--listen", args.listen)) if on]
    if len(sources) != 1:
        raise _ask_refusal("Ask one request: quoted text, --audio FILE.wav, or --listen.",
                           f"Given: {', '.join(sources) or 'none'}.")
    speech = None
    if args.target is not None:
        text = args.target
    else:
        from app.voice import SpeechUnavailable, listen, recognize_file

        try:
            speech = listen() if args.listen else recognize_file(Path(args.audio))
        except SpeechUnavailable as failure:
            print(to_json({"status": "SPEECH_UNAVAILABLE", "reason": failure.reason}) if args.json
                  else f"Answer      : SPEECH_UNAVAILABLE - {failure.reason}")
            return ExitCode.SPEECH_UNAVAILABLE
        if not speech.text:
            print(to_json({"speech": speech, "parts": [], "message": "nothing was recognized"}) if args.json
                  else "Answer      : NOTHING_RECOGNIZED - nothing was recognized; nothing was executed")
            return ExitCode.MISSING_INFORMATION
        text = speech.text
    builders = {"query": answers.from_query, "reason": answers.from_reasoning, "calculate": answers.from_calculation,
                "provenance": answers.from_provenance, "diagram": answers.from_diagram}
    interpretation = interpret(text)
    interpreted = interpretation.status is InterpretationStatus.INTERPRETED
    parts = []
    action_numbers = []
    for number, intent in enumerate(interpretation.intents, 1):
        label = intent.target or intent.intent_type
        if intent.intent_type == "WEB_SEARCH":  # never run from here: it needs the user's site (ADR 0050)
            parts.append(answers.not_run(number, label, intent.intent_type,
                                         "An Internet search needs your authorization of one website for the "
                                         'request: python -m app research "QUESTION" --site URL (ADR 0050).'))
            continue
        if intent.intent_type == "IMAGE_REQUEST" and intent.target:  # the diagram path (ADR 0051)
            argv = ["diagram", f"Draw a diagram of {intent.target}"]
            answer, failure = _ask_run(started, argv)
            parts.append(answers.from_diagram(number, label, intent.intent_type, argv, answer) if failure is None
                         else answers.not_run(number, label, intent.intent_type, failure, status="NEEDS_INFORMATION"))
            continue
        if intent.action is not None or intent.intent_type in _ACTION_INTENTS:
            action_numbers.append(number)
            continue
        if intent.status is not InterpretationStatus.INTERPRETED:
            parts.append(answers.not_run(number, label, intent.intent_type, intent.status.value.lower()
                                         + " - " + interpretation.message, status="NEEDS_INFORMATION",
                                         missing=tuple(intent.missing) + tuple(intent.candidates)))
            continue
        builder = builders.get((intent.command or ("",))[0])
        if builder is None:
            parts.append(answers.not_run(number, label, intent.intent_type,
                                         "This kind of request is not answered from ask."))
            continue
        argv = [*intent.command]
        if argv[0] != "calculate" and args.scope.strip().lower() != "my-books":
            argv += ["--scope", args.scope]
        answer, failure = _ask_run(started, argv)
        if failure is not None:
            parts.append(answers.not_run(number, label, intent.intent_type, failure, status="NEEDS_INFORMATION"))
            continue
        if builder is answers.from_query:
            parts.append(builder(number, label, intent.intent_type, argv, answer, intent.requested_output))
        else:
            parts.append(builder(number, label, intent.intent_type, argv, answer))
    if action_numbers:
        label = ", ".join(interpretation.intents[n - 1].target or interpretation.intents[n - 1].intent_type
                          for n in action_numbers)
        report = _request_pipeline(started, dry_run=args.dry_run).run(text, confirmed=args.confirm)
        plain = _json.loads(to_json(report))
        part = answers.from_pipeline(action_numbers[0], label, "ACTIONS", plain)
        parts.append(part)
    if not interpretation.intents:  # nothing understood: said so, never guessed
        parts.append(answers.not_run(1, text, "UNRECOGNIZED", interpretation.message, status="NEEDS_INFORMATION"))
    parts.sort(key=lambda p: p.number)
    _ask_audit(text, parts)
    if args.json:
        print(to_json({"request": text, "speech": speech, "interpretation": interpretation, "parts": parts}))
    else:
        _print_ask(text, speech, parts, interpreted)
    statuses = {p.status for p in parts}
    if statuses & {"FAILED", "REFUSED"}:
        return ExitCode.ACTION_FAILED
    if not parts or statuses & {"NEEDS_INFORMATION", "CANNOT_DETERMINE", "NOT_EXECUTED", "UNKNOWN", "INSUFFICIENT",
                                "NOT_RUN"}:
        return ExitCode.MISSING_INFORMATION
    return ExitCode.OK


#: Intents that belong to the action path even without an action request of their own
#: (the pipeline answers them: documented workflows, and actions not available).
_ACTION_INTENTS = frozenset({"CREATE_PROJECT", "DELETE_FILE", "UNRESOLVED_REFERENCE"})


def _ask_run(started: StartupResult, argv: list[str]) -> tuple[dict | None, str | None]:
    """Run one command's own handler on the shared startup, its JSON answer captured
    (ADR 0054 P22-2): the same code, refusals and scope rules as the command alone."""
    import contextlib
    import io

    args = _build_parser().parse_args([*argv, "--json"])
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            _COMMANDS[args.command](started, args)
    except RudraError as error:
        return None, f"{error.report.summary} {error.report.reason}"
    text = buffer.getvalue().strip()
    if not text:
        return None, "the command gave no answer"
    return json.loads(text), None


def _ask_audit(text: str, parts) -> None:
    from app.core.logging_setup import get_logger

    get_logger("ui.ask").audit(json.dumps({"event": "ask", "text": text, "parts": [
        {"number": p.number, "intent": p.intent, "path": p.path, "command": list(p.command), "status": p.status}
        for p in parts]}, sort_keys=True, ensure_ascii=False))


_ASK_EXAMPLES = (
    'python -m app ask "What is resistance?"',
    'python -m app ask "Open Calculator and calculate 123 × 456" --dry-run',
    "python -m app ask --audio request.wav --dry-run",
)

#: Flags of other commands `ask` refuses rather than ignores (P22-10).
_NOT_ASK_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name not in ("confirm", "dry_run", "audio", "listen")),
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _ask_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.ask", data_changed=False, retry_safe=True, next_options=_ASK_EXAMPLES,
    )


def _print_ask(text: str, speech, parts, interpreted: bool) -> None:
    """Section 227's structure, part by part, for reading."""
    if speech is not None:
        _print_speech(speech)
    print(f'Request     : "{_one_line(text)}" - {len(parts)} part(s)')
    for part in parts:
        heading = {"KNOWLEDGE": "knowledge", "ACTION": "application control / action",
                   "NOT_RUN": "not run"}[part.path]
        print(f"--- Part {part.number}: {part.intent} ({heading}) - {part.status}")
        if part.command:
            import subprocess

            print(f"Command     : python -m app {subprocess.list2cmdline(part.command)}")
        first, *rest = (part.answer or "").split("\n")
        print(f"ANSWER      : {first}")
        for line in rest:
            print(f"              {line}")
        for title, values in (("Basis", part.basis), ("Reasoning", part.reasoning), ("Calculation", part.calculation),
                              ("Actions", part.actions), ("Assumptions", part.assumptions), ("Sources", part.sources),
                              ("Missing", part.missing), ("Why required", part.why), ("Available", part.available),
                              ("Unavailable", part.unavailable), ("Conflict", part.conflicts),
                              ("Uncertain", part.uncertain),
                              ("Next steps", part.next_steps)):
            for index, value in enumerate(values):
                print(f"{(title if index == 0 else ''):<12}: {_one_line(str(value))}")
        print(f"Status      : {part.status}")


def _cmd_source(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    """A document's source file: its status, and deleting RUDRA's copy (ADR 0055; Part 7).

        python -m app source DOC-00000001
        python -m app source DOC-00000001 --delete-file
        python -m app source DOC-00000001 --delete-file --confirm

    Shows whether RUDRA's preserved copy of the document is present, its SHA-256, its
    source records (SOURCE_FILE_AVAILABLE or SOURCE_FILE_DELETED) and how much knowledge
    came from it. --delete-file deletes RUDRA's own copy to save storage - never your
    original elsewhere - and only with --confirm; without it, it says what would happen.
    The document record, its hash, the sources, the knowledge and its provenance are kept;
    the deletion is audited and verified. Extracting the same file again restores the copy.

    Exit codes: 0 shown, or deleted and verified; 6 not confirmed (nothing deleted) or the
    deletion could not be verified; 3 no such document; 2 invalid request; 5 the database
    cannot be used as it is; 70 anything unexpected.
    """
    from app.documents.lifecycle import SourceLifecycle
    from app.orchestration import to_json
    from app.storage import CODE_SCHEMA_VERSION, DatabaseRole, Repository, connect, database_path, schema_version

    _tolerate_unencodable_output()
    given = [flag for flag, name in _NOT_SOURCE_FLAGS if getattr(args, name) not in (None, False)]
    if args.scope.strip().lower() != "my-books":
        given.append("--scope")
    if given:
        raise _source_refusal(f"'source' does not take {', '.join(given)}.",
                              "Name a document; add --delete-file (and --confirm) to delete RUDRA's copy.")
    if args.target is None:
        raise _source_refusal("Name the document.", "For example DOC-00000001.")
    if args.confirm and not args.delete_file:
        raise _source_refusal("--confirm applies only with --delete-file.", "Showing a source changes nothing.")
    SourceLifecycle.check_identifier(args.target)
    paths = started.context.paths
    path = database_path(paths, DatabaseRole.KNOWLEDGE)
    writing = args.delete_file and args.confirm
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=not writing)
    try:
        if schema_version(connection) != CODE_SCHEMA_VERSION:
            raise StorageError.of(
                "The knowledge database's schema version is not the one this build reads; nothing is migrated "
                "here.", f"This build reads version {CODE_SCHEMA_VERSION}.", stage="cli.source",
                data_changed=False, retry_safe=True,
                next_options=("Migrate explicitly with: python -m app db  (for the live database make a fresh "
                              "D-15 backup first).",))
        lifecycle = SourceLifecycle(Repository(connection), paths.documents_dir)
        if not args.delete_file:
            status = lifecycle.status(args.target)
            if status is None:
                print(to_json({"document_id": args.target, "status": "NOT_FOUND"}) if args.json
                      else f"Source      : {args.target} - NOT_FOUND")
                return ExitCode.MISSING_INFORMATION
            print(to_json(status) if args.json else _source_text(status))
            return ExitCode.OK
        if lifecycle.status(args.target) is None:
            print(to_json({"document_id": args.target, "status": "NOT_FOUND"}) if args.json
                  else f"Source      : {args.target} - NOT_FOUND")
            return ExitCode.MISSING_INFORMATION
        deletion = lifecycle.delete_file(args.target, confirmed=args.confirm)
    finally:
        connection.close()
    if args.json:
        print(to_json(deletion))
    else:
        print(f"Deletion    : {deletion.outcome} - {deletion.message}")
        for text, ok in deletion.checks:
            print(f"  check     : {'yes' if ok else 'NO '} {text}")
        if deletion.audit_id:
            print(f"Audited     : {deletion.audit_id}")
        print(_source_text(deletion.after or deletion.before))
    return ExitCode.OK if deletion.outcome == "DELETED" else ExitCode.ACTION_FAILED


_SOURCE_EXAMPLES = (
    "python -m app source DOC-00000001",
    "python -m app source DOC-00000001 --delete-file --confirm",
)

#: Flags of other commands `source` refuses rather than ignores (ADR 0055).
_NOT_SOURCE_FLAGS = (
    *((flag, name) for flag, name in _NOT_REASON_FLAGS if name not in ("confirm", "delete_file")),
    ("--forward", "forward"), ("--input", "input"), ("--admit", "admit"), ("--assume", "assume"),
)


def _source_refusal(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="cli.source", data_changed=False, retry_safe=True, next_options=_SOURCE_EXAMPLES,
    )


def _source_text(status) -> str:
    lines = [f'Source      : {status.document_id} "{_one_line(status.original_filename)}" - {status.status}',
             f"File        : {status.stored_path} - {'present' if status.preserved_file_present else 'NOT PRESENT'}",
             f"SHA-256     : {status.file_hash}",
             f"Knowledge   : {status.knowledge} object(s) from this document"]
    lines += [f"  record    : {s.id} \"{_one_line(s.name)}\" {s.category}, {s.availability} ({s.status})"
              for s in status.sources]
    return "\n".join(lines)


def _cmd_version(started: StartupResult, args: argparse.Namespace) -> ExitCode:
    if args.json:
        print(
            json.dumps(
                {
                    "name": APP_NAME,
                    "full_name": APP_FULL_NAME,
                    "version": VERSION,
                    "phase": PHASE,
                    "python": sys.version.split()[0],
                },
                indent=2,
            )
        )
        return ExitCode.OK
    print(f"{APP_NAME} {VERSION}")
    print(APP_FULL_NAME)
    print(PHASE)
    print(f"Python {sys.version.split()[0]}")
    return ExitCode.OK


_COMMANDS = {
    "start": _cmd_start,
    "env": _cmd_env,
    "config": _cmd_config,
    "paths": _cmd_paths,
    "db": _cmd_db,
    "extract": _cmd_extract,
    "classify": _cmd_classify,
    "lookup": _cmd_lookup,
    "review": _cmd_review,
    "edition": _cmd_edition,
    "merge": _cmd_merge,
    "query": _cmd_query,
    "index": _cmd_index,
    "reason": _cmd_reason,
    "calculate": _cmd_calculate,
    "provenance": _cmd_provenance,
    "interpret": _cmd_interpret,
    "act": _cmd_act,
    "do": _cmd_do,
    "procedure": _cmd_procedure,
    "manual": _cmd_manual,
    "research": _cmd_research,
    "diagram": _cmd_diagram,
    "voice": _cmd_voice,
    "ask": _cmd_ask,
    "source": _cmd_source,
    "version": _cmd_version,
}


def _print_failure(report: FailureReport) -> None:
    """Print an actionable failure to stderr (Part 4 section 136)."""
    print(f"RUDRA could not continue. [{report.category}]", file=sys.stderr)
    print(report.to_text(), file=sys.stderr)


__all__ = ["ExitCode", "main"]
