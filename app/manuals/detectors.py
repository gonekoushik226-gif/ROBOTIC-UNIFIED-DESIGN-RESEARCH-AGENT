"""Application-documentation detectors (ADR 0049 P17-4 ... P17-7). Pure.

They run only over a document the user declared a manual (P17-2), one stored page at a
time, and find four kinds of item, each with its exact span on the page:

    MENU_PATH    a whole line of elements joined by -> or the arrow sign, or a line followed
                 by lines that begin with one (section 215's layout) - a workflow
    SHORTCUT     a key combination such as Ctrl+N in a sentence
    CONSTRAINT   a sentence that states must / must not / cannot / is limited to / at most
    FILE_FORMAT  a sentence naming a file extension together with file, format, save ...

Menus, commands and inputs are derived from stored menu paths (`inputs`, P17-7); they are
not detected twice. A miss is always preferred to an invention: every rule is narrow.
"""

import re
from dataclasses import dataclass

from app.procedures.steps import SHORTCUT, flat, menu_element, menu_elements, normalize_combo

MENU_PATH = "MENU_PATH"
SHORTCUT_KIND = "SHORTCUT"
CONSTRAINT = "CONSTRAINT"
FILE_FORMAT = "FILE_FORMAT"

_ARROW_IN = re.compile(r"→|->")
#: A line that continues a menu path: it begins with an arrow.
_CONTINUATION = re.compile(r"^\s*(?:→|->)\s*(?P<element>.*?)\s*$")
#: The modal wording of a constraint (P17-6).
_CONSTRAINT = re.compile(
    r"\b(?:must(?:\s+not)?|cannot|can\s+not|may\s+not|(?:is|are)\s+limited\s+to|at\s+most|no\s+more\s+than|"
    r"maximum\s+of)\b", re.IGNORECASE)
#: A file extension: `.ext` or `*.ext`, not inside a word, a path or a number.
_EXTENSION = re.compile(r"(?<![\w/\\.*])\*?\.(?P<ext>[A-Za-z][A-Za-z0-9]{0,7})\b")
_FORMAT_WORD = re.compile(r"\b(?:files?|formats?|extensions?|saved?|saves|exports?|imports?)\b", re.IGNORECASE)
#: A workflow step that asks for an input (P17-7).
_INPUT = re.compile(r"^(?:enter|type|specify|select|choose|pick)\s+(?:the\s+|a\s+|an\s+|your\s+)?(?P<name>.+?)\.?$",
                    re.IGNORECASE)
#: Where a sentence ends: terminal punctuation before whitespace, or a colon or a blank
#: line before a line break.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|:(?=\s*\n)|\n\s*\n")


@dataclass(frozen=True, slots=True)
class Found:
    """One item a manual page states."""

    kind: str
    page_number: int
    #: The span in the stored page text.
    start: int
    end: int
    #: The page's text at the span, flattened - the evidence (the quote check's rule).
    text: str
    #: What is stored as the knowledge object's statement.
    statement: str
    #: The knowledge object's (and the procedure's) name.
    name: str
    steps: tuple[str, ...] = ()


def _lines(text: str) -> list[tuple[int, int, str]]:
    """Each line with its span, without its line break."""
    out, offset = [], 0
    for line in text.split("\n"):
        out.append((offset, offset + len(line), line))
        offset += len(line) + 1
    return out


def _span(line_start: int, line: str) -> tuple[int, int]:
    """The span of a line's text without surrounding whitespace."""
    stripped = line.strip()
    start = line_start + line.index(stripped) if stripped else line_start
    return start, start + len(stripped)


def menu_paths(page_number: int, text: str) -> tuple[Found, ...]:
    """Menu paths: a whole line `A -> B -> C`, or a line followed by `-> B` lines (P17-4)."""
    lines = _lines(text)
    found, index = [], 0
    while index < len(lines):
        start, _, line = lines[index]
        elements = menu_elements(line)
        if elements is not None:
            first, last = _span(start, line)
            found.append(_path(page_number, text, first, last, elements))
            index += 1
            continue
        head = None if _ARROW_IN.search(line) else menu_element(line)
        following, probe = [], index + 1
        while head is not None and probe < len(lines):
            match = _CONTINUATION.match(lines[probe][2])
            element = None if match is None else menu_element(match["element"])
            if element is None:
                break
            following.append(element)
            probe += 1
        if following:
            first, _ = _span(start, line)
            _, last = _span(lines[probe - 1][0], lines[probe - 1][2])
            found.append(_path(page_number, text, first, last, (head, *following)))
            index = probe
            continue
        index += 1
    return tuple(found)


def _path(page_number: int, text: str, start: int, end: int, elements: tuple[str, ...]) -> Found:
    return Found(MENU_PATH, page_number, start, end, flat(text[start:end]), " / ".join(elements),
                 f"Menu path stated on page {page_number}", elements)


def sentences(text: str, excluded: tuple[tuple[int, int], ...] = ()) -> tuple[tuple[int, int], ...]:
    """Sentence spans, never crossing an excluded span (a menu path) or a blank line."""
    cuts = sorted({0, len(text), *(p for span in excluded for p in span),
                   *(m.end() for m in _SENTENCE_END.finditer(text))})
    spans = []
    for start, end in zip(cuts, cuts[1:], strict=False):
        if any(s <= start and end <= e for s, e in excluded):
            continue
        piece = text[start:end]
        if piece.strip():
            first = start + (len(piece) - len(piece.lstrip()))
            spans.append((first, first + len(piece.strip())))
    return tuple(spans)


def sentence_items(page_number: int, text: str, excluded: tuple[tuple[int, int], ...]) -> tuple[Found, ...]:
    """Shortcuts, constraints and file formats, from the page's sentences (P17-5, P17-6)."""
    found = []
    for start, end in sentences(text, excluded):
        sentence = flat(text[start:end])
        for match in SHORTCUT.finditer(sentence):
            combo = normalize_combo(match.group(0))
            found.append(Found(SHORTCUT_KIND, page_number, start, end, sentence, f"Press {combo}",
                               f"Keyboard shortcut {combo} stated on page {page_number}", (f"Press {combo}",)))
        if _CONSTRAINT.search(sentence):
            found.append(Found(CONSTRAINT, page_number, start, end, sentence, sentence,
                               f"Constraint stated on page {page_number}"))
        extensions = sorted({"." + m["ext"].lower() for m in _EXTENSION.finditer(sentence)})
        if extensions and _FORMAT_WORD.search(sentence):
            found.append(Found(FILE_FORMAT, page_number, start, end, sentence, sentence,
                               f"File format {', '.join(extensions)}"))
    return tuple(found)


def detect(page_number: int, text: str) -> tuple[Found, ...]:
    """Everything one declared manual page states, in page order."""
    paths = menu_paths(page_number, text)
    rest = sentence_items(page_number, text, tuple((f.start, f.end) for f in paths))
    return tuple(sorted((*paths, *rest), key=lambda f: (f.start, f.kind)))


def step_input(step: str) -> str | None:
    """The input a workflow step asks for - "Select Template" asks for "template" - or None."""
    match = _INPUT.match(step.strip())
    return None if match is None else " ".join(match["name"].split()).casefold()
