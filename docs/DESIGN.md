# RUDRA design overview

## Principles

- **Knowledge comes from sources, not from generation.** Every stored item records the
  document, page, text span and extraction run it came from. Answers are assembled from
  stored items, rules over stored relationships, or explicit calculations; what is not
  supported is answered *Unknown*.
- **Local first.** One SQLite database and a document store in the user's data folder.
  No service, account or network connection is needed for any core function.
- **Deterministic.** Extraction, interpretation and reasoning are rule-based; the same
  input gives the same result. Optional AI assistance (off by default) can only re-word a
  question into one of RUDRA's own or word an explanation of statements already
  retrieved; it never adds knowledge.
- **Doubt is kept, not hidden.** Text recognized by OCR, loosely phrased definitions and
  PDF equations whose layout left a doubt are stored as *uncertain*, and answers say why.
- **Minimal dependencies.** The standard library plus `pypdf`.

## Layers

| Layer | Packages | Role |
|---|---|---|
| Foundation | `app.core`, `app.config`, `app.version` | Configuration, paths, logging, errors, startup |
| Model | `app.models` | Dataclasses for documents, knowledge objects, concepts, relationships, equations, provenance and more; pure data |
| Storage | `app.storage` | The only SQL: connection settings, forward-only migrations with automatic backups, repository, derived keyword index, backup files |
| Ingestion | `app.documents`, `app.extraction`, `app.manuals` | Format detection and readers for every supported format, PDF parsing (through one adapter), OCR through Windows, PDF equation reconstruction from glyph layout (`pdfmath`), Office Math and MathML conversion, document structure, phrase-based extraction, versioned extraction runs |
| Knowledge | `app.knowledge`, `app.classification`, `app.deduplication` | Concepts, hierarchy, duplicates and conflicts |
| Answering | `app.query`, `app.reasoning`, `app.calculation`, `app.provenance`, `app.verification` | Retrieval, dependency reasoning, exact calculation with units, provenance traces and answer checks |
| Interaction | `app.nlu`, `app.orchestration` | Rule-based interpretation of requests and the `ask` pipeline that routes each part |
| Actions | `app.actions`, `app.computer`, `app.security`, `app.procedures`, `app.applications` | Permission-checked actions, a simulated computer for dry runs, documented procedures |
| Other | `app.internet`, `app.diagrams`, `app.voice`, `app.updates`, `app.providers` | One-page web research, SVG structure diagrams, Windows speech, the update notice, optional AI assistance (provider connections, consent, the credential store, grounding checks) |
| Interfaces | `app.ui.cli`, `app.ui.gui` | The command line, and the desktop window that runs the command line's own commands in-process |

Import boundaries between these packages are enforced by
`tests/unit/test_import_boundaries.py`.

## The desktop window

The window adds no knowledge logic of its own: each action is a command line run through
`app.ui.cli.main` on a worker thread, with its output captured. Two parts are presentation
only:

- **The answer view** (`app/ui/gui/answerview.py`) reads `ask --json` and shows each
  part's answer first; the part's provenance (sources, basis, conflicts, the command that
  answered it and a `provenance` trace of each knowledge item) is shown only after
  **View Sources**.
- **The formula renderer** (`app/ui/gui/mathrender.py`) parses a stored formula's text
  into a small tree, lays it out with the proportions of printed mathematics and draws it
  on a Tk canvas. The stored text is never changed.

## Data location

From source, the project folder is the repository. The packaged program keeps its data in
`%LOCALAPPDATA%\RUDRA` (`app.core.paths.packaged_project_root`), apart from its program
folder, unless a `portable.txt` file or an existing `data` folder sits beside the
executable.

## Versions

Four independent version numbers: the application (`app/version.py`), the database schema
(the highest migration in `app/storage/schema/`), the configuration file
(`config_schema_version`) and the backup format (`app/storage/archive.py`).

## References in the code

Comments in the code cite the project's internal design records by number — decision
records (*ADR nnnn*), development phases and sections of the original requirements
specification. Those records are working documents and are not part of this repository;
the comments state the reasoning they need where it matters.
