"""Phase 22: the final response architecture (ADR 0054 P22-5 ... P22-8; sections 227-230).

What every test holds the builders to: each part of an answer is read from the command's own
answer and never made up; a question nothing stored answers is "Unknown."; one that cannot be
determined says so, with what is missing, why, what is available and the next steps;
disagreeing sources are shown side by side and not resolved.
"""

from __future__ import annotations

from app.orchestration import answers


def _evidence(document: str, page: int, text: str) -> dict:
    return {"document_id": document, "page_number": page, "evidence_text": text}


FOUND = {
    "status": "FOUND", "message": "1 concept(s) answer",
    "concept": {
        "concepts": [{"concept": {"id": "CPT-00000001", "canonical_name": "Resistance"}}],
        "groups": [
            {"name": "Definitions", "items": [{"knowledge": {"id": "K-00000001", "statement":
                                                              "Resistance is defined as the opposition to current."},
                                               "evidence": [_evidence("DOC-00000001", 3, "Resistance is defined as the "
                                                                      "opposition\nto current.")]}]},
            {"name": "Equations", "items": []},
        ],
        "conflicts": [],
    },
}


def test_a_found_definition_with_its_basis_and_sources():
    part = answers.from_query(1, "resistance", "QUERY_CONCEPT", ("query", "--name", "resistance"), FOUND, "definition")
    assert part.status == "ANSWERED" and part.path == "KNOWLEDGE"
    assert part.answer == "Definition: Resistance is defined as the opposition to current."
    assert part.basis == ("CPT-00000001", "K-00000001")
    assert part.sources == ('DOC-00000001 p.3: "Resistance is defined as the opposition to current."',)


def test_a_kind_not_stored_is_unknown_not_guessed():
    part = answers.from_query(1, "resistance", "QUERY_CONCEPT", ("query",), FOUND, "equations")
    assert part.status == "UNKNOWN" and part.answer.startswith("Unknown.") and part.sources == ()


def test_not_found_is_unknown_and_insufficient_is_insufficient_information():
    unknown = answers.from_query(1, "x", "QUERY_CONCEPT", ("query",), {"status": "NOT_FOUND", "message": "none"}, None)
    assert unknown.status == "UNKNOWN" and unknown.answer == "Unknown. none" and unknown.next_steps
    withheld = answers.from_query(1, "x", "QUERY_CONCEPT", ("query",),
                                  {"status": "INSUFFICIENT_AUTHORIZED_INFORMATION", "message": "withheld"}, None)
    assert withheld.status == "INSUFFICIENT" and withheld.answer == "Insufficient information. withheld"


def test_an_application_question_does_not_relabel_mentions_as_an_answer():
    unknown = answers.from_query(1, "MOSFET", "QUERY_CONCEPT", ("query",),
                                 {"status": "FOUND", "concept": {"concepts": [], "groups": []}},
                                 "applications")
    keyword = {"keyword": {"term": "MOSFET", "knowledge": [{"knowledge": {
        "id": "K-00000001", "knowledge_type": "DEFINITION", "statement": "A MOSFET is a device."},
        "evidence": []}], "concepts": [], "pages": []}}
    part = answers.from_mentions(unknown, keyword, "MOSFET", "applications")
    assert part.status == "UNKNOWN"
    assert part.answer.startswith("No application is stored for 'MOSFET'")


def test_a_conflict_is_shown_and_not_resolved():
    conflicted = {**FOUND, "concept": {**FOUND["concept"], "conflicts": [{
        "conflict": {"id": "CON-00000001", "cause": "UNDETERMINED", "context": None},
        "claim_a": {"knowledge_id": "K-00000001", "knowledge": {"statement": "V is 230 V."},
                    "evidence": [_evidence("DOC-00000001", 1, "V is 230 V.")]},
        "claim_b": {"knowledge_id": "K-00000002", "knowledge": {"statement": "V is 240 V."},
                    "evidence": [_evidence("DOC-00000002", 1, "V is 240 V.")]},
    }]}}
    part = answers.from_query(1, "v", "QUERY_CONCEPT", ("query",), conflicted, "definition")
    assert part.conflicts == (
        "Conflict detected: CON-00000001 (cause UNDETERMINED)",
        'Source A: K-00000001 "V is 230 V." - DOC-00000001 p.1: "V is 230 V."',
        'Source B: K-00000002 "V is 240 V." - DOC-00000002 p.1: "V is 240 V."',
        "Resolution: Not automatically selected.",
    )


def test_a_calculated_result_with_its_steps_and_verification():
    answer = {"status": "CALCULATED", "target": "value", "result": {"relation": "=", "displayed": "56088", "unit": ""},
              "steps": [{"text": "value = 123 × 456 = 56088"}], "verification_status": "PENDING", "inputs": [],
              "formulas": [{"text": "value = 123 × 456", "origin": "USER_INPUT"}], "assumptions": []}
    part = answers.from_calculation(2, "value", "CALCULATE", ("calculate",), answer)
    assert part.status == "ANSWERED" and part.answer == "value = 56088"
    assert part.calculation == ("value = 123 × 456 = 56088", "verification: PENDING")


def test_a_calculation_that_cannot_be_determined_says_what_is_missing_and_why():
    answer = {"status": "CANNOT_DETERMINE", "message": "Cannot determine I.", "target": "I", "result": None,
              "missing": [{"symbol": "R", "required_by": ["I = V / R"]}], "steps": [], "assumptions": [],
              "inputs": [{"symbol": "V", "text": "10 V"}], "formulas": [{"text": "I = V / R", "origin": "USER_INPUT"}]}
    part = answers.from_calculation(1, "I", "CALCULATE", ("calculate",), answer)
    assert part.status == "CANNOT_DETERMINE"
    assert part.answer.startswith("I cannot determine this from the currently authorized information.")
    assert (part.missing, part.why, part.available, part.unavailable) == (
        ("R",), ("R is required by I = V / R",), ("V = 10 V",), ("R",))
    assert any("research" in step for step in part.next_steps)


def test_reasoning_that_cannot_be_determined_names_the_missing_concept():
    answer = {"status": "CANNOT_DETERMINE", "message": "Cannot determine resistance.", "methods": [{
        "steps": [], "missing": [{"concept": {"id": "CPT-00000002", "canonical_name": "Current"},
                                  "required_by": [{"concept_id": "CPT-00000001", "relationship_id": "REL-00000003"}]}]}]}
    part = answers.from_reasoning(1, "resistance", "REASON", ("reason", "resistance"), answer)
    assert part.missing == ("Current (CPT-00000002)",) and part.basis == ("REL-00000003",)
    assert part.why == ("Current (CPT-00000002) is required by CPT-00000001 via REL-00000003",)


def test_an_action_part_reports_each_steps_verification():
    report = {"outcome": "DONE", "message": "1 step(s) done and verified.", "workflows": [],
              "execution": {"steps": [{"action": "OPEN_APPLICATION", "status": "VERIFIED", "observed": "new window"}]}}
    part = answers.from_pipeline(1, "Calculator", "ACTIONS", report)
    assert part.path == "ACTION" and part.status == "DONE"
    assert part.actions == ("OPEN_APPLICATION: VERIFIED - new window",) and part.calculation == ()


def test_nothing_is_made_up_from_an_empty_answer():
    part = answers.from_query(1, "x", "QUERY_CONCEPT", ("query",), {"status": "FOUND", "concept": {}}, None)
    assert part.status == "UNKNOWN" and part.sources == () and part.basis == ()


# ------------------------------------------------------------------ keyword mode (ADR 0035/0036 P9-3, P9-22)
#
# `query --keyword` resolves no concept - it reports which stored text matched the term,
# re-read from knowledge.db and scope-checked. Its answer carries the match in `keyword`,
# not `concept`; before this fix `from_query` read only `concept` and reported every
# keyword-mode result "Unknown.", even a FOUND one with real evidence.

KEYWORD_FOUND = {
    "status": "FOUND", "message": "1 knowledge object(s) and 1 concept name(s) match 'flip-flop'.",
    "concept": None,
    "keyword": {
        "term": "flip-flop",
        "knowledge": [{
            "knowledge": {"id": "K-00000001", "knowledge_type": "DEFINITION",
                          "statement": "A flip-flop is a bistable circuit that stores one bit of data."},
            "evidence": [_evidence("DOC-00000001", 2, "A flip-flop is a bistable circuit that stores one bit of data.")],
        }],
        "concepts": [{
            "concept": {"id": "CPT-00000001", "canonical_name": "Flip-flop"},
            "occurrences": [_evidence("DOC-00000001", 2, "Flip-flop")],
        }],
    },
}


def test_a_keyword_hit_is_answered_with_its_basis_and_sources_not_unknown():
    part = answers.from_query(1, "flip-flop", "KEYWORD_SEARCH", ("query", "--keyword", "flip-flop"),
                              KEYWORD_FOUND, None)
    assert part.status == "ANSWERED" and part.path == "KNOWLEDGE"
    assert part.answer == "Definition: A flip-flop is a bistable circuit that stores one bit of data.\nConcept: Flip-flop"
    assert part.basis == ("K-00000001", "CPT-00000001")
    assert part.sources == (
        'DOC-00000001 p.2: "A flip-flop is a bistable circuit that stores one bit of data."',
        'DOC-00000001 p.2: "Flip-flop"',
    )


def test_a_keyword_search_with_no_matches_is_unknown_not_a_crash():
    empty = {**KEYWORD_FOUND, "keyword": {"term": "x", "knowledge": [], "concepts": []}}
    part = answers.from_query(1, "x", "KEYWORD_SEARCH", ("query", "--keyword", "x"), empty, None)
    assert part.status == "UNKNOWN" and part.sources == () and part.basis == ()


def test_concept_mode_is_unaffected_by_the_keyword_field_being_present_and_empty():
    """Every query answer carries a `keyword` key; concept mode's own answer must still
    read only from `concept`, exactly as before."""
    part = answers.from_query(1, "resistance", "QUERY_CONCEPT", ("query", "--name", "resistance"),
                              {**FOUND, "keyword": None}, "definition")
    assert part.answer == "Definition: Resistance is defined as the opposition to current."
