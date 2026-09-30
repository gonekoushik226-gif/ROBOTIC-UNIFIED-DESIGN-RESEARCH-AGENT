# Supported document formats

RUDRA identifies every file by its content, not by its name: a Word document renamed
`.txt` is still read as Word, and a program renamed `.pdf` is refused. Each imported file
is copied into RUDRA's own document store, named by its SHA-256; the original is never
modified. Every extracted statement records the document, the location (page, slide,
sheet or section) and the exact text it came from.

| Format | Extensions | One location is | What is kept | Equations |
|---|---|---|---|---|
| PDF | `.pdf` | a page | The text layer of each page; pages without one are read by OCR (see [OCR.md](OCR.md)); document title and author | Display equations rebuilt from the page layout ([MATH.md](MATH.md)) |
| Word | `.docx` | a section (split at headings) | Heading styles as headings, paragraphs, numbered and bulleted lists, tables (a two-column table reads as *Term: description*), captions, footnotes and endnotes, hyperlink targets; title and author | Office Math (OMML), exact |
| PowerPoint | `.pptx` | a slide | Slide titles as headings, text boxes, tables, speaker notes | Office Math, exact |
| Excel | `.xlsx` | a sheet (long sheets in parts of 200 rows) | Each sheet's cell values, row by row | — |
| EPUB | `.epub` | a section | Chapters in reading order, headings, lists, tables | MathML, exact |
| HTML | `.html`, `.htm`, `.xhtml` | a section | Headings, paragraphs, lists, tables, captions; scripts and styles are ignored | MathML, exact |
| Markdown | `.md`, `.markdown` | a section | Headings, lists, tables | `$$ … $$` blocks, as written |
| Plain text | `.txt`, `.text` | a part | The text (UTF-8, UTF-16 or Windows-1252 detected) | — |
| CSV | `.csv` | a part (200 rows) | Rows; a two-column table reads as *Term: description* | — |
| RTF | `.rtf` | a part | The text, without formatting codes | — |
| Images | `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff` | an image (a TIFF frame) | Text recognized by Windows OCR, with its line and word layout | — (not reconstructed) |

"Exact" means the equation is read from the document's own mathematical structure, so it
is stored as the source's statement. Text from OCR, and PDF equations whose layout left a
doubt, are stored as *uncertain*.

## Refused formats

| Format | Why | What to do |
|---|---|---|
| Legacy Word, Excel, PowerPoint (`.doc`, `.xls`, `.ppt`) | A closed binary format RUDRA does not parse | Open the file and save it as `.docx`, `.xlsx` or `.pptx` |
| JSON | Data structures, not statements | — |
| XML (generic) | Its meaning depends on a schema RUDRA does not know | — |
| Other files (programs, archives, audio, video) | Not documents | — |

## Safety

Office and EPUB files are ZIP containers. They are opened read-only with limits (at most
20,000 parts, 512 MB expanded, 64 MB for any one XML part); XML that declares a DTD or
entities is refused, never expanded; text and RTF files over 64 MB are refused. Nothing
in a document is executed: macros, scripts and embedded objects are ignored.
