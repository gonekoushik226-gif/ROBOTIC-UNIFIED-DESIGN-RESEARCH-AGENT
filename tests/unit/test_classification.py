"""Phase 6 unit tests: rule R1 as pure functions, the basis model, the `RI` prefix.

Rule R1 (ADR 0028) is tested here on hand-written strings, where every expected
match can be read off the text. The end-to-end tests over real pipeline output are
in `tests/integration/test_phase6_acceptance.py`.
"""

from __future__ import annotations

import random

from app.classification.rule_r1 import (
    RULE,
    RULE_VERSION,
    DefinitionText,
    build_name_set,
    canonical_pair,
    normalise_with_positions,
    scan_definition,
)
from app.core.result import Err, Ok
from app.models import (
    ALL_ENTITIES,
    PHASE_6_ENTITIES,
    EntityKind,
    RelationshipInference,
    normalize_alias,
    parse_id,
)

NOW = "2026-09-23T00:00:00.000Z"


def _scan(text: str, names: dict[str, list[str]], concept_id: str = "CPT-00000001"):
    """Scan `text`, given each concept's names as a person would write them.

    R1 reads stored `normalized_alias` values (I6-E); the helper produces them the
    way `ConceptService.add_alias` stores them.
    """
    definition = DefinitionText(
        concept_id=concept_id, knowledge_id="K-00000001", occurrence_id="S-00000001", text=text
    )
    stored = {concept: [normalize_alias(n) for n in names_] for concept, names_ in names.items()}
    return scan_definition(definition, build_name_set(stored))


def _mentioned(scan) -> list[tuple[str, str]]:
    return [(m.mentioned_concept_id, m.matched_text) for m in scan.mentions]


# ----------------------------------------------------------- model and prefix


def test_relationship_inference_is_the_one_phase_6_entity():
    assert PHASE_6_ENTITIES == (RelationshipInference,)
    assert RelationshipInference not in ALL_ENTITIES  # the section 184 22 stay frozen
    assert RelationshipInference.KIND is EntityKind.RELATIONSHIP_INFERENCE
    assert RelationshipInference.KIND.value == "RI"
    assert RelationshipInference.TABLE == "relationship_inference"


def test_the_ri_prefix_parses_unambiguously_beside_rel_rul_and_run():
    for prefix in ("RI", "REL", "RUL", "RUN"):
        assert parse_id(f"{prefix}-00000001")[0].value == prefix


def _inference(**changes) -> RelationshipInference:
    fields = dict(
        id="RI-00000001", created_at=NOW, updated_at=NOW, relationship_id="REL-00000001",
        rule=RULE, rule_version=RULE_VERSION, basis_occurrence_id="S-00000001",
        matched_text="carry",
    )
    fields.update(changes)
    return RelationshipInference(**fields)


def test_a_complete_basis_is_valid():
    assert isinstance(_inference().validate(), Ok)


def test_a_basis_must_name_at_least_one_basis_column():
    """ADR 0027 rule 1, stated in the model as well as in the schema triggers."""
    outcome = _inference(basis_occurrence_id=None).validate()
    assert isinstance(outcome, Err)
    assert any("at least one basis" in problem for problem in outcome.failure.missing)


def test_a_basis_refuses_wrong_kinds_and_blank_text():
    outcome = _inference(
        relationship_id="CPT-00000001", basis_occurrence_id="K-00000001",
        rule=" ", rule_version="", matched_text=" ",
    ).validate()
    assert isinstance(outcome, Err)
    assert len(outcome.failure.missing) == 5


# ------------------------------------------------------------- R1: matching


def test_a_mention_must_be_a_whole_word():
    names = {"CPT-00000001": ["Loop"], "CPT-00000002": ["Node"]}
    for text in ("A loop has nodes.", "A loop is a supernode.", "A loop has node2.",
                 "A loop has 2node."):
        assert _mentioned(_scan(text, names)) == [], text
    for text in ("A loop ends at a node.", "A loop (node) ends.", "A loop: node-to-node."):
        assert _mentioned(_scan(text, names))[0] == ("CPT-00000002", "node"), text


def test_plural_inflected_and_abbreviated_forms_do_not_match():
    """ADR 0028 negative case: no stemming, no plural folding, no abbreviations."""
    names = {"CPT-00000001": ["Mesh"], "CPT-00000002": ["Resistor"]}
    for text in ("A mesh contains resistors.", "A mesh is resistorlike.", "A mesh has a res."):
        assert _mentioned(_scan(text, names)) == [], text


def test_the_longest_name_consumes_a_shorter_name_inside_it():
    """ADR 0028 step 4: 'resistance' inside 'equivalent resistance' is consumed."""
    names = {
        "CPT-00000001": ["Thevenin theorem"],
        "CPT-00000002": ["Resistance"],
        "CPT-00000003": ["Equivalent resistance"],
    }
    scan = _scan("The Thevenin theorem uses the equivalent resistance seen at the port.", names)
    assert _mentioned(scan) == [("CPT-00000003", "equivalent resistance")]


def test_a_shorter_name_still_matches_where_it_stands_alone():
    names = {
        "CPT-00000001": ["Thevenin theorem"],
        "CPT-00000002": ["Resistance"],
        "CPT-00000003": ["Equivalent resistance"],
    }
    scan = _scan(
        "The Thevenin theorem uses the equivalent resistance, not any single resistance.", names
    )
    assert _mentioned(scan) == [
        ("CPT-00000003", "equivalent resistance"),
        ("CPT-00000002", "resistance"),
    ]


def test_the_defining_concepts_own_names_are_skipped_and_consume_their_text():
    """ADR 0028 step 5. 'resistance' inside X's own name is not a mention of Y."""
    names = {"CPT-00000001": ["Equivalent resistance"], "CPT-00000002": ["Resistance"]}
    scan = _scan("An equivalent resistance replaces a network of resistors.", names)
    assert _mentioned(scan) == []
    assert scan.own_name_matches == 1


def test_a_name_shared_by_two_concepts_is_ambiguous_and_skipped():
    """Part 6 section 46's "Gain": never guess which concept a shared name means."""
    names = {
        "CPT-00000001": ["Amplifier"],
        "CPT-00000002": ["Gain"],
        "CPT-00000003": ["Gain"],
        "CPT-00000004": ["Output"],
    }
    scan = _scan("An amplifier raises the gain of its output.", names)
    assert _mentioned(scan) == [("CPT-00000004", "output")]
    assert scan.ambiguous_matches == ("gain",)


def test_matching_uses_decision_d30_normalisation_only():
    """NFKC, casefold and whitespace collapse - and nothing else (ADR 0010)."""
    names = {"CPT-00000001": ["Band"], "CPT-00000002": ["Low-pass  filter"]}
    # A ligature, capitals and a line break all fold for matching; the hyphen is
    # kept. The stored text is still what the source printed (I6-B).
    scan = _scan("A BAND is passed by a LOW-PASS\n  ﬁlter.", names)
    assert _mentioned(scan) == [("CPT-00000002", "LOW-PASS\n  \ufb01lter")]
    assert scan.mentions[0].name == "low-pass filter"
    # Folding the hyphen away would be an equivalence judgement, not normalisation.
    assert _mentioned(_scan("A band is passed by a lowpass filter.", names)) == []


def test_a_hyphen_is_a_word_boundary_as_adr_0028_defines_it():
    """'No letter or digit immediately before or after' - a hyphen is neither.

    Recorded so the behaviour is a known consequence of the approved definition
    rather than a surprise: 'reciprocal' is found inside 'non-reciprocal' unless
    'non-reciprocal' is itself a name, in which case the longer name consumes it.
    """
    names = {"CPT-00000001": ["Network"], "CPT-00000002": ["Reciprocal"]}
    assert _mentioned(_scan("A network may be non-reciprocal.", names)) == [
        ("CPT-00000002", "reciprocal")
    ]
    names["CPT-00000003"] = ["Non-reciprocal"]
    assert _mentioned(_scan("A network may be non-reciprocal.", names)) == [
        ("CPT-00000003", "non-reciprocal")
    ]


def test_the_hyphen_rule_links_a_hyphenated_spelling_to_the_shorter_name():
    """I6-K, ratified 2026-09-23, on the shape of the one real case (Node - Super
    node): the definition's own name is spelled 'super-node', which is not the
    stored name 'super node', so 'node' after the hyphen is a mention of Node."""
    names = {"CPT-00000001": ["Super node"], "CPT-00000002": ["Node"]}
    scan = _scan("Such a group of nodes is called ‘super-node’.", names)
    assert _mentioned(scan) == [("CPT-00000002", "node")]
    assert scan.own_name_matches == 0


def test_every_occurrence_is_found_and_the_direction_is_kept():
    names = {"CPT-00000002": ["Current"], "CPT-00000009": ["Port"]}
    text = "A port carries current in and current out."
    scan = _scan(text, names, concept_id="CPT-00000009")
    first = text.index("current")
    second = text.index("current", first + 1)
    assert [(m.start, m.matched_text) for m in scan.mentions] == [
        (first, "current"), (second, "current")
    ]
    for mention in scan.mentions:
        assert mention.mentioning_concept_id == "CPT-00000009"  # X, whose definition it is
        assert mention.mentioned_concept_id == "CPT-00000002"  # Y, the name it contains
        assert mention.basis_occurrence_id == "S-00000001"
        assert mention.pair == ("CPT-00000002", "CPT-00000009")  # canonical, unordered


# ----------------------------------------------- R1: the stored matched text (I6-B)


def _assert_is_source_text(scan) -> None:
    text = scan.definition.text
    for mention in scan.mentions:
        assert text[mention.raw_start:mention.raw_end] == mention.matched_text
        assert normalize_alias(mention.matched_text) == mention.name


def test_matched_text_is_exactly_what_the_source_printed():
    names = {"CPT-00000001": ["Loop"], "CPT-00000002": ["Current"],
             "CPT-00000003": ["Voltage divider"]}
    scan = _scan("The Loop joins a CURRENT source to a Voltage\n  Divider.", names)
    assert _mentioned(scan) == [
        ("CPT-00000002", "CURRENT"), ("CPT-00000003", "Voltage\n  Divider"),
    ]
    _assert_is_source_text(scan)


def test_compatibility_forms_map_back_to_the_characters_the_source_used():
    """NFKC and casefold change lengths; the mapping still lands on the source text."""
    cases = [
        ("\ufb01lter", "A band \ufb01lter is used.", "\ufb01lter"),  # ligature expands
        ("Strasse", "A road is a Stra\u00dfe here.", "Stra\u00dfe"),  # casefold expands
        ("Caf\u00e9", "The Cafe\u0301 opens.", "Cafe\u0301"),  # composed by NFKC
        ("\uac00", "It is near \u1100\u1161 today.", "\u1100\u1161"),  # Hangul jamo
        ("Ohm", "It is 5 \u2126HM wide.", None),  # U+2126 folds to omega, not o: no match
    ]
    for name, text, expected in cases:
        scan = _scan(text, {"CPT-00000001": ["Zzz"], "CPT-00000002": [name]})
        found = [m.matched_text for m in scan.mentions]
        assert found == ([] if expected is None else [expected]), (name, text)
        _assert_is_source_text(scan)


def test_a_match_whose_source_text_cannot_be_proved_is_skipped_and_counted():
    """'\u00bd' normalises to '1\u20442': a match starting inside it has no exact
    source text, so it is skipped rather than given one it did not print."""
    names = {"CPT-00000001": ["Mix"], "CPT-00000002": ["2 cups"], "CPT-00000003": ["Cups"]}
    scan = _scan("A mix uses \u00bd cups of flour.", names)
    assert scan.mentions == ()
    assert scan.unmappable_matches == ("2 cups",)  # and it consumed "cups" (I6-C)


def test_the_position_map_is_proved_against_the_d30_rule():
    for raw in ("  Two\tspaces \u00a0 here  ", "A \u00a8 spacing diaeresis",
                "Stra\u00dfe \ufb01 Cafe\u0301", "\u1100\u1161\u11a8 x", "plain"):
        text, spans = normalise_with_positions(raw)
        assert text == normalize_alias(raw)
        assert len(spans) == len(text)
        assert all(0 <= a < b <= len(raw) for a, b in spans)
    assert normalise_with_positions("   ") is None


# ------------------------------------------------ R1: stored names (I6-E)


def test_the_name_set_uses_stored_names_exactly_as_given():
    """No recomputation: a name that was not stored normalised simply does not match."""
    entries = build_name_set({"CPT-00000001": ["Node"], "CPT-00000002": ["branch"]})
    assert [e.name for e in entries] == ["branch", "Node"]
    definition = DefinitionText(
        concept_id="CPT-00000003", knowledge_id="K-00000001", occurrence_id="S-00000001",
        text="A mesh joins a node to a branch.",
    )
    assert [m.name for m in scan_definition(definition, entries).mentions] == ["branch"]


def test_blank_evidence_yields_nothing():
    names = {"CPT-00000001": ["Node"], "CPT-00000002": ["Branch"]}
    assert _scan("   ", names).mentions == ()


# ---------------------------------------------------------- R1: determinism


def test_the_name_set_is_scanned_longest_first_then_by_name_then_by_concept_id():
    names = {
        "CPT-00000005": ["ab"],
        "CPT-00000004": ["ba"],
        "CPT-00000003": ["abc"],
        "CPT-00000002": ["ab"],
        "CPT-00000001": ["zz top"],
    }
    order = [(e.name, e.concept_ids) for e in build_name_set(names)]
    assert order == [
        ("zz top", ("CPT-00000001",)),
        ("abc", ("CPT-00000003",)),
        ("ab", ("CPT-00000002", "CPT-00000005")),
        ("ba", ("CPT-00000004",)),
    ]


def test_the_output_does_not_depend_on_input_order():
    names = {
        f"CPT-{i:08d}": [name]
        for i, name in enumerate(
            ["Node", "Branch", "Loop", "Mesh", "Super node", "Tree", "Co-tree", "Graph"], 1
        )
    }
    text = "A super node joins a node and a branch; a loop in a graph is a mesh or tree."
    expected = _scan(text, names, concept_id="CPT-00000004")
    shuffled_keys = list(names)
    for seed in range(20):
        random.Random(seed).shuffle(shuffled_keys)
        shuffled = {key: names[key] for key in shuffled_keys}
        assert _scan(text, shuffled, concept_id="CPT-00000004") == expected


def test_canonical_pair_orders_by_identifier_only():
    assert canonical_pair("CPT-00000009", "CPT-00000002") == ("CPT-00000002", "CPT-00000009")
    assert canonical_pair("CPT-00000002", "CPT-00000009") == ("CPT-00000002", "CPT-00000009")
