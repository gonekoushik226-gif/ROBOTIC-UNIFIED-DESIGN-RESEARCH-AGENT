"""Identifier tests (ADR 0006).

The ceiling and overflow policy is the part most likely to be quietly broken by a
later change, so it is tested explicitly rather than assumed.
"""

from __future__ import annotations

import pytest

from app.models.identifiers import (
    FIXED_WIDTH_CEILING,
    ID_PATTERN,
    MIN_DIGITS,
    EntityKind,
    format_id,
    is_valid_id,
    parse_id,
    prefixes,
)

#: The 22 entities of Part 5 section 184. This set never grows: it is what makes
#: that phase's completeness checkable by counting (decision D-29, ADR 0012).
SECTION_184_PREFIXES = {
    "DOC", "DV", "SEG", "SRC", "S", "K", "CPT", "REL", "EQ", "VAR", "RUL",
    "PRC", "CALC", "DER", "CON", "Q", "INT", "ACT", "PLAN", "VER", "MEM", "AUD",
}

#: Added by Phase 3 (ADR 0008, ADR 0010).
PHASE_3_PREFIXES = {"CA", "CO", "RO"}

#: Added by Phase 4 (ADR 0015).
PHASE_4_PREFIXES = {"DST"}

#: Added by Phase 5. RUN was reserved by ADR 0006; XIS was approved 2026-09-21
#: (ADR 0018, ADR 0022).
PHASE_5_PREFIXES = {"RUN", "XIS"}

#: Added by Phase 6 (ADR 0027, approved 2026-09-21).
PHASE_6_PREFIXES = {"RI"}

#: Added by Phase 8 (ADR 0034, P8-22).
PHASE_8_PREFIXES = {"KE", "CE"}

EXPECTED_PREFIXES = (
    SECTION_184_PREFIXES
    | PHASE_3_PREFIXES
    | PHASE_4_PREFIXES
    | PHASE_5_PREFIXES
    | PHASE_6_PREFIXES
    | PHASE_8_PREFIXES
)


def test_the_twenty_two_section_184_kinds_are_all_present():
    """Phase 2's guarantee, stated separately so later phases cannot erode it."""
    assert SECTION_184_PREFIXES <= {kind.value for kind in EntityKind}
    assert len(SECTION_184_PREFIXES) == 22


def test_every_entity_kind_is_accounted_for():
    assert {kind.value for kind in EntityKind} == EXPECTED_PREFIXES
    assert len(list(EntityKind)) == 31


def test_prefixes_are_unique_and_well_formed():
    values = [kind.value for kind in EntityKind]
    assert len(values) == len(set(values))
    assert all(1 <= len(value) <= 4 and value.isupper() for value in values)


def test_reserved_prefixes_are_not_implemented():
    """JOB is reserved in ADR 0006 but belongs to a later phase.

    Declaring it now would claim an entity that does not exist - U-4 defers the
    job system. Changed in Phase 5: RUN, reserved alongside it, became live with
    ExtractionRun (ADR 0018), so it is now asserted present rather than absent.
    """
    assert "JOB" not in EXPECTED_PREFIXES
    assert "RUN" in EXPECTED_PREFIXES
    assert EntityKind.EXTRACTION_RUN.value == "RUN"


def test_format_pads_to_eight_digits():
    assert format_id(EntityKind.KNOWLEDGE_OBJECT, 1) == "K-00000001"
    assert format_id(EntityKind.KNOWLEDGE_OBJECT, 1234) == "K-00001234"
    assert format_id(EntityKind.SOURCE_OCCURRENCE, 891) == "S-00000891"


def test_specification_six_digit_example_is_normalised():
    """Part 3 section 74 writes S-000891; RUDRA normalises the width to 8.

    The prefix comes from the specification, the width does not.
    """
    assert format_id(EntityKind.SOURCE_OCCURRENCE, 891) == "S-00000891"
    assert not ID_PATTERN.match("S-000891")


@pytest.mark.parametrize("number", [1, 891, 1234, FIXED_WIDTH_CEILING])
def test_below_the_ceiling_the_width_is_exactly_eight(number: int):
    identifier = format_id(EntityKind.KNOWLEDGE_OBJECT, number)
    assert len(identifier.split("-")[1]) == MIN_DIGITS
    assert ID_PATTERN.match(identifier)


@pytest.mark.parametrize("number", [FIXED_WIDTH_CEILING + 1, 10**12])
def test_above_the_ceiling_identifiers_grow_and_stay_valid(number: int):
    """The approved policy: no failure, no format change, uniqueness preserved."""
    identifier = format_id(EntityKind.KNOWLEDGE_OBJECT, number)
    assert ID_PATTERN.match(identifier), identifier
    assert parse_id(identifier) == (EntityKind.KNOWLEDGE_OBJECT, number)
    assert len(identifier.split("-")[1]) > MIN_DIGITS


def test_ceiling_is_ninety_nine_million():
    assert FIXED_WIDTH_CEILING == 99_999_999


def test_ordering_holds_below_the_ceiling():
    ids = [format_id(EntityKind.KNOWLEDGE_OBJECT, n) for n in (1, 2, 10, 100, 1234, FIXED_WIDTH_CEILING)]
    assert ids == sorted(ids)


def test_ordering_is_lost_above_the_ceiling():
    """Documented consequence, and the reason nothing may sort by identifier.

    If this test ever fails, the overflow policy has changed and ADR 0006 must be
    updated with it.
    """
    across = [
        format_id(EntityKind.KNOWLEDGE_OBJECT, FIXED_WIDTH_CEILING),
        format_id(EntityKind.KNOWLEDGE_OBJECT, FIXED_WIDTH_CEILING + 1),
    ]
    assert sorted(across) != across


def test_regex_allows_more_than_eight_digits():
    """`{8,}` rather than `{8}` is what makes the format match the policy."""
    assert ID_PATTERN.pattern == r"^[A-Z]{1,4}-[0-9]{8,}$"
    assert ID_PATTERN.match("K-100000000")


def test_parse_round_trips_every_kind():
    for kind in EntityKind:
        identifier = format_id(kind, 42)
        assert parse_id(identifier) == (kind, 42)


def test_similar_prefixes_parse_unambiguously():
    """S, SRC and SEG share an initial letter; the first hyphen separates them."""
    assert parse_id("S-00000891")[0] is EntityKind.SOURCE_OCCURRENCE
    assert parse_id("SRC-00000012")[0] is EntityKind.SOURCE
    assert parse_id("SEG-00002187")[0] is EntityKind.DOCUMENT_SEGMENT


@pytest.mark.parametrize(
    "value",
    [
        "K-0000001",     # only 7 digits
        "K-1234",        # unpadded
        "k-00000001",    # lowercase prefix
        "KNOWL-00000001",  # prefix too long
        "K_00000001",    # wrong separator
        "K-0000000a",    # not all digits
        "-00000001",     # no prefix
        "K-",            # no digits
        "",
    ],
)
def test_malformed_identifiers_are_rejected(value: str):
    assert not is_valid_id(value)
    with pytest.raises(ValueError):
        parse_id(value)


def test_unknown_prefix_is_rejected_even_when_well_formed():
    assert ID_PATTERN.match("ZZ-00000001")
    assert not is_valid_id("ZZ-00000001")
    with pytest.raises(ValueError):
        parse_id("ZZ-00000001")


def test_is_valid_id_can_require_a_kind():
    assert is_valid_id("K-00000001", kind=EntityKind.KNOWLEDGE_OBJECT)
    assert not is_valid_id("K-00000001", kind=EntityKind.SOURCE)


def test_non_strings_are_rejected_rather_than_crashing():
    for value in (None, 1, 1.5, [], {}):
        assert not is_valid_id(value)


def test_numbers_start_at_one():
    for number in (0, -1):
        with pytest.raises(ValueError):
            format_id(EntityKind.KNOWLEDGE_OBJECT, number)


def test_prefix_table_is_reportable():
    table = prefixes()
    assert table["K"] == "KNOWLEDGE_OBJECT"
    assert table["S"] == "SOURCE_OCCURRENCE"
    assert table["CA"] == "CONCEPT_ALIAS"
    assert table["DST"] == "DOCUMENT_STRUCTURE"
    assert table["RUN"] == "EXTRACTION_RUN"
    assert table["XIS"] == "EXTRACTION_ISSUE"
    assert table["KE"] == "KNOWLEDGE_EQUIVALENCE"
    assert table["CE"] == "CONCEPT_EQUIVALENCE"
    assert len(table) == 31


def test_phase_3_prefixes_parse_unambiguously():
    """`CA` beside `CALC`, and `CO` beside `CON`, are the new near-collisions.

    They parse correctly for the same reason `S`/`SRC`/`SEG` do: the FIRST hyphen
    delimits the prefix.
    """
    for prefix in ("CA", "CALC", "CO", "CON", "RO", "RUL", "DST", "DOC", "DV"):
        identifier = f"{prefix}-00000001"
        assert parse_id(identifier)[0].value == prefix, identifier
