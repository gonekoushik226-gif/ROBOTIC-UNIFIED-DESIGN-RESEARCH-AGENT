# Changelog

## 0.1.0

First public release of RUDRA for Windows.

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
