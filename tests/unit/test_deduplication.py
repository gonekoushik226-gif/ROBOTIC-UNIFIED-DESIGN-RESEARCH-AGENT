"""Phase 8 rules in isolation: normalisation, rule C1, the exact relation, canonical
members, the two new entities and the property owner (ADRs 0032-0034).

No database here: the in-memory index is built by hand, so each rule is checked on
input whose meaning is known exactly (Part 1 section 24).
"""

from __future__ import annotations

import pytest

from app.deduplication import Item, KnowledgeIndex, c1_contradicts, normalized_statement
from app.deduplication.rules import by_counter, canonical_pair, counter
from app.extraction import detectors
from app.models import (
    ConceptEquivalence,
    ConceptEquivalenceBasis,
    ConceptEquivalenceStatus,
    KnowledgeEquivalence,
    KnowledgeEquivalenceOutcome,
    KnowledgeType,
    RelationType,
    normalize_alias,
)

NOW = "2026-09-23T00:00:00.000Z"
DIODE_07 = "A silicon diode is a diode with a forward voltage of 0.7 V."
DIODE_03 = "A silicon diode is a diode with a forward voltage of 0.3 V."


# ------------------------------------------------------------ normalisation (B)


def test_statements_are_normalised_by_d30_and_nothing_more():
    assert normalized_statement("  A Flip-Flop  is\na bistable\tcircuit. ") == normalize_alias(
        "A Flip-Flop is a bistable circuit."
    )
    # Punctuation and hyphens are kept: removing them would be a judgement (ADR 0010).
    assert normalized_statement("MOS-FET") != normalized_statement("MOSFET")
    assert normalized_statement("x,y") != normalized_statement("x y")


# --------------------------------------------------------------------- rule C1


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (DIODE_07, DIODE_03),
        ("The gain is 5.", "The gain is 10."),
        ("R = 10 ohm and C = 2 F", "R = 20 ohm and C = 3 F"),
        ("The offset is −5 mV", "The offset is 5 mV"),
    ],
)
def test_c1_flags_statements_that_differ_only_in_numeric_values(first, second):
    assert c1_contradicts(first, second)
    assert c1_contradicts(second, first)


@pytest.mark.parametrize(
    ("first", "second", "why"),
    [
        (DIODE_07, DIODE_07, "identical"),
        (DIODE_07, DIODE_07.replace("0.7 V", "0.7 mV"), "a unit differs"),
        (DIODE_07, DIODE_07.replace("0.7 V", "0.70 V"), "the same value"),
        ("The gain is high.", "The gain is 10.", "a word against a number"),
        ("The diode is conducting.", "The diode is not conducting.", "negation (no C2)"),
        ("Req = R1 + R2", "Req = R1 + R3", "a symbol, not a value"),
        ("The gain is 5.", "The gain is 10,", "different trailing punctuation"),
        ("It holds 1,000 bits", "It holds 2,000 bits", "not a plain number"),
        ("Forward voltage 0.6–0.7 V", "Forward voltage 0.5–0.6 V", "a range"),
        ("It is 0.7v here", "It is 0.3v here", "a number fused with a unit"),
        ("The gain is 5 at 1 kHz.", "The gain is 5 at 1 kHz and more.", "token count differs"),
    ],
)
def test_c1_never_broadens_beyond_numeric_value_differences(first, second, why):
    assert not c1_contradicts(first, second), why


# ------------------------------------------------------------------ ordering


def test_counters_order_by_number_not_by_string():
    """ADR 0006 rule 1: never sort by the identifier string."""
    ids = ["K-100000000", "K-99999999", "K-00000002"]
    assert by_counter(ids) == ["K-00000002", "K-99999999", "K-100000000"]
    assert counter("K-100000000") == 100_000_000
    assert sorted(ids) != by_counter(ids)  # string order would be wrong above the ceiling


def test_concept_pairs_are_stored_in_one_canonical_order():
    assert canonical_pair("CPT-00000009", "CPT-00000002") == ("CPT-00000002", "CPT-00000009")
    assert canonical_pair("CPT-00000002", "CPT-00000009") == ("CPT-00000002", "CPT-00000009")


# ------------------------------------------------------ the index (P8-12, P8-13)

DOC = "DOC-00000001"


def _index() -> KnowledgeIndex:
    """Two concepts named Gain (identity) and one named Loss, with a few objects."""
    index = KnowledgeIndex()
    index.set_aliases("CPT-00000001", {"gain"})
    index.set_aliases("CPT-00000002", {"gain"})
    index.set_aliases("CPT-00000003", {"loss"})
    return index


def _put(index, knowledge_id, knowledge_type, statement, location, concept=None):
    index.add(knowledge_id, knowledge_type, statement)
    index.add_location(knowledge_id, location)
    if concept is not None:
        index.attach_concept(knowledge_id, concept)


def test_a_definition_links_only_with_concept_identity_or_at_the_same_location():
    index = _index()
    _put(index, "K-00000001", KnowledgeType.DEFINITION, "The gain is the ratio.", (DOC, 1, 0, 20),
         "CPT-00000001")
    elsewhere = (DOC, 9, 0, 20)
    same_name = Item(KnowledgeType.DEFINITION, "The  gain is the ratio.", elsewhere, "CPT-00000002")
    other_name = Item(KnowledgeType.DEFINITION, "The gain is the ratio.", elsewhere, "CPT-00000003")
    same_place = Item(KnowledgeType.DEFINITION, "The gain is the ratio.", (DOC, 1, 0, 20), "CPT-00000003")
    assert index.exact_matches(same_name) == ["K-00000001"]
    assert index.exact_matches(other_name) == []
    assert index.exact_matches(same_place) == ["K-00000001"]  # section 80, any type


def test_an_equation_is_exact_only_at_the_same_location():
    index = _index()
    _put(index, "K-00000001", KnowledgeType.EQUATION, "V = IR", (DOC, 1, 5, 11))
    assert index.exact_matches(Item(KnowledgeType.EQUATION, "V = IR", (DOC, 2, 5, 11))) == []
    assert index.exact_matches(Item(KnowledgeType.EQUATION, "V = IR", (DOC, 1, 5, 11))) == ["K-00000001"]
    # Identical elsewhere: a POSSIBLE_DUPLICATE target, never a link (section 77).
    assert index.same_statement(Item(KnowledgeType.EQUATION, "V = IR", (DOC, 2, 5, 11))) == ["K-00000001"]


@pytest.mark.parametrize(
    "knowledge_type",
    [KnowledgeType.PROPERTY, KnowledgeType.UNIT, KnowledgeType.RULE, KnowledgeType.VARIABLE,
     KnowledgeType.PROCEDURE, KnowledgeType.EXAMPLE],
)
def test_other_types_link_on_an_identical_statement_anywhere(knowledge_type):
    index = _index()
    _put(index, "K-00000001", knowledge_type, "Current is measured in ampere.", (DOC, 1, 0, 30))
    item = Item(knowledge_type, "current is  measured in AMPERE.", ("DOC-00000002", 4, 7, 37))
    assert index.exact_matches(item) == ["K-00000001"]
    assert index.exact_matches(Item(knowledge_type, "Current is measured in volt.", (DOC, 4, 0, 1))) == []


def test_the_smallest_counter_is_linked_and_is_the_canonical_member():
    index = _index()
    for knowledge_id in ("K-00000010", "K-00000002", "K-00000100"):
        _put(index, knowledge_id, KnowledgeType.RULE, "Never short a source.", (DOC, 1, 0, 9))
    assert index.exact_matches(Item(KnowledgeType.RULE, "Never short a source.", (DOC, 3, 0, 9))) == [
        "K-00000002", "K-00000010", "K-00000100",
    ]
    assert index.duplicate_groups() == [["K-00000002", "K-00000010", "K-00000100"]]
    assert index.is_canonical("K-00000002")
    assert not index.is_canonical("K-00000010") and not index.is_canonical("K-00000100")


def test_possible_duplicate_candidates_are_canonical_same_concept_and_not_identical():
    index = _index()
    ratio = "The gain is the ratio of output to input."
    _put(index, "K-00000001", KnowledgeType.DEFINITION, ratio, (DOC, 1, 0, 9), "CPT-00000001")
    # A stored exact duplicate of K-1 (same concept identity) - non-canonical.
    _put(index, "K-00000002", KnowledgeType.DEFINITION, ratio, ("DOC-00000002", 1, 0, 9), "CPT-00000002")
    _put(index, "K-00000003", KnowledgeType.DEFINITION, "Loss is waste.", (DOC, 2, 0, 9), "CPT-00000003")
    aliases = index.concept_aliases("CPT-00000002")
    new = normalized_statement("The gain is the ratio of the controller output to the error.")
    assert index.same_concept(KnowledgeType.DEFINITION, new, aliases) == ["K-00000001"]
    assert index.same_concept(KnowledgeType.DEFINITION, normalized_statement(ratio), aliases) == []


def test_a_property_is_attached_only_through_has_property():
    index = _index()
    index.add("K-00000001", KnowledgeType.PROPERTY, "The characteristic of gain is x.")
    index.attach_concept("K-00000001", "CPT-00000001", RelationType.DEFINED_BY)  # wrong edge
    assert index.object_aliases("K-00000001") == frozenset()
    index.attach_concept("K-00000001", "CPT-00000001", RelationType.HAS_PROPERTY)
    assert index.object_aliases("K-00000001") == {"gain"}


def test_a_removed_object_is_no_longer_compared():
    index = _index()
    _put(index, "K-00000001", KnowledgeType.UNIT, "measured in volt", (DOC, 1, 0, 9))
    index.remove("K-00000001")
    assert index.exact_matches(Item(KnowledgeType.UNIT, "measured in volt", (DOC, 1, 0, 9))) == []
    assert index.ids() == []


# ------------------------------------------------------------ the new entities


def _assessment(**overrides) -> KnowledgeEquivalence:
    fields = dict(
        id="KE-00000001", created_at=NOW, updated_at=NOW, canonical_knowledge_id="K-00000001",
        outcome=KnowledgeEquivalenceOutcome.POSSIBLE_DUPLICATE, rule="P8-13", rule_version="1",
        other_knowledge_id="K-00000002",
    )
    fields.update(overrides)
    return KnowledgeEquivalence(**fields)


def test_an_assessment_record_is_valid_in_each_emitted_shape():
    assert _assessment().validate().is_ok()
    assert _assessment(outcome=KnowledgeEquivalenceOutcome.EXACT_DUPLICATE, other_knowledge_id=None,
                       linked_occurrence_id="S-00000009", extraction_run_id="RUN-00000001",
                       rule="P8-12").validate().is_ok()
    assert _assessment(outcome=KnowledgeEquivalenceOutcome.CONTRADICTORY, rule="C1",
                       conflict_id="CON-00000001").validate().is_ok()


@pytest.mark.parametrize(
    "overrides",
    [
        {"other_knowledge_id": None},  # no other side
        {"linked_occurrence_id": "S-00000001"},  # two other sides
        {"other_knowledge_id": "K-00000001"},  # compared with itself
        {"outcome": KnowledgeEquivalenceOutcome.CONTRADICTORY},  # contradiction without conflict
        {"conflict_id": "CON-00000001"},  # conflict without contradiction
        {"other_knowledge_id": None, "linked_occurrence_id": "S-00000001"},  # link not exact
        {"rule": " "},
    ],
)
def test_an_assessment_record_refuses_every_malformed_shape(overrides):
    assert not _assessment(**overrides).validate().is_ok()


def _concept_record(**overrides) -> ConceptEquivalence:
    fields = dict(
        id="CE-00000001", created_at=NOW, updated_at=NOW, concept_a_id="CPT-00000001",
        concept_b_id="CPT-00000002", status=ConceptEquivalenceStatus.POSSIBLE_EQUIVALENT,
        basis=ConceptEquivalenceBasis.SHARED_NAME_OTHER_DOCUMENT, rule="P8-16", rule_version="1",
        shared_name="gain",
    )
    fields.update(overrides)
    return ConceptEquivalence(**fields)


def test_a_concept_record_needs_canonical_order_and_its_evidence():
    assert _concept_record().validate().is_ok()
    assert _concept_record(basis=ConceptEquivalenceBasis.STATED_EQUIVALENT_TO, shared_name=None,
                           relationship_id="REL-00000001").validate().is_ok()
    assert not _concept_record(concept_a_id="CPT-00000002", concept_b_id="CPT-00000001").validate().is_ok()
    assert not _concept_record(shared_name=None).validate().is_ok()
    assert not _concept_record(basis=ConceptEquivalenceBasis.STATED_EQUIVALENT_TO).validate().is_ok()


def test_the_vocabularies_are_complete_and_spelled_as_the_specification_spells_them():
    assert {o.value for o in KnowledgeEquivalenceOutcome} == {
        "EXACT_DUPLICATE", "SEMANTICALLY_EQUIVALENT", "PARTIALLY_OVERLAPPING",
        "RELATED_BUT_DISTINCT", "CONTEXT_DEPENDENT", "CONTRADICTORY", "UNKNOWN",
        "POSSIBLE_DUPLICATE",
    }
    assert {s.value for s in ConceptEquivalenceStatus} == {
        "CONFIRMED_EQUIVALENT", "POSSIBLE_EQUIVALENT", "NOT_EQUIVALENT", "UNKNOWN",
    }
    assert RelationType.HAS_PROPERTY.value == "HAS_PROPERTY"


# -------------------------------------------------- the property owner (P8-24)


def test_only_the_property_of_x_form_records_an_owner():
    text = (
        "Capacitance is the property of a capacitor to store charge.\n"
        "The characteristic of a capacitor is its ability to store charge.\n"
    )
    first, second = detectors.detect_properties(1, text)
    # Pattern 1: the subject is the property itself - no owner, as before.
    assert (first.subject, first.owner) == ("Capacitance", None)
    assert first.statement == "Capacitance is the property of a capacitor to store charge."
    # Pattern 2: the subject is the owner (article removed by the term normaliser).
    assert (second.subject, second.owner) == ("Capacitor", "Capacitor")
    assert second.statement == "The characteristic of a capacitor is its ability to store charge."


def test_recording_the_owner_changes_no_statement_subject_or_count():
    """The patterns and what they capture are exactly as Phase 5 left them."""
    text = (
        "Resistance is the property of a material to oppose the flow of current.\n"
        "1. Inductance: Inductance is the property of a coil to oppose change.\n"
        "The properties of resistors are well known in every circuit book.\n"
        "The property of a resistor is that it dissipates energy as heat.\n"
    )
    found = detectors.detect_properties(1, text)
    assert [(p.subject, p.statement) for p in found] == [
        ("Resistance", "Resistance is the property of a material to oppose the flow of current."),
        ("Inductance", "Inductance is the property of a coil to oppose change."),
        ("Resistor", "The property of a resistor is that it dissipates energy as heat."),
    ]
    assert [p.owner for p in found] == [None, None, "Resistor"]
