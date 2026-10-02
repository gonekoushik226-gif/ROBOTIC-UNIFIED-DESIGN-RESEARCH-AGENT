# RUDRA

**Robotic Unified Design Research Agent** — a local-first knowledge, reasoning and
calculation application for Windows.

RUDRA reads your own documents — PDFs, Word, PowerPoint, Excel, EPUB, web pages, plain
text and scanned images — extracts the knowledge they state (definitions, equations,
variables, properties and the relationships between concepts) and stores it in a
knowledge base on your computer. You ask questions in plain English and get answers built
only from that knowledge, each one traceable to the document and page it came from.

**Your documents are the authority.** RUDRA does not invent answers. An answer is a
statement found in your documents, a rule applied to stored relationships, or a real
calculation. When your documents do not hold the answer, RUDRA says so. Text that was
recognized by OCR, or an equation whose layout could not be read with certainty, is
marked *uncertain* in the answer, with the reason.

---

## Contents

**For users**

- [Install](#install)
- [What RUDRA does](#what-rudra-does)
- [Using RUDRA](#using-rudra)
- [Supported documents](#supported-documents)
- [Mathematics](#mathematics)
- [Backing up and moving your knowledge base](#backing-up-and-moving-your-knowledge-base)
- [Optional AI assistance](#optional-ai-assistance)
- [Privacy](#privacy)
- [Updates](#updates)
- [Working offline](#working-offline)
- [Where your data is stored](#where-your-data-is-stored)
- [Uninstalling](#uninstalling)
- [Limitations](#limitations)

**For developers**

- [Building from source](#building-from-source)
- [Releasing](#releasing)
- [License](#license)

---

# For users

You need only Windows. You do **not** need Git, GitHub, Python or any development tool:
download the installer, install, and use RUDRA.

## Install

**Requirements:** Windows 10 or 11, 64-bit (tested on Windows 11). No Python, LaTeX or
other software is needed.

1. Open the **[Releases](https://github.com/gonekoushik226-gif/ROBOTIC-UNIFIED-DESIGN-RESEARCH-AGENT/releases)** page.
2. Download `RUDRA-Setup-<version>-win64.exe` (and, to verify it, `SHA256SUMS.txt`).
3. Run the installer. It installs for your user account only and needs no administrator
   rights. Choose whether to add a desktop shortcut.
4. Start RUDRA from the Start Menu (or the desktop shortcut).

The installer is not code-signed, so Windows SmartScreen may warn the first time
(**More info → Run anyway**). [docs/INSTALL.md](docs/INSTALL.md) explains how to verify
the download, upgrade and uninstall.

## What RUDRA does

- **Your documents become a knowledge base.** RUDRA keeps a preserved copy of each
  document and extracts what it states, with the page (or slide, sheet or section) and the
  exact text each item came from.
- **Plain-English questions.** *"What is resistance?"*, *"Explain the operating principle
  of a MOSFET"*, *"What are the properties of capacitors according to my documents?"*,
  *"Which equations are given for resonance?"*, *"Where is Ohm's law stated?"*,
  *"Compare the definitions of power"*, *"Summarize capacitance"* — interpreted by
  deterministic rules and answered from your knowledge base.
- **Clean answers, sources on request.** The answer is shown by itself. **View Sources**
  opens where it came from: the document, the page, the quoted text, the file's
  fingerprint check and the knowledge items the answer rests on, with a button to open
  the source document at that page.
- **A list of what it knows.** The **Knowledge** page shows every concept, definition,
  equation, variable and more that was stored from your documents, the page and quoted text
  behind each, how sure RUDRA is of it — and what was found but *not* stored, and why.
- **Textbook mathematics**, typeset from a structured, searchable notation (see
  [Mathematics](#mathematics)).
- **Calculation that chooses its own equations.** Ask *"Calculate the current when
  V = 10 V and R = 5 Ω"* and RUDRA finds the equations your documents state, chains them in
  the right order (turning an equation around where needed), calculates exactly with SI units
  and dimension checks, cross-checks other routes, verifies the result independently and
  shows every step and its source — through Ask, the same as any other question, not a
  separate page.
- **Speak instead of typing.** A microphone button beside Ask dictates your question. Speech
  is recognized on your computer by an open-source Whisper model - offline, no account,
  nothing recorded or sent anywhere - and you can edit the words before sending. See
  [docs/VOICE.md](docs/VOICE.md).
- **Reasoning over stored relationships**, conflict detection between sources, and a
  per-item provenance trace that re-checks the quoted text and the preserved file.
- **Backup, restore and uninstall**, from one Settings page.
- **Optional AI assistance**, off unless you turn it on with your own key (see
  [Optional AI assistance](#optional-ai-assistance)).

RUDRA also includes permission-checked computer actions, documented procedures,
application manuals, single-page web research on request, structure diagrams and Windows
speech input. Several of these are only partly implemented; see
[Limitations](#limitations).

## Using RUDRA

### First start

RUDRA opens as a small assistant window: a status line, the latest answer and one input
box. On first start it creates its data folder and, while it is empty, tells you to add a
document. **Full window** opens the full interface: **Ask**, **Add document**, **Knowledge**
and **Settings** for everyday use, with Status, Lookup, Provenance, Command and Help grouped
as *Advanced* for when you want the detail underneath.

### Adding a document

**Full window → Add document**, choose one or more files with **Browse…**, then select
**Add document(s)**. Browse again to add more files to the selection; **Remove selected**
and **Clear list** change it before processing. RUDRA copies each document into its own
store (your originals are never changed), reads it, stores what it states and makes it
searchable, so you can ask about it straight away. Each document is processed separately:
if one cannot be read, RUDRA continues with the others and leaves the failed file selected
so you can retry it. The results explain what was stored (for example "66 definitions, 524
equations"), what was *already known* from other evidence, and what was found but *not
stored* and why. **See what was stored** opens the Knowledge page for that document; the
technical detail - document ID, page counts, issues - is one click away behind **Details**.

### What RUDRA knows

**Knowledge** lists, for each document, what was stored, linked and not stored, and lets you
browse every concept, definition, equation, variable, unit, property, rule, relationship,
example and procedure with the document and page it came from, the quoted text and how sure
RUDRA is ("as printed", or *uncertain* with the reason). For equations it also says whether a
calculation can use them, and why not when it cannot (an integral, a derivative, a fraction a
PDF flattened). Worked numeric examples such as "V = 10 V" and question-bank material are
deliberately not stored as knowledge.

### Asking questions, including calculations

Type a question in the assistant's input box or on the **Ask** page - or click the
microphone button beside it, speak (it stops listening when you stop talking, or click it
again), and edit the recognized words before sending - a reading the recognizer was unsure of
is flagged **CHECK THE WORDS**, and names it gets wrong can be added under Settings > Voice.
The answer appears by itself. Lines resting on uncertain recognition are marked **Uncertain**
with the reason, for example *"recognized by OCR from a scanned page; compare it with the
source"*. A question that names nothing (*"summarize this section"*) is answered with what
is missing, never with a guess.

There is no separate Calculate page: ask RUDRA to calculate the same way you would ask
anything else. State what you want and what you know — *"Calculate the current when V = 10 V
and R = 5 Ω"*, *"What is the power if the voltage is 10 volts and the current is 2 amps?"*,
*"If R1 = 10 kΩ and R2 = 4.7 kΩ, find the equivalent resistance"* — and RUDRA chooses the
equations itself from your documents: one, or several in the order their values flow
(finding Rtotal from R1 and R2 before using it in a second equation), turning an equation
around where the question needs it (`V = I R` also gives `I = V / R`). It shows the route,
each substitution, the independent check and the source of every equation.

Name a quantity by the **symbol** your documents use (`V`, `Rtotal`) or by a **name they
explain** ("where V is the voltage" lets you say "the voltage"). If a document never says
what a symbol means, RUDRA says so rather than guess. If the stored equations cannot give the
answer it names what is missing; if two stored equations give different values it shows both
and chooses neither; an equation read from a PDF page's text layer is used only when no exact
one will do, and is marked *uncertain*. If you state the formulas yourself (*"Calculate I given
I = V / R, V = 10 V and R = 5 Ω"*), those are the formulas used.

### Viewing sources

Each answer has a **View Sources** button. It reveals the documents and pages the answer
came from with the quoted text, the knowledge items and concepts it rests on, the full
provenance trace (the preserved file's SHA-256 checked again, the page, the exact text
span, the extraction run) and conflicting claims. **Open source document** opens a
read-only copy of the document. **Hide Sources** returns to the answer alone; provenance
is always kept.

## Supported documents

| Format | Read as |
|---|---|
| PDF | Native text per page; pages without text are read by OCR; display equations rebuilt from the page layout |
| Word (`.docx`) | Headings, paragraphs, lists, tables, captions, footnotes and Office Math equations |
| PowerPoint (`.pptx`) | One unit per slide: title, text, tables, equations and speaker notes |
| Excel (`.xlsx`) | Each sheet's rows |
| EPUB | Chapters, headings, lists, tables and MathML equations |
| HTML (`.html`, `.htm`) | Headings, paragraphs, lists, tables and MathML equations |
| Markdown, plain text, CSV, RTF | Text, headings and tables where the format has them |
| PNG, JPEG, TIFF images | Windows OCR (text marked as recognized, answers marked uncertain) |

Legacy Office files (`.doc`, `.xls`, `.ppt`), JSON and XML are refused with an
explanation — save legacy files in the current format first. Details:
[docs/FORMATS.md](docs/FORMATS.md) and [docs/OCR.md](docs/OCR.md).

## Mathematics

Equations are stored as structured, searchable text in a subset of LaTeX
(`\frac{R}{L}`, `x^{2}`, `\sqrt{2gh}`, `\int_{0}^{T} p\,dt`, `\begin{pmatrix}…`) and
displayed as a textbook would set them: stacked and nested fractions, powers and
subscripts, radicals, integrals and sums with limits, matrices, piecewise cases, aligned
multi-line derivations, accents, binomials, equation numbers and long equations broken
across lines. Equations come from Word (Office Math), web pages and EPUB (MathML), and
from PDF pages, where RUDRA rebuilds display equations from where the page places each
glyph. A PDF equation whose layout is ambiguous is kept as the page printed it and marked
uncertain. A calculation is shown as a worked solution: the result on its own card, the
values given, and each step as an equation and the equation with its values put in (fractions
stacked, units upright, subscripts lowered), with the check on the result. See
[docs/MATH.md](docs/MATH.md).

## Backing up and moving your knowledge base

**Full window → Settings → Back up your knowledge** writes one
`RUDRA-knowledge-<date>.rudrabackup` file — to a USB drive, an external disk or any
folder. It holds a consistent snapshot of the knowledge database, the preserved copies of
your documents, OCR and equation-layout records, and a manifest with version numbers and
a SHA-256 checksum of every file. It never holds the program, logs, settings, AI keys or
paths of your computer.

**Settings → Restore from a backup** restores it on the same or another computer. The file
is checked completely before anything changes; an existing knowledge base is only replaced
after you confirm, and it is moved aside, not deleted. A backup from a newer RUDRA is
refused; one from an older RUDRA is upgraded. See
[docs/BACKUP_FORMAT.md](docs/BACKUP_FORMAT.md).

## Optional AI assistance

RUDRA works fully without any AI service. If you want, you can connect **one** external
provider of your choice — Anthropic, OpenAI, Google Gemini or Mistral — with **your own
API key**, from **Settings → Optional AI assistance → Manage AI assistance…**. It is off
by default and is only turned on after you read what will be sent and agree.

- The key is stored by the Windows Credential Manager, never in a RUDRA file, log or
  backup.
- **Interpret** sends only your question's text, so the provider can turn an unusual
  wording into one of RUDRA's own questions; RUDRA then answers from your documents.
- **Explain** sends only your question and the statements already shown in the answer.
  Every sentence of the explanation must cite those statements; sentences that do not, or
  that contain a number the statements do not, are removed. The explanation is labelled as
  wording by the provider, never as a source, and nothing it says is stored as knowledge.

Details: [docs/AI_PRIVACY.md](docs/AI_PRIVACY.md).

## Privacy

- Everything RUDRA knows is stored in your own user profile. There is no account, no
  cloud storage, no telemetry and no analytics.
- RUDRA contacts the Internet only for: **the update check** (one anonymous request for
  the public release information of this repository; it can be turned off), **web research
  you ask for**, one named page at a time, and **AI assistance you turned on**.
- Imported documents are copied into RUDRA's own store; your originals are never modified
  or deleted.
- Your voice stays on your computer. Speech is recognized offline; microphone audio is held in
  memory only while it is being recognized, is never written to disk and is never sent
  anywhere, and RUDRA listens only after you click the button.

## Updates

RUDRA checks GitHub for a newer stable release at most once a day. Drafts and
pre-releases are ignored. A notice with a **Download** button opens the release page in
your browser; RUDRA never downloads or installs anything itself. **Settings → Check for
updates** checks immediately; **Check automatically** turns the daily check off. To
update, run the newer installer — your knowledge base is kept.

## Working offline

Everything except the update check, web research and optional AI assistance works
without an Internet connection: importing (OCR included), questions, calculation,
provenance, formula display, speaking to RUDRA, backup and restore. An update check that cannot reach GitHub
fails silently.

## Where your data is stored

| What | Where |
|---|---|
| The program | `%LOCALAPPDATA%\Programs\RUDRA\` — replaced by upgrades |
| Your data | `%LOCALAPPDATA%\RUDRA\` |
| &nbsp;&nbsp;Knowledge database | `%LOCALAPPDATA%\RUDRA\data\database\knowledge.db` |
| &nbsp;&nbsp;Preserved documents | `%LOCALAPPDATA%\RUDRA\data\documents\` |
| &nbsp;&nbsp;OCR and equation-layout records | `%LOCALAPPDATA%\RUDRA\data\extracted\` |
| &nbsp;&nbsp;Automatic database backups | `%LOCALAPPDATA%\RUDRA\data\backups\` (before every schema upgrade and every restore) |
| &nbsp;&nbsp;Search index, caches | `%LOCALAPPDATA%\RUDRA\data\indexes\`, `data\cache\` (rebuildable) |
| &nbsp;&nbsp;Settings and logs | `%LOCALAPPDATA%\RUDRA\config\`, `%LOCALAPPDATA%\RUDRA\logs\` |
| AI keys (if you add one) | Windows Credential Manager, entries `RUDRA/ai/<provider>` |

Do not copy or edit `knowledge.db` while RUDRA is running — use **Settings → Back up your
knowledge** instead.

## Uninstalling

Use **Settings → Uninstall RUDRA** in the window (it starts the same uninstaller as the
others), **Settings → Apps → Installed apps** in Windows, or *Uninstall RUDRA* in the Start
Menu. This removes the program and its shortcuts. **Your data folder is kept**, so
reinstalling continues where you left off; delete `%LOCALAPPDATA%\RUDRA` yourself to
remove your knowledge base too.

## Limitations

[docs/LIMITATIONS.md](docs/LIMITATIONS.md) lists every subsystem with its status. The
most important limits:

- **Extraction is phrase-based.** RUDRA finds statements phrased as definitions,
  equations, variables with legends, properties and a fixed set of relation phrases.
  What is phrased otherwise is missed rather than guessed.
- **PDF equations inside sentences** are kept as the text layer gives them and marked
  uncertain; only display equations are rebuilt from the layout, and only where the
  geometry settles every glyph.
- **OCR** uses Windows' own engine, which reports no confidence; everything it reads is
  treated as uncertain. Handwriting and equations in scanned images are not reconstructed.
- **Calculation uses only what your documents state.** RUDRA chooses among your stored
  equations but never invents one; it can use equations that read as plain arithmetic (in a
  real engineering textbook, about a third of the equations found), not calculus or
  simultaneous systems, and says what is missing when they do not suffice.
- **The question interpreter is rule-based.** A question outside its patterns is reported
  as not understood (optional AI can re-word it, but never answers it).
- **Computer actions** verify typed and pasted text only in standard Windows edit fields;
  keys, clicks and scrolling are reported as not verifiable.
- **Windows only**, 64-bit.

## The command line

The installer also adds **RUDRA Command Line** (`RUDRA-CLI.exe`) to the Start Menu. It
runs the same commands the window runs:

```powershell
RUDRA-CLI.exe --help
RUDRA-CLI.exe extract "C:\path\to\book.pdf"
RUDRA-CLI.exe ask "What is resistance?"
RUDRA-CLI.exe provenance K-00000001
RUDRA-CLI.exe calculate I --formula "I = V / R" --input "V=10 V" --input "R=5 Ω"
```

Add `--json` for machine-readable output.

---

# For developers

Everything below is for building RUDRA from its source code. Users of the Windows
application do not need any of it.

## Building from source

Requirements: Windows, Python 3.14 (64-bit), and for the installer
[Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
git clone https://github.com/gonekoushik226-gif/ROBOTIC-UNIFIED-DESIGN-RESEARCH-AGENT.git
cd ROBOTIC-UNIFIED-DESIGN-RESEARCH-AGENT
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements\build.txt
.venv\Scripts\python.exe -m pytest -p no:cacheprovider    # the test suite
.venv\Scripts\python.exe -m app.ui.gui                     # the window, from source
.venv\Scripts\python.exe windows\build.py --installer      # dist\RUDRA\ and dist\installer\
```

Run from source, RUDRA keeps its data in the repository folder (`config\`, `data\`,
`logs\`, all ignored by Git); `--project-root PATH` points it elsewhere. The only
third-party Python dependency is `pypdf`; everything else — the window, OCR (through
Windows), the formula renderer, the microphone, the backup format, the update check and the
optional AI connections — uses the Python standard library and Windows' own components. The
one other addition is the offline speech recognizer (whisper.cpp, an OpenAI Whisper model and
the Silero voice-activity model, all MIT), fetched with pinned SHA-256 hashes by
`python windows\fetch_speech.py` into `speech\` and bundled by the build.
[docs/BUILDING.md](docs/BUILDING.md) describes the build, the package checks and the
installer test; [docs/DESIGN.md](docs/DESIGN.md) gives an overview of the architecture.

## Releasing

Releases are built by the GitHub Actions **Release** workflow from a version tag, as a
draft for review. See [docs/RELEASING.md](docs/RELEASING.md).

## License

RUDRA is released under the [MIT License](LICENSE). The Windows application bundles
third-party components under their own licenses, listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
