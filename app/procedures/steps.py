"""The steps and parameters of a documented procedure (ADR 0048 P16-3, P16-4; ADR 0049 P17-8). Pure.

A stored procedure's steps are its knowledge object's statement, joined with `" / "` when
it was written. They are accepted only when they equal the steps read again from the
evidence quote - the document's own text - so every step RUDRA executes or constructs is
provably the document's. Three layouts are read back:

    Step N       "Step 1: ... Step 2: ..." (Phase 5's detector)
    menu path    "File -> New Project -> Create", with arrows (Phase 17, P17-4)
    shortcut     a sentence naming the key combination the one step presses (P17-5)

Parameters are the placeholders a step writes in angle brackets, e.g. `<folder>`
(section 19: *"Procedures must be parameterized"*).
"""

import re

#: How the extractor joined the steps into the knowledge object's statement.
STEP_SEPARATOR = " / "

#: The Phase 5 step marker (`app/extraction/detectors.py`, `_STEP`), found inside the
#: flattened quote rather than at line starts.
_MARKER = re.compile(r"\bStep\s*(?P<n>\d{1,2})\s*[:.–—-]?\s*", re.IGNORECASE)

#: A parameter placeholder: a name in angle brackets.
_PLACEHOLDER = re.compile(r"<(?P<name>[A-Za-z][A-Za-z0-9 _-]{0,39})>")

#: A menu element (P17-4): a capital letter, then at most 39 characters of letters, digits,
#: spaces and & ' ( ) / + - . - so "New Project..." is one, and "x" or "0" never is.
_ELEMENT = re.compile(r"[A-Z][A-Za-z0-9&'()/+.\- ]{0,39}")
_ARROW = re.compile(r"\s*(?:→|->)\s*")

#: A key combination (P17-5): one or more modifiers joined by + to one key.
_KEY = (r"(?:F1[0-2]|F[1-9]|Enter|Return|Tab|Escape|Esc|Delete|Del|Insert|Ins|Home|End|Space|Backspace|"
        r"PageUp|PageDown|PgUp|PgDn|Up|Down|Left|Right|[A-Za-z0-9])")
SHORTCUT = re.compile(rf"\b(?:(?:Ctrl|Control|Alt|Shift|Win|Cmd)\s*\+\s*)+{_KEY}(?![A-Za-z0-9])", re.IGNORECASE)
_MODIFIERS = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "cmd": "Cmd"}
_KEYS = {"enter": "Enter", "return": "Enter", "tab": "Tab", "escape": "Esc", "esc": "Esc", "delete": "Delete",
         "del": "Delete", "insert": "Insert", "ins": "Insert", "home": "Home", "end": "End", "space": "Space",
         "backspace": "Backspace", "pageup": "PageUp", "pgup": "PageUp", "pagedown": "PageDown",
         "pgdn": "PageDown", "up": "Up", "down": "Down", "left": "Left", "right": "Right"}


def flat(text: str) -> str:
    """Whitespace collapsed to single spaces - the extractor's and the quote check's rule."""
    return " ".join(text.split())


_flat = flat


def stored_steps(statement: str) -> tuple[str, ...]:
    """The steps as the statement holds them."""
    return tuple(_flat(part) for part in statement.split(STEP_SEPARATOR))


def quoted_steps(quote: str) -> tuple[str, ...] | None:
    """The steps as the document printed them at `Step N` markers, or None when the quote
    does not split cleanly: it must begin with a marker and hold at least two."""
    marks = list(_MARKER.finditer(quote))
    if len(marks) < 2 or quote[: marks[0].start()].strip():
        return None
    bounds = [m.end() for m in marks]
    ends = [m.start() for m in marks[1:]] + [len(quote)]
    return tuple(_flat(quote[start:end]) for start, end in zip(bounds, ends, strict=True))


def menu_element(text: str) -> str | None:
    """One menu element, trimmed, or None; a final full stop (not "...") is not part of it.

    A label, not a sentence: at most five words, each beginning with a capital letter or a
    digit except the short function words (*Save as...*, *Export to PDF*). So *New Project
    to begin* is no element; a sentence-case label (*Page setup*) is missed, never invented.
    """
    element = text.strip()
    if element.endswith(".") and not element.endswith("..."):
        element = element[:-1].rstrip()
    if not _ELEMENT.fullmatch(element):
        return None
    words = element.split()
    if len(words) > 5 or not all(_label_word(word) for word in words):
        return None
    return element


#: Words a menu label may leave in lower case.
_FUNCTION_WORDS = frozenset({"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or",
                             "the", "to", "with"})


def _label_word(word: str) -> bool:
    core = word.strip(".&/+()'-")
    return not core or core[0].isupper() or core[0].isdigit() or core.casefold() in _FUNCTION_WORDS


def menu_elements(line: str) -> tuple[str, ...] | None:
    """A whole line of two or more menu elements joined by arrows, or None (P17-4)."""
    parts = _ARROW.split(line.strip())
    if len(parts) < 2:
        return None
    elements = tuple(menu_element(part) for part in parts)
    return None if any(e is None for e in elements) else elements


def normalize_combo(text: str) -> str:
    """A key combination in one spelling: "ctrl + shift + s" is "Ctrl+Shift+S"."""
    *modifiers, key = (part.strip() for part in re.split(r"\s*\+\s*", text.strip()))
    folded = key.casefold()
    name = _KEYS.get(folded) or (key.upper() if len(key) == 1 or re.fullmatch(r"f\d{1,2}", folded) else key)
    return "+".join([*(_MODIFIERS[m.casefold()] for m in modifiers), name])


def recovered_steps(statement: str, quotes: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    """The steps, and "" when every quote agrees with the statement; otherwise the steps
    and the reason they cannot be executed as documented (P16-3, P17-8)."""
    steps = stored_steps(statement)
    if not quotes:
        return steps, "no evidence quote is available to check the steps against"
    for quote in quotes:
        again = quoted_steps(quote) or menu_elements(quote)
        if again is None and len(steps) == 1 and steps[0].startswith("Press "):
            combos = {normalize_combo(m.group(0)) for m in SHORTCUT.finditer(quote)}
            again = steps if steps[0].removeprefix("Press ") in combos else None
        if again is None:
            return steps, ("the evidence quote does not split into its steps (Step N markers, a menu "
                           "path's arrows, or the shortcut its one step presses)")
        if tuple(again) != steps:
            return steps, ("the stored steps and the evidence quote disagree (a step may contain "
                           f"{STEP_SEPARATOR.strip()!r}, or the quote holds another Step marker)")
    return steps, ""


def parameter_key(name: str) -> str:
    """How parameter names are compared: whitespace collapsed, case-folded."""
    return _flat(name).casefold()


def parameters(steps: tuple[str, ...]) -> tuple[str, ...]:
    """The placeholders the steps name, in order of first appearance, as first written."""
    found: dict[str, str] = {}
    for step in steps:
        for match in _PLACEHOLDER.finditer(step):
            found.setdefault(parameter_key(match["name"]), _flat(match["name"]))
    return tuple(found.values())


def value_problem(value: str) -> str:
    """Why a parameter value cannot be used, or "" (P16-4)."""
    if not value.strip():
        return "is empty"
    if "\n" in value or "\r" in value:
        return "spans more than one line"
    if "<" in value or ">" in value:
        return "contains < or >"
    return ""


def substitute(step: str, values: dict[str, str]) -> str:
    """The step with each placeholder replaced by its value (keys as `parameter_key`)."""
    return _PLACEHOLDER.sub(lambda m: values[parameter_key(m["name"])], step)
