"""Words the person wants RUDRA to spell as they write them: names, terms, product names.

A speech model hears a sound and writes the commonest spelling: an uncommon name comes
out as whatever it sounds like (Kaushik may be written Koshik). Telling the recogniser the
spelling beforehand - as the start of the text it is continuing - makes it use that
spelling. The words are the person's own, kept in `config/voice.json` on this computer and
read for each listening; they leave the computer nowhere.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

FILE = "voice.json"
MAX_WORDS = 20
MAX_LENGTH = 40
_ALLOWED = re.compile(r"[^\w .'’-]", re.UNICODE)


def clean(text: str | Iterable[str]) -> tuple[str, ...]:
    """The words in `text` (separated by commas or new lines), tidied: letters, digits, spaces,
    hyphens, full stops and apostrophes only; each at most 40 characters; no repeats; at most 20."""
    pieces = re.split(r"[,;\n]", text) if isinstance(text, str) else list(text)
    words: list[str] = []
    for piece in pieces:
        word = " ".join(_ALLOWED.sub("", str(piece)).split())[:MAX_LENGTH].strip()
        if word and word.casefold() not in {w.casefold() for w in words}:
            words.append(word)
    return tuple(words[:MAX_WORDS])


def load(config_dir: Path) -> tuple[str, ...]:
    """The saved words; none when the file is missing or unreadable (never an error)."""
    try:
        data = json.loads((config_dir / FILE).read_text(encoding="utf-8"))
        return clean(data.get("words") or ())
    except (OSError, ValueError, AttributeError, TypeError):
        return ()


def save(config_dir: Path, words: str | Iterable[str]) -> tuple[str, ...]:
    """Keep the words (tidied) in `config/voice.json`; returns what was kept."""
    kept = clean(words)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / FILE).write_text(json.dumps({"words": list(kept)}, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    return kept
