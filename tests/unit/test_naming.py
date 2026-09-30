"""Alias normalisation (decision D-30, ADR 0010).

The rule is deliberately minimal, and the tests that matter most are the ones
proving what it does **not** fold: stripping hyphens would turn normalisation into
an equivalence judgement, which Part 3 section 73 forbids.
"""

from __future__ import annotations

import pytest

from app.models.naming import NORMALIZATION_VERSION, normalize_alias


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("MOSFET", "mosfet"),
        ("MOSFET", "Mosfet"),
        ("Sequential Logic", "sequential logic"),
        ("  padded  ", "padded"),
        ("double  space", "double space"),
        ("tab\tseparated", "tab separated"),
        ("new\nline", "new line"),
    ],
)
def test_typographic_differences_fold_together(left, right):
    assert normalize_alias(left) == normalize_alias(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Part 3 section 73: "Never merge two concepts merely because they have
        # similar names." Deciding these are the same is Phase 8's job, with
        # evidence - not a side effect of a normalisation function.
        ("MOS-FET", "MOSFET"),
        ("MOS transistor", "MOStransistor"),
        ("flip-flop", "flip flop"),
        ("E.M.F.", "EMF"),
    ],
)
def test_judgement_calls_are_left_alone(left, right):
    assert normalize_alias(left) != normalize_alias(right)


def test_unicode_is_normalised_to_a_stable_form():
    """Phase 0 check #3 found real technical text carries these characters."""
    # U+2126 OHM SIGN and U+03A9 GREEK CAPITAL OMEGA are different code points that
    # NFKC folds together; sources spell the same unit either way.
    assert normalize_alias("Ω") == normalize_alias("Ω")
    # Compatibility forms: MICRO SIGN vs GREEK SMALL LETTER MU.
    assert normalize_alias("µF") == normalize_alias("μF")


def test_normalisation_is_idempotent():
    for value in ("MOSFET", "  Mixed  Case  ", "Ω", "MOS-FET"):
        once = normalize_alias(value)
        assert normalize_alias(once) == once


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_a_name_that_normalises_to_nothing_is_refused(value):
    """Storing it would create a row no lookup could ever find."""
    with pytest.raises(ValueError):
        normalize_alias(value)


def test_a_non_string_is_refused():
    with pytest.raises(TypeError):
        normalize_alias(None)  # type: ignore[arg-type]


def test_the_rule_is_versioned():
    """A later change must be detectable; stored values would need recomputing."""
    assert NORMALIZATION_VERSION == 1
