"""The Phase 9 query engine over real pipeline output (steps 2-8; not the section 199 acceptance).

Generated one-page PDFs go through the real Phase 4 ingestion, Phase 5 extraction and
stage 15 (the Phase 8 `world` fixture), and the engine reads the result through a
read-only connection. What is checked is that the engine sees what the pipelines
store: definitions and owned properties linked across runs and listed once,
shared-name concept records as their basis only, unlinked variables and examples
stated under D1 - and that a query changes no byte of the database.
"""

from __future__ import annotations

import hashlib

from app.models import KnowledgeType
from app.query import AnswerStatus, QueryEngine, QueryRequest, to_json
from app.storage import connect, queries
from tests.integration.test_phase8_acceptance import (  # noqa: F401 - `world` is a fixture
    CAPACITOR,
    COMPANION_LINES,
    OWNED_PROPERTY,
    page,
    world,
)


def _group(section, name):
    return next(group for group in section.groups if group.name == name)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_engine_reads_what_the_pipelines_stored_and_writes_nothing(world):
    world["both"](page("Book P1", CAPACITOR, OWNED_PROPERTY, *COMPANION_LINES), "P1")
    _, second = world["both"](page("Book P2", CAPACITOR, OWNED_PROPERTY), "P2")
    db_path = world["db_path"]
    world["connection"].execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = _sha256(db_path)

    reader = connect(db_path, read_only=True)
    try:
        engine = QueryEngine(reader, database_path=db_path)
        result = engine.run(QueryRequest.concept("Capacitor"))
        again = engine.run(QueryRequest.concept("Capacitor"))
        variables = queries.knowledge_type_counts_for_run(reader, "RUN-00000001")
    finally:
        reader.close()
    assert _sha256(db_path) == before
    assert to_json(result) == to_json(again)

    assert result.status is AnswerStatus.FOUND
    section = result.concept
    assert len(section.concepts) == 2  # one concept per run, both named "Capacitor"
    (definition,) = _group(section, "Definitions").items
    assert definition.knowledge.statement == CAPACITOR and len(definition.links) == 2
    (owned,) = _group(section, "Properties").items
    assert owned.knowledge.statement == OWNED_PROPERTY
    assert {link.edge.relationship.relation_type.value for link in owned.links} == {"HAS_PROPERTY"}
    assert len(owned.links) == 2 and owned.number_of_sources == 2
    # Stage 15's shared-name record joins the two resolved concepts: its basis, once.
    (possible,) = result.possible_equivalents
    assert possible.already_resolved and possible.section is None
    assert [r.basis.value for r in possible.records] == ["SHARED_NAME_OTHER_DOCUMENT"]
    # D1: variables and examples are stored, and are not attached to the concept.
    assert variables.get(KnowledgeType.VARIABLE.value) and variables.get(KnowledgeType.EXAMPLE.value)
    for name in ("Variables", "Examples"):
        group = _group(section, name)
        assert group.items == () and "No stored edge links any" in group.note
    assert {run.extractor_version for run in section.provenance.runs} == {second.run.extractor_version}
    assert not any("below 4" in note for note in result.notes)


def test_a_page_of_a_real_document_is_its_stored_text_and_occurrences(world):
    document, _ = world["both"](page("Book P1", CAPACITOR, OWNED_PROPERTY), "P1")
    reader = connect(world["db_path"], read_only=True)
    try:
        result = QueryEngine(reader, database_path=world["db_path"]).run(
            QueryRequest.page(document.id, 1))
    finally:
        reader.close()

    assert result.status is AnswerStatus.FOUND
    (segment,) = result.page.segments
    assert CAPACITOR in segment.text and OWNED_PROPERTY in segment.text
    statements = {
        o.subject.statement for o in result.page.occurrences if o.evidence.subject_kind == "KNOWLEDGE_OBJECT"
    }
    assert {CAPACITOR, OWNED_PROPERTY} <= statements
    positioned = [o.evidence.char_start for o in result.page.occurrences if o.evidence.char_start is not None]
    assert positioned == sorted(positioned)
