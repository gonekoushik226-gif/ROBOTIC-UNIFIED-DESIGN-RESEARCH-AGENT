"""Adaptive document-structure detection (Part 2 section 35, ADR 0015).

Section 35's one imperative is **"Structure detection must be adaptive"**, and it
supplies its own counter-examples: one textbook numbers itself

    Chapter 5 / 5.1 / 5.2.1

another

    Unit III / Module 2 / Topic A

and a third has no explicit hierarchy at all. A detector that understands only the
first is not adaptive, so this module recognises several heading *shapes* and, just
as importantly, reports nothing when it recognises none.

Two rules it will not break:

* **Nothing is invented.** A heading is emitted only when a line actually matches a
  shape. Part 1 section 5 - unsupported information must not be presented as fact -
  applies to structure as much as to claims.
* **Everything found here is `EXPLICIT_STRUCTURE`**, because every element comes
  from a line the document printed. `INFERRED_STRUCTURE` is reserved for structure
  deduced by other means (font size, whitespace, a table of contents), which Phase
  4 does not attempt. The value exists in the vocabulary so that a later phase can
  use it honestly; emitting it now for a heading that was in fact printed would be
  a lie in the other direction.

Detection quality is deliberately modest, and this is the specification's own
position: section 35 says the system *"should attempt to identify"* these elements,
and **Part 5 section 189 does not test structure at all** (ADR 0015).
"""

import re
from dataclasses import dataclass

from app.models.enums import DocumentKind, StructureOrigin

#: "Chapter 5", "CHAPTER 12 - Amplifiers", "Appendix B", "Unit III", "Module 2".
#: The keyword carries the kind; the remainder, if any, is the title.
_KEYWORD = re.compile(
    r"^\s*(?P<word>chapter|unit|module|part|appendix|section|topic|preface|"
    r"introduction|references|bibliography)\s*"
    r"(?P<label>[0-9]+|[IVXLC]+|[A-Z])?\s*[:.–—-]?\s*(?P<title>.{0,120})$",
    re.IGNORECASE,
)

#: "5.2", "5.2.1", "5.2.1.3" followed by a title. Depth comes from the dot count,
#: which is why this shape carries hierarchy and the keyword shape does not.
_NUMBERED = re.compile(
    r"^\s*(?P<label>\d+(?:\.\d+){1,4})\.?\s+(?P<title>\S.{0,120})$"
)

#: Words that name a kind on their own, e.g. "Definition 3.1", "Theorem 2".
_LABELLED_KIND = re.compile(
    r"^\s*(?P<word>definition|theorem|lemma|proof|example|exercise|problem|"
    r"solution|note|remark|procedure|algorithm)\s*"
    r"(?P<label>[0-9][0-9.]*)?\s*[:.–—-]?\s*(?P<title>.{0,120})$",
    re.IGNORECASE,
)

_KEYWORD_KINDS: dict[str, DocumentKind] = {
    "chapter": DocumentKind.CHAPTER,
    "unit": DocumentKind.CHAPTER,
    "module": DocumentKind.SECTION,
    "part": DocumentKind.CHAPTER,
    "topic": DocumentKind.SUBSECTION,
    "section": DocumentKind.SECTION,
    "appendix": DocumentKind.APPENDIX,
    "preface": DocumentKind.PREFACE,
    "introduction": DocumentKind.PREFACE,
    "references": DocumentKind.REFERENCES,
    "bibliography": DocumentKind.REFERENCES,
}

_LABELLED_KINDS: dict[str, DocumentKind] = {
    "definition": DocumentKind.DEFINITION,
    "theorem": DocumentKind.THEOREM,
    "lemma": DocumentKind.LEMMA,
    "proof": DocumentKind.PROOF,
    "example": DocumentKind.EXAMPLE,
    "exercise": DocumentKind.EXERCISE,
    "problem": DocumentKind.PROBLEM,
    "solution": DocumentKind.SOLUTION,
    "note": DocumentKind.NOTE,
    "remark": DocumentKind.REMARK,
    "procedure": DocumentKind.PROCEDURE,
    "algorithm": DocumentKind.PROCEDURE,
}

#: A heading is short. A 300-character line is a paragraph that happens to begin
#: with a number.
_MAX_HEADING_LENGTH = 140


@dataclass(frozen=True, slots=True)
class DetectedHeading:
    """One heading found on one page, before it becomes a database row."""

    page_number: int
    kind: DocumentKind
    #: Free text, never parsed into numbers: "5.2.1", "III", "B", or None.
    label: str | None
    title: str | None
    #: Nesting depth, 0 for top level. Derived from the label shape where the
    #: document provides one, which is how "5.2.1" becomes a child of "5.2".
    depth: int
    origin: StructureOrigin = StructureOrigin.EXPLICIT_STRUCTURE


def detect_headings(page_number: int, text: str) -> tuple[DetectedHeading, ...]:
    """Find the headings a page actually printed.

    Returns an empty tuple for a page with none - which is the correct answer for
    most pages of most books, and for every page of a document with no hierarchy.
    """
    found: list[DetectedHeading] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > _MAX_HEADING_LENGTH:
            continue
        heading = _match(page_number, line)
        if heading is not None:
            found.append(heading)
    return tuple(found)


def assign_parents(
    headings: tuple[DetectedHeading, ...],
) -> tuple[tuple[DetectedHeading, int | None], ...]:
    """Pair each heading with the index of its parent, or None at top level.

    A stack walk over `depth`. It never invents a parent: a heading deeper than
    anything before it simply attaches to the nearest shallower ancestor, and a
    heading with no shallower ancestor is top level.
    """
    pairs: list[tuple[DetectedHeading, int | None]] = []
    stack: list[tuple[int, int]] = []  # (depth, index)
    for index, heading in enumerate(headings):
        while stack and stack[-1][0] >= heading.depth:
            stack.pop()
        pairs.append((heading, stack[-1][1] if stack else None))
        stack.append((heading.depth, index))
    return tuple(pairs)


# ---------------------------------------------------------------------- internals


def _match(page_number: int, line: str) -> DetectedHeading | None:
    numbered = _NUMBERED.match(line)
    if numbered is not None:
        label = numbered.group("label")
        return DetectedHeading(
            page_number=page_number,
            # Depth from the dot count: "5.1" is a section, "5.1.1" a subsection,
            # anything deeper a sub-subsection. Section 35 names exactly these three.
            kind=_numbered_kind(label),
            label=label,
            title=_clean(numbered.group("title")),
            depth=min(label.count("."), 3),
        )

    labelled = _LABELLED_KIND.match(line)
    if labelled is not None:
        word = labelled.group("word").lower()
        return DetectedHeading(
            page_number=page_number,
            kind=_LABELLED_KINDS[word],
            label=labelled.group("label"),
            title=_clean(labelled.group("title")),
            # These sit inside whatever section contains them rather than forming
            # the spine of the document.
            depth=4,
        )

    keyword = _KEYWORD.match(line)
    if keyword is not None:
        word = keyword.group("word").lower()
        kind = _KEYWORD_KINDS[word]
        return DetectedHeading(
            page_number=page_number,
            kind=kind,
            label=keyword.group("label"),
            title=_clean(keyword.group("title")),
            depth=0 if kind in (DocumentKind.CHAPTER, DocumentKind.PREFACE,
                                DocumentKind.APPENDIX, DocumentKind.REFERENCES) else 1,
        )
    return None


def _numbered_kind(label: str) -> DocumentKind:
    dots = label.count(".")
    if dots <= 1:
        return DocumentKind.SECTION
    if dots == 2:
        return DocumentKind.SUBSECTION
    return DocumentKind.SUB_SUBSECTION


def _clean(title: str | None) -> str | None:
    """Empty titles stay None - a heading with no text has no title to record."""
    if title is None:
        return None
    cleaned = title.strip(" .:-–—")
    return cleaned or None
