# OCR — reading scanned pages and images

RUDRA reads text in images with the OCR engine built into Windows (`Windows.Media.Ocr`),
and renders scanned PDF pages with Windows' own PDF renderer (`Windows.Data.Pdf`). Both
run on your computer: nothing is uploaded, nothing extra is installed, and OCR works
offline.

## When OCR is used

- **PDF pages** are first read from their text layer. Only a page whose text layer is
  missing or unusable (fewer than 24 characters, or mostly garbled characters) is rendered
  and recognized. A page with a little text of its own (a title page) keeps it unless OCR
  reads substantially more. A document can therefore mix native pages and recognized
  pages.
- **Images** — PNG, JPEG and TIFF (every frame of a multi-page TIFF) — are always
  recognized.

## What is kept

- The recognized text is stored with the origin **OCR**, never as the document's own text.
- The lines and word boxes are kept beside the document
  (`data\extracted\<document hash>\page-N.ocr.json`) and travel with knowledge-base
  backups.
- Windows' engine reports no confidence value, so none is stored — RUDRA never invents
  one.

## Uncertainty

Recognition can misread characters, so every statement extracted from OCR text is stored
as **uncertain**, and its extraction record carries the `+ocr` mark. Answers show such
lines under **Uncertain** with the reason *"recognized by OCR from a scanned page or image;
compare it with the source"*; **View Sources → Open source document** shows the page.

## When OCR is not available

OCR needs at least one OCR language installed in Windows (installed with most display
languages; **Settings → Time & language → Language & region → a language → Language
options → Optical character recognition**). Without one, the import still completes: the
pages without text are recorded, and RUDRA says that they could not be read. A failure of
the OCR engine on some pages is reported the same way and never stops the import.

## Limits

- Printed text only; handwriting is not supported.
- Heavily rotated, skewed or low-resolution scans give poor results.
- Mathematics inside images is not reconstructed: it is kept as recognized text,
  uncertain.
- Recognition speed is a second or more per page.

## For developers

Setting the environment variable `RUDRA_DISABLE_OCR=1` switches OCR off for a process;
the test suite does this so results do not depend on the machine's OCR languages, and
the OCR tests switch it back on.
