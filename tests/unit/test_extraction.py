"""Phase 5 extraction stages 9-13 in isolation (ADR 0017, ADR 0022).

Each Part 5 section 190 category is tested both ways - that it finds what it
claims to find, and that it declines what it says it does not recognise. The
second half matters more: section 190 ends "Do not claim to extract something that
the implementation does not actually extract", and a detector that fires on the
wrong thing is precisely such a claim.

The texts are small, written here, and ASCII - they are fixtures, not the
acceptance document. The real document is exercised in
`tests/integration/test_phase5_acceptance.py`.
"""

from __future__ import annotations

import pytest

from app.extraction import classify, detectors, normalize, validate
from app.extraction.candidates import (
    EquationCandidate,
    PageCandidates,
    RelationshipCandidate,
    Span,
)
from app.models import ExtractionIssue, ExtractionIssueType
from app.models.enums import PHASE_8_ONLY_ISSUES, RelationType
from app.models.naming import normalize_alias

# ------------------------------------------------------- stage 9: classification


def _one_page(text: str) -> classify.PageView:
    return classify.classify_pages(((1, text),))[0]


def test_masking_preserves_every_offset():
    text = "A node is a junction.\n(A) 5 V\n(B) 10 V\nWhat is the current?\nMore prose here."
    view = _one_page(text)
    assert len(view.expository) == len(text)
    assert len(view.instructional) == len(text)
    # Line breaks survive, so line structure (and therefore spans) is unchanged.
    assert view.expository.count("\n") == text.count("\n")


def test_multiple_choice_options_are_masked_because_distractors_are_false():
    """The acceptance document's p129 option "(iv) Resonant frequency depends on
    resistance." is false. Options must never reach a claim detector."""
    view = _one_page("Intro sentence stays in place.\n(iv) Resonant frequency depends on resistance.")
    assert "Resonant" not in view.expository
    assert "Intro sentence" in view.expository


def test_questions_blanks_and_year_tags_are_masked():
    view = _one_page(
        "Which parameters are used in the analysis of transistors?\n"
        "The Thevenin voltage across a-b is ______.\n"
        "The circuit shown was asked in the exam. [2015]"
    )
    assert not view.expository.strip()


def test_question_sections_persist_across_pages_until_a_chapter_opener():
    views = classify.classify_pages(
        (
            (1, "A branch is a path between two nodes.\nExercises\nA node is a point."),
            (2, "Another practice statement that must be masked."),
            (3, "Superposition theorem statement comes first.\nCHAPTER HIGHLIGHTS"),
        )
    )
    assert "branch" in views[0].expository
    assert "node is a point" not in views[0].expository
    assert not views[1].expository.strip()
    assert views[1].fully_question
    # The opener is emitted at the END of page 3 (as on the acceptance document),
    # yet the whole page is expository again.
    assert "Superposition theorem statement" in views[2].expository


def test_worked_example_stems_are_masked_but_kept_for_the_example_detector():
    view = _one_page(
        "Example 1\nA capacitor of 100 mF stores 10 mJ of energy.\nSolution\n"
        "The charge follows from the energy relation."
    )
    assert "capacitor of 100 mF" not in view.expository
    assert "capacitor of 100 mF" in view.instructional
    assert "charge follows" in view.expository


# -------------------------------------------------------------- stage 13: names


def test_term_normalisation_touches_names_only_and_never_folds_unicode():
    assert normalize.term("a branch") == "Branch"
    assert normalize.term("For example, a linear element") == "Linear element"
    assert normalize.term("\\ Output current") == "Output current"
    # NFKC would turn the superscript into "2"; collapse() must not.
    assert normalize.collapse("x²   =  y") == "x² = y"


def test_labels_are_bounded():
    label = normalize.label("Equation", "x" * 500)
    assert len(label) < 80 and label.startswith("Equation: ")


# ----------------------------------------------- categories 1-2: concepts, definitions


@pytest.mark.parametrize(
    ("sentence", "term"),
    [
        ("A point at which two or more elements are connected together is called node.", "Node"),
        ("A part of the network that connects the various points is called a branch.", "Branch"),
        ("The voltage is defined as the work required to move a unit charge.", "Voltage"),
        ("A tree is a connected subgraph of a network that has no closed paths.", "Tree"),
        ("Potential difference in electrical terminology is known as voltage and is denoted by V.", "Voltage"),
    ],
)
def test_definitional_sentences_yield_a_concept_and_a_definition(sentence, term):
    concepts, definitions = detectors.detect_definitions(3, sentence)
    assert [c.name for c in concepts] == [term]
    assert [d.concept_name for d in definitions] == [term]
    assert definitions[0].page_number == 3


def test_a_glossary_label_whose_body_does_not_define_it_yields_a_concept_only():
    """Under the label "Current", the acceptance document explains free electrons.
    That explanation is not a definition of current, and must not be stored as one."""
    concepts, definitions = detectors.detect_definitions(
        1, "2. Current: There are free electrons available in all conductive materials."
    )
    assert [c.name for c in concepts] == ["Current"]
    assert definitions == ()


def test_a_glossary_label_whose_body_defines_it_yields_both():
    concepts, definitions = detectors.detect_definitions(
        1, "1. Electron: Electron is a mobile charge carrier in a conductor."
    )
    assert [c.name for c in concepts] == ["Electron"]
    assert definitions[0].statement == "Electron is a mobile charge carrier in a conductor."


@pytest.mark.parametrize(
    "sentence",
    [
        "The current flows through the resistor and heats it.",  # no definitional verb
        "Two elements are said to be in series only when currents are the same.",  # prepositional
        "This is called the solution.",  # stop term
    ],
)
def test_non_definitions_yield_nothing(sentence):
    concepts, definitions = detectors.detect_definitions(1, sentence)
    assert definitions == ()


def test_a_definition_span_points_back_into_the_page():
    text = "Heading line\nA point at which two or more elements meet is called node.\n"
    _concepts, definitions = detectors.detect_definitions(1, text)
    span = definitions[0].span
    assert text[span.start: span.end].strip().startswith("A point at which")


# ---------------------------------------------------------------- category 3: equations


def test_symbolic_equations_are_found_and_numeric_substitutions_marked():
    found = detectors.detect_equations(1, "V = IR\nVL = 10 V\nL = b - n + 1 = 55 - 11 + 1 = 45\nq = CV")
    by_expr = {e.expression: e.numeric for e in found}
    assert by_expr["V = IR"] is False
    assert by_expr["q = CV"] is False
    assert by_expr["VL = 10 V"] is True
    assert by_expr["L = b - n + 1 = 55 - 11 + 1 = 45"] is True


def test_condition_lines_are_not_equations():
    found = detectors.detect_equations(
        1, "\n".join(("1. If Z = R or Z = L", "Given i(0+) = 0 and", "V = IR"))
    )
    assert [e.expression for e in found] == ["V = IR"]


def test_a_chain_ending_in_a_value_with_a_unit_word_is_numeric():
    found = detectors.detect_equations(1, "VX = Vth = 8 volts")
    assert found[0].numeric is True


def test_a_sentence_containing_equals_is_prose_not_an_equation():
    found = detectors.detect_equations(
        1, "If w equals the angular frequency then the resulting current becomes = something larger"
    )
    assert found == ()


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("dQ== or v", "two '=' signs"),
        ("P = dW", "differential"),
        ("i(t) = dq t", "differential"),
        ("L= . () Volts", "empty parentheses"),
        ("V = d", "lone 'd'"),
        ("Power (P) = Energy", "single word"),
        ("Rj Xj XLL C+ + -  = Rj X", "two operators in a row"),
        ("di= * f", "opens with an operator"),
        ("W = Pd tV Id t Vt", "'d t' split by a space"),
        ("i(0+) = 0 and", "mid-clause"),
        ("Y = a □ b", "empty box"),
    ],
)
def test_flattened_equations_are_recognised_and_never_repaired(expression, fragment):
    problem = validate.check_equation_structure(expression)
    assert problem is not None and fragment in problem


@pytest.mark.parametrize(
    "expression",
    ["V = IR", "Req = R1 + R2", "I1 = Z2*I/(Z1 + Z2)", "V2 = Z21I1 + Z22I2", "dW = Pdt"],
)
def test_sound_equations_pass_the_structure_check(expression):
    assert validate.check_equation_structure(expression) is None


# ---------------------------------------------------------------- category 4: variables


def test_variable_legends_are_found():
    found = detectors.detect_variables(
        1, "The resistance of a wire is given by R = rL/A, where r is the resistivity of the material."
    )
    assert [(v.symbol, v.name) for v in found] == [("r", "resistivity")]


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        # Both shapes are the acceptance document's own sentences (pages 1 and 2).
        ("Potential difference in electrical terminology is known as voltage and is "
         "denoted by V, and it is expressed in terms of energy.", ("V", "voltage")),
        ("Power is the rate of change of energy and is denoted by P in this text.", ("P", "power")),
    ],
)
def test_denoted_by_names_the_quantity_never_a_fragment(sentence, expected):
    """Regression: a fixed-width capture once named V as "rical terminology is
    known as voltage" - a false name, which is worse than none."""
    found = detectors.detect_variables(1, sentence)
    assert [(v.symbol, v.name) for v in found] == [expected]


def test_a_symbol_never_introduced_is_reported_not_given_a_meaning():
    page = PageCandidates(
        page_number=4,
        segment_id="SEG-00000004",
        equations=(
            EquationCandidate(page_number=4, span=Span(0, 6), text="Z = XY", expression="Z = XY"),
        ),
    )
    findings = validate.unknown_variables((page,))
    assert [f.issue_type for f in findings] == [ExtractionIssueType.UNKNOWN_VARIABLE]
    assert "'Z'" in findings[0].detail


# -------------------------------------------------------------------- category 5: units


def test_explicit_unit_statements_are_found_and_bare_quantities_are_not():
    found = detectors.detect_units(
        1, "The electric current is measured in Ampere (A). A 10 V source drives 5 mA."
    )
    assert [u.unit for u in found] == ["Ampere"]


def test_expressed_in_a_non_unit_word_is_not_a_unit():
    assert detectors.detect_units(1, "A sinusoid can be expressed in either sine or cosine form.") == ()


def test_an_unknown_unit_is_recorded_not_invented():
    page = PageCandidates(page_number=1, segment_id="SEG-00000001",
                          units=detectors.detect_units(1, "It is measured in furlongs (fl)."))
    result = validate.validate_page(page, known_terms=frozenset(), needs_ocr=False)
    assert result.survivors.units == ()
    assert [f.issue_type for f in result.findings] == [ExtractionIssueType.UNKNOWN_UNIT]


# --------------------------------------------------------------- category 6: properties


def test_property_statements_are_found_inside_a_glossary_entry():
    found = detectors.detect_properties(
        2, "2. Resistance: Electrical resistance is the property of material to oppose current."
    )
    assert [p.subject for p in found] == ["Electrical resistance"]


def test_figure_captions_mentioning_characteristics_are_not_properties():
    assert detectors.detect_properties(5, "Figure 1 Ideal voltage source and V-I characteristics.") == ()


# -------------------------------------------------------------------- category 7: rules


def test_modal_statements_and_named_laws_are_rules():
    found = detectors.detect_rules(
        1,
        "Dependent sources are never deactivated during analysis.\n"
        "Kirchhoff's Voltage Law states that the sum of voltages around a loop is zero.",
    )
    assert len(found) == 2
    assert found[1].name == "Kirchhoff's Voltage Law"


@pytest.mark.parametrize(
    "sentence",
    [
        "Steps to Apply Superposition Theorem are listed on this page for reference.",
        "This theorem is used to find the value of load resistance in the circuit.",
        "Thevenin's theorem was published in the year eighteen eighty three.",
    ],
)
def test_merely_mentioning_a_theorem_is_not_a_rule(sentence):
    """ADR 0017 category 7 is deontic and invariant statements, not mentions."""
    assert detectors.detect_rules(1, sentence) == ()


def test_if_then_rules_record_their_precondition():
    found = detectors.detect_rules(
        1, "If the network is linear, then superposition must apply to every source."
    )
    assert found[0].preconditions and found[0].output


# ----------------------------------------------------------------- category 8: examples


def test_examples_take_their_statement_from_prose_only():
    found = detectors.detect_examples(3, "Example 1\nA capacitor of 100 mF stores 10 mJ of energy.")
    assert found[0].label == "Example 1"
    assert "capacitor" in found[0].statement


def test_an_example_that_is_a_figure_keeps_only_its_label():
    found = detectors.detect_examples(7, "Example\nA\n+\n-V1 L\nC")
    assert found[0].statement == "Example"


# --------------------------------------------------------------- category 9: procedures


def test_step_sequences_are_procedures():
    found = detectors.detect_procedures(
        28, "Step 1: Select a single source acting alone.\nStep 2: Find the response.\n"
        "Step 3: Repeat for every source."
    )
    assert len(found) == 1 and len(found[0].steps) == 3


def test_numbered_lists_are_not_procedures_zero_yield_is_correct():
    """625 numbered lines on the acceptance document; four are steps. A numbered
    definition list must not become a procedure."""
    text = "1. Electron: a mobile charge carrier.\n2. Current: the flow of charge.\n3. Voltage: work per charge."
    assert detectors.detect_procedures(1, text) == ()


def test_a_single_step_is_not_a_sequence():
    assert detectors.detect_procedures(1, "Step 1: Do this and nothing else.") == ()


# ------------------------------------------------------------ category 10: prerequisites


def test_stated_prerequisites_are_found():
    found = detectors.detect_prerequisites(
        1, "Network synthesis requires a knowledge of Laplace transforms, and of poles."
    )
    assert [(p.concept_name, p.prerequisite_name) for p in found] == [
        ("Network synthesis", "Laplace transforms")
    ]


def test_ordering_and_mentions_are_not_prerequisites_zero_yield_is_correct():
    """The acceptance document has no prerequisite language. Mentioning a concept
    earlier, or in a later chapter, is not a stated prerequisite - that inference
    is Phase 6's."""
    text = (
        "Ohm's law relates voltage and current in a resistor.\n"
        "Using Ohm's law, the Thevenin equivalent can be found for this circuit."
    )
    assert detectors.detect_prerequisites(1, text) == ()


# ------------------------------------------------------------ category 11: relationships


def test_the_specifications_own_example_is_a_composed_of_relationship():
    """Part 2 section 40: "A full adder consists of two half adders and an OR gate." """
    found = detectors.detect_relationships(
        1, "A full adder consists of half adders and an OR gate."
    )
    assert [(r.subject, r.relation_type, r.object) for r in found] == [
        ("Full adder", RelationType.COMPOSED_OF, "Half adders")
    ]


def test_relationships_need_both_endpoints_to_be_defined_concepts():
    candidate = RelationshipCandidate(
        page_number=1, span=Span(0, 10), text="This theorem is applicable to only linear networks.",
        subject="This theorem", object="Only linear networks", relation_type=RelationType.APPLIES_TO,
    )
    page = PageCandidates(page_number=1, segment_id="SEG-00000001", relationships=(candidate,))
    result = validate.validate_page(page, known_terms=frozenset(), needs_ocr=False)
    assert result.survivors.relationships == ()
    assert [f.issue_type for f in result.findings] == [ExtractionIssueType.BROKEN_RELATIONSHIP]

    known = frozenset({normalize_alias("This theorem"), normalize_alias("Only linear networks")})
    assert validate.validate_page(page, known_terms=known, needs_ocr=False).survivors.relationships


# ------------------------------------------------------------ stage 12: glyph loss


@pytest.mark.parametrize(
    "text",
    [
        "Linear circuits follow Ohm's law (V a I ohm's Law) for every element.",  # proportional sign lost
        "\\ Output current depends on the input voltage.",  # therefore sign lost
        "Each resistor has a value of 1 □ in the ladder.",  # ohm lost to a box
    ],
)
def test_glyph_loss_is_detected(text):
    assert validate.glyph_loss(text) is not None


def test_a_definition_with_glyph_loss_is_not_stored_and_is_recorded():
    concepts, definitions = detectors.detect_definitions(
        83, "It is called a linear circuit when V a I holds for every element."
    )
    page = PageCandidates(page_number=83, segment_id="SEG-00000083",
                          concepts=concepts, definitions=definitions)
    result = validate.validate_page(page, known_terms=frozenset(), needs_ocr=False)
    assert result.survivors.definitions == ()
    finding = result.findings[0]
    assert finding.issue_type is ExtractionIssueType.OCR_UNCERTAINTY
    assert "No OCR ran" in finding.detail


def test_glyph_loss_is_recorded_even_where_no_detector_fired():
    """Page 83 of the acceptance document: no detector matches the sentence, yet
    the corruption must still become an issue - verbatim, once per signature."""
    text = ("It is the circuit whose parameters remain constant with\n"
            "change in applied voltage or current (V a I ohm's Law).\n")
    page = PageCandidates(page_number=83, segment_id="SEG-00000083")
    result = validate.validate_page(page, known_terms=frozenset(), needs_ocr=False, page_text=text)
    assert [f.issue_type for f in result.findings] == [ExtractionIssueType.OCR_UNCERTAINTY]
    assert result.findings[0].excerpt == "change in applied voltage or current (V a I ohm's Law)."
    assert "No OCR ran" in result.findings[0].detail


def test_a_clean_page_records_no_glyph_loss():
    page = PageCandidates(page_number=1, segment_id="SEG-00000001")
    result = validate.validate_page(
        page, known_terms=frozenset(), needs_ocr=False, page_text="A node is a junction of branches."
    )
    assert result.findings == ()


# ---------------------------------------------------- stage 12: section 60 vocabulary


def test_phase_8_issue_types_are_accepted_since_stage_15_emits_them():
    """Refused until Phase 8; stage 15 now emits both (decision P8-17, ADR 0033)."""
    for issue_type in PHASE_8_ONLY_ISSUES:
        issue = ExtractionIssue(
            id="XIS-00000001", created_at="2026-09-21T00:00:00.000Z",
            updated_at="2026-09-21T00:00:00.000Z", extraction_run_id="RUN-00000001",
            document_id="DOC-00000001", issue_type=issue_type, detail="stage 15 finding",
        )
        assert issue.validate().is_ok()


def test_the_vocabulary_holds_all_nine_section_60_checks():
    assert len(ExtractionIssueType) == 9
    assert len(PHASE_8_ONLY_ISSUES) == 2


def test_a_page_needing_ocr_is_reported():
    page = PageCandidates(page_number=9, segment_id="SEG-00000009")
    result = validate.validate_page(page, known_terms=frozenset(), needs_ocr=True)
    assert [f.issue_type for f in result.findings] == [ExtractionIssueType.OCR_UNCERTAINTY]
    assert "D-04" in result.findings[0].detail


def test_candidates_without_a_segment_are_not_stored():
    page = detectors.detect_all(1, "", "A tree is a connected subgraph of a network.", "")
    result = validate.validate_page(page, known_terms=frozenset(), needs_ocr=False)
    assert result.survivors.total == 0
    assert [f.issue_type for f in result.findings] == [ExtractionIssueType.MISSING_SOURCE_REFERENCE]
