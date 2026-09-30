# RUDRA — Limitations

RUDRA documents what it cannot do instead of hiding it behind confident language. Section 1
takes every limitation earlier versions of this document listed and says what became of it:
**SOLVED** (implemented and demonstrated by the automated tests), **PARTIALLY SOLVED** or
**REMAINS**. Section 2 labels every subsystem **IMPLEMENTED**, **PARTIALLY IMPLEMENTED** or
**NOT IMPLEMENTED**.

## 1. Former limitations and what became of them

| Former limitation | Status | What is true now |
|---|---|---|
| **No OCR** — scanned pages and images gave no knowledge | **SOLVED** | Pages without a text layer and PNG, JPEG and TIFF images are read by Windows' own OCR engine, offline, with no extra software. Native text is always preferred; OCR runs only where it is missing. Recognized text is stored as OCR, its line and word layout is kept beside the document, and every answer resting on it is marked *uncertain* with the reason. When no OCR language is installed, the import goes on and says which pages could not be read. Tested on generated PNG, JPEG and TIFF images and a PDF with a scanned page (`tests/integration/test_formats_end_to_end.py`). Limits: Windows reports no recognition confidence (none is invented); handwriting, rotated scans and equations inside scanned images are not reconstructed; accuracy depends on scan quality. See [OCR.md](OCR.md). |
| **PDF only** | **SOLVED** for the formats listed in [FORMATS.md](FORMATS.md) | PDF, Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), EPUB, HTML, Markdown, plain text, CSV, RTF, and PNG/JPEG/TIFF through OCR — each tested from import to answer. Legacy `.doc`, `.xls`, `.ppt`, JSON and XML are **refused** with an explanation, not read. |
| **Equations laid out over several lines in a PDF** were stored as fragments and never reconstructed | **PARTIALLY SOLVED** | Display equations are rebuilt from where the page places each glyph and draws each rule: fractions (nested too), powers, subscripts, radicals, integrals and sums with limits, derivatives, matrices, piecewise cases, aligned derivations and equation numbers — each tested on generated PDFs, including pages drawn inside form objects. An equation is rebuilt only when the geometry settles every glyph; otherwise the page's text is kept exactly and the equation is stored as *uncertain*, with the reasons and the original text kept beside the document. On a real 137-page engineering textbook about a third of the display-equation regions were rebuilt with certainty and spot checks against the printed pages matched; the rest stayed uncertain (glyph widths from partly embedded fonts, glyphs printed over one another, reading orders the text layer does not share). Mathematics inside sentences is not rebuilt. See [MATH.md](MATH.md). |
| **Formula display** showed matrices, multi-line alignments and other LaTeX as written | **SOLVED** | Matrices (`pmatrix`, `bmatrix`, `vmatrix`, …), `cases`, `aligned`/`gathered`, accents, binomials, `\tag` equation numbers and long equations broken across lines are typeset (`tests/unit/test_math_render.py`, `test_pdf_math.py`). |
| **Extraction is phrase-based** | **PARTIALLY SOLVED** | More ways of stating a definition are recognised (*"X denotes"*, *"By X we mean"*, *"We define X as"*, *"X stands for"*, glossary tables). Looser shapes (*"X is the …"*, *"Term – description"*) are stored as *uncertain*. What is phrased otherwise is still missed rather than guessed. |
| **Question interpretation is rule-based** | **PARTIALLY SOLVED** | Grammar version 2 understands explanations, *how does X work*, properties, comparing definitions, equations and variables of a topic, *where is X stated*, summaries, *according to my documents*, and plurals. Questions that name nothing (*"summarize this"*) are reported as incomplete. It is still deterministic: other wordings are *not understood*. Optional AI assistance can re-word such a question into one of RUDRA's own — it never answers it. |
| **No language model of any kind** | **CHANGED — optional** | RUDRA itself still uses none, and works fully without one. A user may connect one external provider (Anthropic, OpenAI, Google Gemini or Mistral) with their own key, off by default and enabled only with consent. It may re-word a question or word an explanation of statements already retrieved; unsupported sentences are removed, and nothing it says becomes knowledge. Tested with mocked providers only. See [AI_PRIVACY.md](AI_PRIVACY.md). |
| **Computer actions: no UI Automation** — typing, keys, clicks, scrolling and pasting were never verified | **PARTIALLY SOLVED** | Typed and pasted text is read back from the focused field: *verified* when a standard Windows edit field (never a password field) holds it, *failed* when the field can be read and does not. Other controls (browsers, modern apps), keys, clicks and scrolling remain *inconclusive* — never reported as success. There is still no general UI Automation. |
| **Deduplication** | **REMAINS** | Exact duplicates are linked; near-duplicates are recorded and never merged; meaning-level equivalence is not detected. |
| **Web pages** | **REMAINS** | One named page per request; JavaScript-built and login-protected pages are not read; no search provider. |
| **Windows only** | **REMAINS** | RUDRA runs on Windows 10/11, 64-bit; OCR, the credential store and computer control use Windows components. There is no macOS or Linux build. |
| **Installer not code-signed** | **REMAINS** | Windows SmartScreen may warn before the first start. |

## 2. Every subsystem, labelled

| Subsystem | Label | The limit |
|---|---|---|
| Ingestion, provenance, persistence, versioned extraction runs | IMPLEMENTED | The formats in [FORMATS.md](FORMATS.md) |
| OCR | IMPLEMENTED | Windows' engine and installed OCR languages; no confidence values; see §1 |
| PDF equation reconstruction | PARTIALLY IMPLEMENTED | Display equations only; see §1 |
| Source-file lifecycle | IMPLEMENTED | RUDRA's copy of a document can be deleted and restored; deleting knowledge itself is NOT IMPLEMENTED |
| Classification and hierarchy | IMPLEMENTED | Only stated relations and one inference rule (`RELATED_TO`) |
| Deduplication | PARTIALLY IMPLEMENTED | See §1 |
| Query engine and keyword index | IMPLEMENTED | Exact names after normalisation (regular plurals included); no fuzzy matching |
| Dependency-based reasoning | IMPLEMENTED | Over stored `REQUIRES` / `DEPENDS_ON` relationships only |
| Calculation | IMPLEMENTED | Exact rationals and SI dimensions; formulas are given or admitted, never chosen by RUDRA |
| Provenance and answer verification | IMPLEMENTED | — |
| Formula display | IMPLEMENTED | A subset of LaTeX (see [MATH.md](MATH.md)); anything else is shown as written |
| Knowledge-base backup and restore | IMPLEMENTED | One file per backup; no scheduled backups |
| Update notice | IMPLEMENTED | Notifies and links to the release page; never downloads or installs |
| Natural-language interpretation | PARTIALLY IMPLEMENTED | See §1 |
| Optional AI assistance | IMPLEMENTED (optional) | One provider at a time; needs the user's key and an Internet connection; mocked in tests, not tested against the live services |
| Action engine and computer control | PARTIALLY IMPLEMENTED | See §1 |
| Procedural memory | IMPLEMENTED | Documented procedures only; RUDRA infers none |
| Application documentation | PARTIALLY IMPLEMENTED | Layout-based; only *create a project* is matched to a documented workflow, and its menu steps are not executed |
| Controlled Internet research | PARTIALLY IMPLEMENTED | One named page per request; no search provider |
| Diagrams | PARTIALLY IMPLEMENTED | Structure diagrams from stored relationships; no schematics, charts or pictures |
| Voice | PARTIALLY IMPLEMENTED | English, through Windows' own speech engine; free dictation is much less reliable than commands |
| Full integration (`ask`) | IMPLEMENTED | Routes what the interpreter understands |
| Permission engine and audit | PARTIALLY IMPLEMENTED | Every action judged and logged; high-risk actions not enabled |
| Job system, crash recovery | PARTIALLY IMPLEMENTED | Transactional ingestion, migrations with automatic backups, backup and restore; no job queue |
| Health check and observability | PARTIALLY IMPLEMENTED | An environment report and logs at every start; no health service |
| Desktop window | PARTIALLY IMPLEMENTED | A compact assistant that expands to ten pages; no knowledge browser, action preview or audit history — those remain commands |

## 3. Performance

CPU only, single machine. A command starts in under a second; a question or calculation
adds tens to hundreds of milliseconds. Importing a PDF takes a few seconds for a short
document; reading equation layout adds roughly a tenth of a second per page, and OCR a
second or more per recognized page.

## 4. Platform

RUDRA is a Windows application (Windows 10/11, 64-bit), tested on Windows 11. The
installer is not code-signed.
