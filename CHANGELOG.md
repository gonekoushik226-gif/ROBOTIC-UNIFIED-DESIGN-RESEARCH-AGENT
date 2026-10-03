# Changelog

## 1.2.0

### Fixed

- **Ask offers question patterns.** On the Ask page, choose “What is…?”, “How does it
  work?”, “How is it used?” or “What are its applications?” and enter a topic. RUDRA fills
  in the question and runs it through the same document-grounded Ask flow.
- Application templates request only applications recorded in the knowledge base. If a
  source-stated application link is missing, RUDRA says so and labels any keyword hits as
  mentions rather than presenting them as applications.
- **Ask understands common explanation phrasing.** “Explain how a MOSFET works,” “State
  Ohm's law,” “What does Ohm's law state?” and application/usage questions now look up the
  named topic instead of treating the whole sentence as its name or rejecting it. Answers
  still come only from the user's stored documents, with their original citations.

## 1.1.0

### Added

- **Add several documents in one operation.** Choose multiple files at once or add more
  files to the selection, remove individual choices, then start one batch. RUDRA reports
  each document separately, continues past a file it cannot read, and keeps failed files
  selected so they can be retried. The search index and knowledge inventory are refreshed
  after the batch.

## 1.0.0

Initial public release — 2026-10-02.

A pass over the whole journey - install, add a document, ask, calculate, back up, restore,
speak, restart, uninstall - repairing what a careful user would trip over.

### Added

- **Calculations are shown as worked solutions.** The result sits on a card of its own
  (*I = 0.4 A*, unit upright); under it, what you gave, then each step as an equation and the
  same equation with its values put in - fractions stacked, subscripts lowered (*R*<sub>total</sub>),
  large numbers grouped (*20 000 Ω*) - and the independent check. Equations that disagree are
  set side by side, none chosen; the formula an unanswerable calculation was missing is
  typeset. LaTeX in a quotation from your sources is typeset where it stands, and
  `\( \)`, `\[ \]`, `$$` and `$` are never shown as delimiters. The plain lines are unchanged
  in the command line, the JSON and Copy. See [docs/MATH.md](docs/MATH.md).
- **Offline speech recognition with Whisper.** The microphone button now uses OpenAI's
  Whisper model (small.en, MIT) run by whisper.cpp (MIT) with the Silero voice-activity
  model (MIT), entirely on your computer - no cloud, no account, nothing stored. It replaces
  Windows' built-in dictation, which turned *"My name is Kaushik"* into *"Like name is go
  seek"*: on a 96-recording test set the share of words wrong fell from 38.2% to 2.6% (see
  [docs/VOICE.md](docs/VOICE.md) for the method, the figures and their limits). Listening
  ends by itself when you stop talking or when you click; the button and the status line
  say whether RUDRA is listening or working out the words; a reading the model was unsure
  of is flagged **CHECK THE WORDS**; silence gives nothing, not invented words.
- **Words to spell as written** (Settings > Voice): names and terms speech gets wrong, kept
  on your computer and given to the recognizer as context.
- `python windows\fetch_speech.py` puts the speech files (pinned by SHA-256) beside the
  program for a source checkout and for the build; the installer includes them.

- **RUDRA chooses the equations for a calculation.** Ask *"Calculate the current when
  V = 10 V and R = 5 ohms"* or *"Find the output voltage if Vin = 12 V, R1 = 10 kΩ and
  R2 = 20 kΩ"* and RUDRA works out what is asked, what you gave, and which of your stored
  equations connect the two - turning them around where needed and chaining two, three or
  more in the right order, with every intermediate value shown and checked independently.
  If the documents do not suffice it says exactly what is missing; it never invents a
  formula. Equations that give different values are both shown and none is chosen; routes
  that agree confirm each other; a route that mixes incompatible dimensions is rejected.
  Also available as the `solve` command.
- **Knowledge inventory.** A new Knowledge page (and the `inventory` command) lists, for
  every document, what was stored, what was linked to knowledge already held and what was
  *not* stored and why - with the page and exact quote behind every item and whether each
  equation can be calculated with. Adding a document ends with the same plain summary, and
  says so honestly when a document gave nothing to store.
- **Ask finds what is only mentioned.** A question about something no document defines
  (*"What is the gain?"*) now answers with the passages that mention it, labelled as
  mentions rather than definitions, instead of "unknown".
- **The search index looks after itself.** Adding a document makes it searchable at once,
  and asking rebuilds a missing index without being told to.

### Changed

- **Ask does calculation too.** The standalone Calculate page is gone; ask RUDRA to
  calculate the same way you ask anything else, in plain English, or state the formula
  yourself (*"Calculate I given I = V / R, V = 10 V and R = 5 Ω."*). The calculation engine
  and its command are unchanged.
- **The window opens on Ask**, with a welcome that says what RUDRA holds and what to do
  next. Add document, Knowledge and Settings are the everyday pages; Status, Lookup,
  Provenance, Command and Help are grouped as Advanced. Pages that are taller than the window
  scroll instead of being cut off.
- **Add document** ends with a short, plain-language result instead of the command's
  technical report (still one click away behind **Details**). A file that cannot be added
  says what to do about it (for example, save an old `.doc` file as `.docx`).
- **Settings** is the one place for maintenance: checking for updates, testing the
  microphone, backing up and restoring your knowledge, managing optional AI assistance and
  uninstalling RUDRA through the real uninstaller.
- Wording throughout the window and the command line is for people, not for the project's
  development history.

### Fixed

- **A calculation's steps were hard to read** (linear text such as `I = 12 V ÷ 30 Ω`, and a
  stored equation shown in View Sources as `f = \frac{1}{T}`). See *Added*.
- **Speech recognition was unusable** for ordinary English and names. See *Added*.
- The Ask page's example *"Which equations are stored for resistance?"* was not a question
  RUDRA understands; it now reads *"Which equations are given for resistance?"*.

- **The microphone button no longer flashes a Windows console window.** Speech recognition
  and synthesis start their helper hidden. A missing microphone, silence, a cancelled
  listen and a failed attempt each give a plain explanation and can be retried.
- `--help` no longer crashes on a console that cannot display every character.
- Two stored equations that give different values for unitless numbers are shown as a
  conflict ("V = 10; V = 7"), not as a bare list.
- A document whose result pane was left on screen no longer squeezes the Knowledge list.
- pypdf's own "fontTools is required..." warning no longer appears while reading a PDF's
  text. Investigated and found to make no measured difference on the project's reference
  document (RUDRA does not bundle the optional `fontTools` package); see
  [docs/FORMATS.md](docs/FORMATS.md).

### Earlier implementation (0.1.0 draft; never released)

This functionality was present in the unpublished 0.1.0 draft and is included in RUDRA 1.0.0.

### Documents and knowledge

- Import PDF, Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), EPUB, HTML,
  Markdown, plain text, CSV and RTF documents, and PNG, JPEG and TIFF images, into a local
  knowledge base: definitions, equations, variables, properties, rules, procedures and
  relationships between concepts, each with its document, location and exact text.
  Formats are identified by their content; legacy `.doc`/`.xls`/`.ppt`, JSON and XML are
  refused with an explanation.
- Scanned PDF pages and images are read with Windows' own OCR, offline. Native text is
  always preferred; recognized text is stored as OCR and answered as uncertain.
- More ways of stating a definition are recognised; looser sentence shapes are stored as
  uncertain.

### Questions and answers

- Ask in plain English: definitions, explanations (*how does X work*), properties,
  comparisons of definitions, equations and variables of a topic, *where is X stated*,
  summaries, and *according to my documents*. Questions that name nothing are reported as
  incomplete; answers come only from the knowledge base, stored relationships and
  explicit calculations, and say *Unknown* when nothing supports them.
- Answers are shown by themselves; lines resting on uncertain recognition are marked with
  the reason. **View Sources** reveals the documents, pages, quoted text, the knowledge
  items used and a verified provenance trace, and opens the source document.

### Mathematics

- Equations are stored in a structured, searchable notation and typeset as in a
  textbook: nested fractions, powers, subscripts, radicals, integrals and sums with
  limits, matrices, piecewise cases, aligned derivations, accents, binomials, equation
  numbers, and long equations broken across lines.
- Word/PowerPoint Office Math and HTML/EPUB MathML are read exactly.
- Display equations in PDFs are rebuilt from where the page places its glyphs; an
  equation whose layout is ambiguous is kept as printed and marked uncertain, with the
  evidence kept beside the document.
- Exact calculation with SI units and dimension checks.

### Optional AI assistance

- Off by default. A user may connect Anthropic, OpenAI, Google Gemini or Mistral with
  their own API key, kept by the Windows Credential Manager, after agreeing to what will
  be sent. It can re-word a question RUDRA did not understand (only the question is sent)
  or word an explanation of an answer's statements (only the question and those statements
  are sent). Every explained sentence must cite the statements; unsupported sentences and
  numbers are removed. Nothing it says is stored as knowledge.

### Other

- **Export Knowledge Base** and **Import Knowledge Base**: one versioned, checksummed
  backup file with the database, the preserved documents and the OCR and equation-layout
  records, validated completely before it replaces anything. AI keys are never included.
- Typed and pasted text in computer actions is verified by reading the focused edit field
  back; what cannot be read back is reported as not verified.
- Optional notice when a newer stable release is published on GitHub, with a manual
  **Check for Updates**. Nothing is downloaded or installed automatically.
- Works offline; no account, cloud service or telemetry.

### Windows installer

- Per-user installation without administrator rights, into
  `%LOCALAPPDATA%\Programs\RUDRA`, with a Start Menu entry and an optional desktop
  shortcut.
- User data is kept separately in `%LOCALAPPDATA%\RUDRA` and survives upgrades and
  uninstalling.
- Not code-signed: Windows SmartScreen may warn before the first start.
