"""Alias normalisation (decision D-30, ADR 0010).

The rule, and nothing beyond it::

    NFKC  ->  casefold()  ->  collapse internal whitespace

Hyphens and punctuation are **deliberately not stripped**. Folding ``MOS-FET`` into
``MOSFET`` would not be normalisation; it would be an equivalence *judgement*
encoded in a function, and Part 3 section 73 forbids exactly that: "Never merge two
concepts merely because they have similar names." Typographic differences
(``MOSFET`` versus ``mosfet``) are folded; differences a person could reasonably
disagree about are left for Phase 8 to weigh with evidence.

NFKC matters in practice rather than in theory: Phase 0 check #3 verified that
real technical text carries ``Ω`` and ``μF``, and NFKC is what makes those stable
across sources that encode them differently.

``normalized_alias`` is a **persisted derived value** and SQLite cannot verify it,
because nothing in the database can call this function. `NORMALIZATION_VERSION` is
written into `database_metadata` so that a future change to this rule is
detectable rather than silent - every stored value would then need recomputing.

This module is layer 0: pure, no I/O, no imports outside the standard library.
"""

import re
import unicodedata
from typing import Final

#: Bumped whenever the rule below changes. Stored in `database_metadata` as
#: `alias_normalization_version` (ADR 0010).
NORMALIZATION_VERSION: Final = 1

_WHITESPACE: Final = re.compile(r"\s+")


def normalize_alias(value: str) -> str:
    """Return the canonical lookup form of a concept name.

    Raises ValueError for a value that is empty or only whitespace, because such a
    name cannot be looked up and storing it would create a row nothing can find.
    """
    if not isinstance(value, str):
        raise TypeError(f"alias must be a string, got {type(value).__name__}")
    folded = unicodedata.normalize("NFKC", value).casefold()
    collapsed = _WHITESPACE.sub(" ", folded).strip()
    if not collapsed:
        raise ValueError(f"alias normalises to nothing: {value!r}")
    return collapsed
