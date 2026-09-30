"""Text-quality assessment, stage 6's gate (Part 2 section 33, ADR 0016).

Section 33's pipeline is::

    PDF -> Detect text quality -> If inadequate: OCR

**The detection is unconditional; the OCR engine is not.** That distinction is what
this module implements. Phase 4 assesses every page and reports which ones would
need OCR; it does not perform OCR, and no OCR engine is installed (decision D-04
remains open until a real document requires it).

What this module must never do is paper over the gap. Section 33 says *"Never
silently replace the original extracted text"*, and Part 1 section 6 forbids
turning an unknown into a fact. A page that yields no usable text is reported as
needing OCR and stored with empty text and an honest `TextOrigin` - not dropped,
and not filled in.

The thresholds below are **heuristics, not specification**. Section 33 says
"insufficient machine-readable text" without defining sufficiency.
"""

from dataclasses import dataclass

from app.models.enums import TextOrigin

#: Below this many characters, a page is treated as having no usable text. A page
#: of a technical textbook carries hundreds; a scanned image yields a handful of
#: stray marks at most.
MIN_CHARACTERS: int = 24

#: A page whose characters are mostly not letters or digits is likely OCR noise or
#: a figure caption fragment rather than readable prose.
MIN_ALPHANUMERIC_RATIO: float = 0.35


@dataclass(frozen=True, slots=True)
class TextQuality:
    """The verdict on one page's extracted text."""

    page_number: int
    character_count: int
    alphanumeric_ratio: float
    #: True when this page would need OCR to contribute anything.
    needs_ocr: bool
    #: Why, in words, for the report a person reads. None when the text is fine.
    reason: str | None
    origin: TextOrigin

    @property
    def usable(self) -> bool:
        return not self.needs_ocr


def assess(page_number: int, text: str) -> TextQuality:
    """Judge one page's text (Part 2 section 33).

    `TextOrigin` is set here and is never guessed later:

    * `NATIVE_TEXT` - the parser returned usable text.
    * `UNKNOWN` - the page yielded nothing usable. **Not `OCR`**: no OCR was run,
      and labelling it `OCR` would claim a provenance that does not exist.

    `MIXED` and `OCR` are reachable only once an OCR engine exists, which is why
    Phase 4 never produces them.
    """
    stripped = text.strip()
    count = len(stripped)
    alphanumeric = sum(1 for character in stripped if character.isalnum())
    ratio = (alphanumeric / count) if count else 0.0

    if count < MIN_CHARACTERS:
        return TextQuality(
            page_number=page_number,
            character_count=count,
            alphanumeric_ratio=ratio,
            needs_ocr=True,
            reason=(
                f"only {count} characters of text were extracted "
                f"(threshold {MIN_CHARACTERS}); the page is probably scanned"
            ),
            origin=TextOrigin.UNKNOWN,
        )

    if ratio < MIN_ALPHANUMERIC_RATIO:
        return TextQuality(
            page_number=page_number,
            character_count=count,
            alphanumeric_ratio=ratio,
            needs_ocr=True,
            reason=(
                f"only {ratio:.0%} of the extracted characters are letters or "
                f"digits (threshold {MIN_ALPHANUMERIC_RATIO:.0%}); the text is "
                "probably extraction noise"
            ),
            origin=TextOrigin.UNKNOWN,
        )

    return TextQuality(
        page_number=page_number,
        character_count=count,
        alphanumeric_ratio=ratio,
        needs_ocr=False,
        reason=None,
        origin=TextOrigin.NATIVE_TEXT,
    )
