"""The deduplication rules as pure functions (ADR 0033).

Nothing here reads or writes the database, so every rule can be tested on its own
(Part 1 section 24). The rule names recorded on assessment rows are the decision
identifiers of ADR 0033 - `P8-12`, `P8-13`, `P8-16` - and rule `C1`, which ADR 0033
names itself; a reader of a record can go straight to the rule that wrote it.

**No similarity measure, score or threshold exists here** (P8-13, G1). Only three
judgements are made, all deterministic: two statements are identical after
normalisation, or they are not; two concepts share a stored normalised name, or
they do not; two statements differ only in numeric values (C1), or they do not.
"""

import re
from decimal import Decimal, InvalidOperation

from app.models.identifiers import parse_id
from app.models.naming import normalize_alias

#: Exact duplicates (P8-12): link, never recreate. Also the rule an identical
#: EQUATION at another location is recorded under - `POSSIBLE_DUPLICATE`, because
#: section 77 says two equations that look identical may mean different things.
RULE_EXACT = "P8-12"
#: Same type, same concept, statement not identical (P8-13): `POSSIBLE_DUPLICATE`.
RULE_SAME_CONCEPT = "P8-13"
#: Rule C1 (P8-14): same type, same concept, differing only in numeric values.
RULE_C1 = "C1"
#: Concept equivalence (P8-16): `POSSIBLE_EQUIVALENT`, never merged.
RULE_CONCEPT = "P8-16"
#: Every rule above is at its first version.
RULE_VERSION = "1"


def normalized_statement(statement: str) -> str:
    """A statement's comparison form: decision D-30's normalisation, nothing more.

    NFKC, casefold, whitespace collapse - the only normalisation this project has
    accepted (ADR 0033 P8-12, class B). Punctuation and hyphens are kept, because
    removing them would be an equivalence judgement (ADR 0010). Computed in memory
    and never stored: `normalized_hash` stays NULL (P8-18).
    """
    return normalize_alias(statement)


def counter(identifier: str) -> int:
    """The numeric counter of an identifier - the only correct sort key (ADR 0006).

    Never the identifier string: string order breaks above the fixed-width ceiling.
    """
    return parse_id(identifier)[1]


def by_counter(identifiers) -> list[str]:
    """Identifiers sorted by their numeric counter (ADR 0006 rule 1)."""
    return sorted(identifiers, key=counter)


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    """The one storage order of an unordered concept pair (the P6-4b precedent).

    Plain string comparison, used only to pick one of two storage forms; the order
    means nothing.
    """
    return (a, b) if a < b else (b, a)


# --------------------------------------------------------------------- rule C1

#: A token that is a number and nothing else: optional sign (ASCII or U+2212),
#: ASCII digits with an optional decimal part, and at most one trailing sentence
#: punctuation mark, which must then be the same on both sides. Anything else -
#: `0.7v`, `1,000`, `10^3`, `0.6–0.7`, `R1` - is not a number to C1, so a
#: difference in it keeps the pair `POSSIBLE_DUPLICATE`. This is deliberately
#: narrow: it must never broaden C1 beyond numeric-value differences (ADR 0033).
_NUMBER = re.compile(r"^(?P<value>[+\-−]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+))(?P<tail>[.,;:]?)$")


def _number(token: str) -> tuple[Decimal, str] | None:
    match = _NUMBER.match(token)
    if match is None:
        return None
    try:
        value = Decimal(match.group("value").replace("−", "-"))
    except InvalidOperation:  # pragma: no cover - the pattern admits only numbers
        return None
    return value, match.group("tail")


def c1_contradicts(first: str, second: str) -> bool:
    """Rule C1 (ADR 0033, P8-14) on two statements of the same type and concept.

    The two statements are normalised (D-30) and split on whitespace. They are
    `CONTRADICTORY` when they have the same number of tokens, differ in at least one
    position, and every position where they differ holds a number on both sides with
    different values - section 46's "X = value A / X = value B". Any other
    difference, a word, a unit or a negation, is not C1.
    """
    left = normalized_statement(first).split()
    right = normalized_statement(second).split()
    if len(left) != len(right):
        return False
    differing = [(a, b) for a, b in zip(left, right) if a != b]
    if not differing:
        return False
    for a, b in differing:
        number_a, number_b = _number(a), _number(b)
        if number_a is None or number_b is None:
            return False
        (value_a, tail_a), (value_b, tail_b) = number_a, number_b
        if tail_a != tail_b or value_a == value_b:
            return False
    return True
