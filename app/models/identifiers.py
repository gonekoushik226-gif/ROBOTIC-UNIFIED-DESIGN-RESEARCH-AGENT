"""Entity identifiers (ADR 0006, master specification Part 3 section 74).

Format::

    <PREFIX>-<zero-padded decimal, minimum width 8>
    canonical regex:  ^[A-Z]{1,4}-[0-9]{8,}$

Ceiling and overflow policy, exactly as approved:

* The fixed-width form holds 99,999,999 identifiers per entity kind.
* Below the ceiling, the number is padded to exactly 8 digits.
* Above it, the number grows naturally to 9 or more digits. There is no failure,
  no migration and no format change; allocation simply continues.
* Uniqueness is preserved without limit.
* Lexicographic ordering is lost above the ceiling, which is why nothing in RUDRA
  may sort by identifier for correctness. Sort by `created_at` instead.

The regex uses `{8,}` rather than `{8}` precisely so it stays consistent with that
policy; `{8}` would reject every identifier issued after the ceiling.

Note on width: the specification shows both `K-00001234` (8 digits) and
`S-000891` (6). RUDRA takes the prefix letters from the specification but
normalises the width to a minimum of 8, so the identifier the specification writes
as `S-000891` is written `S-00000891` here.
"""

import re
from enum import StrEnum
from typing import Final

#: Smallest number of digits. Identifiers may be longer (see the overflow policy).
MIN_DIGITS: Final = 8

#: The ceiling of the fixed-width form. Allocation continues past it.
FIXED_WIDTH_CEILING: Final = 10**MIN_DIGITS - 1

ID_PATTERN: Final = re.compile(r"^[A-Z]{1,4}-[0-9]{8,}$")

_PREFIX_PATTERN: Final = re.compile(r"^[A-Z]{1,4}$")


class EntityKind(StrEnum):
    """Every entity kind that exists, each carrying its identifier prefix.

    The value *is* the prefix. `ExtractionRun` (`RUN`) and `Job` (`JOB`) are
    reserved in ADR 0006 for later phases and are deliberately absent here,
    because nothing implements them yet.

    22 kinds arrived with Phase 2 (Part 5 section 184). Phase 3 added three
    (ADR 0008, ADR 0010), Phase 4 one (ADR 0015), Phase 5 two (ADR 0018, ADR 0022),
    Phase 6 one (ADR 0027) and Phase 8 two (ADR 0034). Adding a member here is
    sufficient on its own: the migrator seeds a counter for every member on every
    migration (ADR 0013), so no migration script needs to know about it.
    """

    DOCUMENT = "DOC"
    DOCUMENT_VERSION = "DV"
    DOCUMENT_SEGMENT = "SEG"
    SOURCE = "SRC"
    SOURCE_OCCURRENCE = "S"
    KNOWLEDGE_OBJECT = "K"
    CONCEPT = "CPT"
    RELATIONSHIP = "REL"
    EQUATION = "EQ"
    VARIABLE = "VAR"
    RULE = "RUL"
    PROCEDURE = "PRC"
    CALCULATION = "CALC"
    DERIVATION = "DER"
    CONFLICT = "CON"
    QUERY = "Q"
    INTENT = "INT"
    ACTION = "ACT"
    EXECUTION_PLAN = "PLAN"
    VERIFICATION = "VER"
    MEMORY_ITEM = "MEM"
    AUDIT_EVENT = "AUD"

    # Phase 3. `CA` and `CALC`, and `CO` and `CON`, are distinct strings; the first
    # hyphen delimits the prefix, so they parse unambiguously (as `S`/`SRC`/`SEG`
    # already do).
    CONCEPT_ALIAS = "CA"
    CONCEPT_OCCURRENCE = "CO"
    RELATIONSHIP_OCCURRENCE = "RO"

    # Phase 4 (ADR 0015).
    DOCUMENT_STRUCTURE = "DST"

    # Phase 5. `RUN` was reserved by ADR 0006 and becomes live here (ADR 0018).
    # `XIS` is the one prefix ADR 0006 did not reserve; approved 2026-09-21
    # (ADR 0022). `JOB` stays reserved and absent - U-4 defers the job system.
    EXTRACTION_RUN = "RUN"
    EXTRACTION_ISSUE = "XIS"

    # Phase 6 (ADR 0027, approved 2026-09-21). `RI` shares its first letter with
    # `REL`, `RUL` and `RUN`; the first hyphen delimits the prefix, so it parses
    # unambiguously.
    RELATIONSHIP_INFERENCE = "RI"

    # Phase 8 (ADR 0034 P8-22). Initials of the entity name, as `RI` and `XIS` are.
    # `KE` beside `K`, and `CE` beside `CA`, `CO`, `CON`, `CALC` and `CPT`, parse
    # unambiguously for the same reason: the first hyphen delimits the prefix.
    KNOWLEDGE_EQUIVALENCE = "KE"
    CONCEPT_EQUIVALENCE = "CE"

    @property
    def prefix(self) -> str:
        return self.value


def format_id(kind: EntityKind, number: int) -> str:
    """Render `(kind, number)` in canonical form.

    Raises ValueError for a number below 1, which would not be a real allocation.
    """
    if number < 1:
        raise ValueError(f"identifier numbers start at 1, got {number}")
    return f"{kind.prefix}-{number:0{MIN_DIGITS}d}"


def parse_id(value: str) -> tuple[EntityKind, int]:
    """Recover `(kind, number)` from a canonical identifier.

    The first hyphen delimits the prefix, which is what keeps `S`, `SRC` and `SEG`
    unambiguous despite sharing an initial letter.
    """
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        raise ValueError(f"not a valid RUDRA identifier: {value!r}")
    prefix, digits = value.split("-", 1)
    try:
        kind = EntityKind(prefix)
    except ValueError as exc:
        raise ValueError(f"unknown identifier prefix {prefix!r} in {value!r}") from exc
    return kind, int(digits)


def is_valid_id(value: object, *, kind: EntityKind | None = None) -> bool:
    """True when `value` is a canonical identifier, optionally of a given kind."""
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        return False
    prefix = value.split("-", 1)[0]
    if prefix not in {member.value for member in EntityKind}:
        return False
    return kind is None or prefix == kind.prefix


def prefixes() -> dict[str, str]:
    """Prefix to entity-kind name, for documentation and reports."""
    return {member.value: member.name for member in EntityKind}


# Fail at import time rather than at runtime if the table ever breaks its own rule.
for _member in EntityKind:
    if not _PREFIX_PATTERN.match(_member.value):  # pragma: no cover - guard
        raise ValueError(f"prefix {_member.value!r} does not match ^[A-Z]{{1,4}}$")
if len({m.value for m in EntityKind}) != len(list(EntityKind)):  # pragma: no cover
    raise ValueError("entity prefixes are not unique")
