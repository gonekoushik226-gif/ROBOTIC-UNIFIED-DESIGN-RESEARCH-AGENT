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
| Answering | `app.query`, `app.reasoning`, `app.calculation`, `app.solving`, `app.inventory`, `app.provenance`, `app.verification` | Retrieval, dependency reasoning, exact calculation with units, choosing the stored equations for a calculation, the per-document inventory of what was stored, provenance traces and answer checks |
| Interaction | `app.nlu`, `app.orchestration` | Rule-based interpretation of requests and the `ask` pipeline that routes each part |
| Actions | `app.actions`, `app.computer`, `app.security`, `app.procedures`, `app.applications` | Permission-checked actions, a simulated computer for dry runs, documented procedures |
| Other | `app.internet`, `app.diagrams`, `app.voice`, `app.updates`, `app.providers` | One-page web research, SVG structure diagrams, offline speech recognition (Whisper through whisper.cpp) with Windows speech output, the update notice, optional AI assistance (provider connections, consent, the credential store, grounding checks) |
| Interfaces | `app.ui.cli`, `app.ui.gui` | The command line, and the desktop window that runs the command line's own commands in-process |

Import boundaries between these packages are enforced by
`tests/unit/test_import_boundaries.py`.

## Choosing equations for a calculation

`app.solving` answers *"calculate X given these values"* from stored equations, without a
language model, without embeddings and without any equation written into the program:

1. **Read.** Each stored equation is cleaned into plain arithmetic where that can be done
   without guessing (LaTeX to text, chained equalities split, an unambiguous implicit
   product split, units dropped); anything else is kept out, with the reason.
2. **Turn around.** An equation is also offered rearranged for each of its other
   quantities, by undoing one operation at a time; what cannot be rearranged exactly is
   refused rather than approximated.
3. **Search.** A bounded search from the asked-for quantity back to the given values finds
   the equations that connect them and the order in which the values flow. A stored
   statement is used once per route, equations the source states exactly are preferred over
   ones read from a PDF's text layer, and fewer equations come before more.
4. **Bind names.** A word such as *current* is matched to a symbol only through a document's
   own statement of what the symbol stands for.
5. **Decide.** Every route is evaluated with exact rationals and SI dimensions. A route
   whose dimensions do not fit is rejected; routes that give the same value confirm one
   another; routes that give different values are all shown and none is chosen; no route
   means the answer names what is missing. The chosen route is then checked again by
   `app.verification`.

`app.inventory` reads the same stored equations to say, for each, whether a calculation
can use it. Everything is read-only.

## The desktop window

The window adds no knowledge logic of its own: each action is a command line run through
`app.ui.cli.main` on a worker thread, with its output captured. Two parts are presentation
only:

- **The answer view** (`app/ui/gui/answerview.py`) reads `ask --json` and shows each
  part's answer first; the part's provenance (sources, basis, conflicts, the command that
  answered it and a `provenance` trace of each knowledge item) is shown only after
  **View Sources**.
- **The Knowledge page** lists the `inventory` command's JSON: per document what was
  stored, linked and not stored, and per item its source and quote.
- **The formula renderer** (`app/ui/gui/mathrender.py`) parses a stored formula's text
  into a small tree, lays it out with the proportions of printed mathematics and draws it
  on a Tk canvas. The stored text is never changed.

## Open-source review

RUDRA keeps its dependency footprint small on purpose (Windows 11, an Intel i3-1215U, 8 GB of
RAM, no GPU, no network required). A component was replaced only where a verified,
material benefit justified the cost; everything else was kept. The review below was done on
2026-10-01 against the upstream repositories and model cards; licenses are the ones those
projects publish for the versions named.

| Area | Verdict | What RUDRA uses | Considered, and why not |
|---|---|---|---|
| Speech to text | **REPLACED** | whisper.cpp v1.9.4 (MIT) running OpenAI Whisper small.en (MIT) with Silero VAD v5.1.2 (MIT), offline - [VOICE.md](VOICE.md) | Windows' built-in dictation was the old engine: 38% of the words wrong on the test set. Vosk (Apache-2.0) publishes 9.85% word error for its 40 MB English model and 5.69% for its 1.8 GB one on LibriSpeech test-clean (not measured here). sherpa-onnx (Apache-2.0) is a Python package plus a separate native core package, with pretrained models under their own licenses (NVIDIA's Parakeet is CC-BY-4.0). faster-whisper (MIT) runs the same Whisper models through CTranslate2, onnxruntime, PyAV, `huggingface-hub` and `tokenizers` - far more to bundle for no gain. The `openai-whisper` package (MIT) needs PyTorch. Moonshine (MIT) is a young Python package that requires NumPy, `sounddevice` and others, and was not measured here. Whisper through whisper.cpp is one small program and one model file, run as a separate process that gives its memory back when it ends |
| Microphone capture | New, no dependency | Windows' `winmm` through `ctypes` | `sounddevice` / PyAudio (a native PortAudio library to bundle for what `waveIn` already does) |
| Speech output | **KEPT** | Windows' `System.Speech` voices, used only by `voice --say` | Piper: the MIT repository `rhasspy/piper` is archived, its maintained successor `OHF-Voice/piper1-gpl` is GPL-3.0. Kokoro (Apache-2.0 weights) needs the GPL-3.0 espeak-ng phonemizer and a neural runtime. Nothing in the window speaks, so a better voice would improve nothing a user hears |
| Showing mathematics | **KEPT, improved** | RUDRA's own typesetter on a Tk canvas with Cambria Math (`mathrender.py`); calculations are now set as worked solutions | KaTeX (MIT) and MathJax (Apache-2.0) need a JavaScript engine or an embedded browser; matplotlib's mathtext draws bitmaps and brings NumPy and Pillow; `latex2mathml`, `pylatexenc` and `unicodeit` convert notation but draw nothing; a TeX distribution is gigabytes. The defect was how the result was *presented* (linear text, raw LaTeX), not the typesetter |
| Units | **KEPT** | RUDRA's closed SI unit table with exact rational arithmetic (ADR 0042, D-07) | `pint` (BSD-3-Clause): float-based by default and an open-ended unit grammar, where RUDRA's rule is a closed table that refuses what it does not know |
| PDF text | **KEPT** | `pypdf` (BSD-3-Clause) | PyMuPDF is AGPL-3.0; `pdfminer.six` (MIT) gives no more than pypdf's glyph visitor already supplies for equation reconstruction; `pypdfium2` adds a native PDFium for rendering that Windows' own renderer already does |
| OCR | **KEPT** | Windows.Media.Ocr (offline, nothing to bundle) | Tesseract 5 (Apache-2.0) would add per-word confidence but needs a bundled program and language data, with no verified accuracy gain on clean print; RapidOCR / PaddleOCR (Apache-2.0) need a neural runtime of 100 MB or more; Surya is GPL-3.0 with restricted model terms. The one real gain on offer - confidence values - is the place to start if OCR becomes the priority |
| Office, EPUB, HTML readers | **KEPT** | The Python standard library (`zipfile`, `xml.etree`, `html.parser`), with hard limits | `python-docx` / `openpyxl` (MIT) would add dependencies and read less than RUDRA's safety limits allow it to |
| Search | **KEPT** | SQLite FTS5 over a rebuildable `index.db`; `knowledge.db` stays authoritative | Elasticsearch, vector databases and embedding models: heavyweight, and RUDRA must not depend on similarity alone |
| Window | **KEPT** | Tkinter | No toolkit change was warranted |
| Packaging | **KEPT** | PyInstaller (GPL-2.0-or-later with the Bootloader Exception), Inno Setup | Nuitka is AGPL-3.0 |

The only new runtime pieces are therefore the speech recogniser's three files and the
standard-library `ctypes` calls that capture the microphone; no new Python package was
added. [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) records versions, licenses and
where each is used.

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
