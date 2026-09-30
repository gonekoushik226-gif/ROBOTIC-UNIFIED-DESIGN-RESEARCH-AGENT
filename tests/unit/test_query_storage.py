"""Phase 9 step 1: the read-only `knowledge.db` queries the query engine assembles from.

ADR 0036: concept-equivalence records naming a concept (P9-17); the reverse
`HAS_PROPERTY` lookup behind per-item concept attribution (P9-13); one page's stored
text and the occurrences located on it (P9-21); and the source, document, run and
declared-edition rows behind a set of evidence rows (P9-18).

What every test holds the queries to: stored rows come back exactly as stored - no
inference, merging, deduplication or conflict resolution - in a fixed order whose
last key is the numeric identifier counter, never the identifier string (P9-22,
ADR 0006). Every test works in its own freshly migrated temporary database
(`tests/conftest.py`); the live database is never opened.
"""

from __future__ import annotations

from app.deduplication.editions import declare_edition
from app.models import (
    Authorization,
    ConceptEquivalence,
    ConceptEquivalenceStatus,
    Document,
    ExtractionRunStatus,
    KnowledgeType,
    LifecycleStatus,
    Relationship,
    RelationshipOrigin,
    RelationType,
    SourceAvailability,
)
from app.models.identifiers import EntityKind, format_id, parse_id
from app.models.views import EvidenceContext
from app.storage import connect, queries
from tests.unit.query_rows import STATED
from tests.unit.query_rows import QueryRows as _Rows

HAS_PROPERTY = RelationType.HAS_PROPERTY


def _by_counter(rows) -> tuple:
    return tuple(sorted(rows, key=lambda row: parse_id(row.id)[1]))


# ------------------------------------------------- concept-equivalence records (P9-17)


def test_equivalence_records_naming_a_concept_are_returned_as_stored(repo):
    rows = _Rows(repo)
    x, a, b, c = (rows.concept(name) for name in ("MOS transistor", "MOSFET", "MOSFET", "BJT"))
    run = rows.run(rows.document("book"))
    stated = rows.edge(RelationType.EQUIVALENT_TO, from_concept_id=x.id, to_concept_id=a.id)
    x_a = rows.equivalence(x, a, basis=STATED, relationship=stated, run=run)
    a_b = rows.equivalence(a, b, status=ConceptEquivalenceStatus.NOT_EQUIVALENT)
    b_c = rows.equivalence(b, c)

    found = queries.equivalences_of_concept(repo.connection, a.id)

    assert found == (x_a, a_b)
    assert found == tuple(repo.get(ConceptEquivalence, record.id) for record in found)
    # Either side counts: the concept is `concept_b_id` in one, `concept_a_id` in the other.
    assert (found[0].concept_b_id, found[1].concept_a_id) == (a.id, a.id)
    # Every status and basis is returned as stored - none is filtered or confirmed.
    assert found[0].basis is STATED
    assert (found[0].relationship_id, found[0].extraction_run_id) == (stated.id, run.id)
    assert found[0].shared_name is None
    assert found[1].status is ConceptEquivalenceStatus.NOT_EQUIVALENT
    # Scoped to the concept asked about: the record between two other concepts is absent.
    assert b_c not in found
    assert queries.equivalences_of_concept(repo.connection, c.id) == (b_c,)


def test_equivalence_records_are_in_numeric_counter_order(repo):
    """`CE-100000000` sorts before `CE-99999999` as a string; the counter decides."""
    rows = _Rows(repo)
    a, b, c, d = (rows.concept(name) for name in ("A", "B", "C", "D"))
    first = rows.equivalence(a, b)
    high = rows.equivalence(a, c, format_id(EntityKind.CONCEPT_EQUIVALENCE, 100_000_000))
    low = rows.equivalence(a, d, format_id(EntityKind.CONCEPT_EQUIVALENCE, 99_999_999))
    assert high.id < low.id  # what string order would do

    assert queries.equivalences_of_concept(repo.connection, a.id) == (first, low, high)


def test_a_concept_without_records_gets_an_empty_tuple(repo):
    rows = _Rows(repo)
    a, b, lonely = (rows.concept(name) for name in ("A", "B", "Lonely"))
    rows.equivalence(a, b)

    assert queries.equivalences_of_concept(repo.connection, lonely.id) == ()
    assert queries.equivalences_of_concept(repo.connection, "CPT-99999999") == ()


# --------------------------------------------------- reverse HAS_PROPERTY (P9-13)


def test_has_property_edges_name_every_owner_of_a_property(repo):
    rows = _Rows(repo)
    first_owner, second_owner, other = (rows.concept(n) for n in ("MOSFET", "MOSFET", "BJT"))
    owned = rows.knowledge(
        KnowledgeType.PROPERTY, "The characteristic of a MOSFET is its high input impedance."
    )
    unrelated = rows.knowledge(
        KnowledgeType.PROPERTY, "The characteristic of a BJT is its current gain."
    )
    first = rows.edge(HAS_PROPERTY, from_concept_id=first_owner.id, to_knowledge_id=owned.id)
    second = rows.edge(HAS_PROPERTY, from_concept_id=second_owner.id, to_knowledge_id=owned.id)
    # Not owners of `owned`: another relation type, another object, the other direction.
    rows.edge(RelationType.DEFINED_BY, from_concept_id=other.id, to_knowledge_id=owned.id)
    rows.edge(HAS_PROPERTY, from_concept_id=other.id, to_knowledge_id=unrelated.id)
    rows.edge(HAS_PROPERTY, from_knowledge_id=owned.id, to_concept_id=other.id)

    found = queries.has_property_edges_to(repo.connection, owned.id)

    assert found == (first, second)
    assert found == tuple(repo.get(Relationship, edge.id) for edge in found)
    assert [edge.from_concept_id for edge in found] == [first_owner.id, second_owner.id]
    for edge in found:
        assert edge.relation_type is HAS_PROPERTY
        assert edge.origin is RelationshipOrigin.EXPLICIT
        assert (edge.to_knowledge_id, edge.to_concept_id, edge.from_knowledge_id) == (
            owned.id, None, None,
        )


def test_inactive_owner_edges_are_returned_only_when_asked(repo):
    rows = _Rows(repo)
    former, owner = rows.concept("Capacitor"), rows.concept("Capacitor")
    owned = rows.knowledge(
        KnowledgeType.PROPERTY, "The property of a capacitor is its capacitance."
    )
    retired = rows.edge(
        HAS_PROPERTY, status=LifecycleStatus.DEPRECATED,
        from_concept_id=former.id, to_knowledge_id=owned.id,
    )
    active = rows.edge(HAS_PROPERTY, from_concept_id=owner.id, to_knowledge_id=owned.id)

    assert queries.has_property_edges_to(repo.connection, owned.id) == (active,)
    everything = queries.has_property_edges_to(repo.connection, owned.id, active_only=False)
    assert everything == (retired, active)
    assert everything[0].lifecycle_status is LifecycleStatus.DEPRECATED


def test_owner_edges_are_in_numeric_counter_order(repo):
    rows = _Rows(repo)
    a, b = rows.concept("A"), rows.concept("B")
    owned = rows.knowledge(KnowledgeType.PROPERTY, "The property of A is B.")
    high = rows.edge(
        HAS_PROPERTY, format_id(EntityKind.RELATIONSHIP, 100_000_000),
        from_concept_id=a.id, to_knowledge_id=owned.id,
    )
    low = rows.edge(
        HAS_PROPERTY, format_id(EntityKind.RELATIONSHIP, 99_999_999),
        from_concept_id=b.id, to_knowledge_id=owned.id,
    )

    assert queries.has_property_edges_to(repo.connection, owned.id) == (low, high)


def test_a_property_without_owners_gets_an_empty_tuple(repo):
    rows = _Rows(repo)
    owned = rows.knowledge(KnowledgeType.PROPERTY, "The property of a resistor is its resistance.")

    assert queries.has_property_edges_to(repo.connection, owned.id) == ()
    assert queries.has_property_edges_to(repo.connection, "K-99999999") == ()


# ------------------------------------------------------------------ pages (P9-21)


def test_segments_on_page_are_that_pages_stored_text_in_reading_order(repo):
    rows = _Rows(repo)
    book, other = rows.document("book"), rows.document("other")
    rows.segment(book, 1)
    second_part = rows.segment(book, 2, ordinal=1)  # stored before the first part
    first_part = rows.segment(book, 2, ordinal=0)
    rows.segment(other, 2)

    assert queries.segments_on_page(repo.connection, book.id, 2) == (first_part, second_part)
    assert queries.segments_on_page(repo.connection, book.id, 3) == ()
    assert queries.segments_on_page(repo.connection, "DOC-99999999", 2) == ()


def test_evidence_on_page_holds_every_subject_kind_in_text_order(repo):
    rows = _Rows(repo)
    connection = repo.connection
    book, other = rows.document("book"), rows.document("other")
    source, other_source = rows.source(book), rows.source(other)
    run = rows.run(book)
    page_one = rows.segment(book, 1)
    first_part, second_part = rows.segment(book, 2, 0), rows.segment(book, 2, 1)
    mosfet, amplifier = rows.concept("MOSFET"), rows.concept("Amplifier")
    definition = rows.knowledge(
        KnowledgeType.DEFINITION, "A MOSFET is a voltage-controlled transistor."
    )
    uses = rows.edge(RelationType.USES, from_concept_id=mosfet.id, to_concept_id=amplifier.id)
    # Stored out of text order on purpose.
    later = rows.knowledge_occurrence(
        definition, source, page=2, segment=first_part, span=(10, 20), run=run
    )
    unlocated = rows.knowledge_occurrence(definition, source, page=2, run=run)
    in_second_part = rows.relationship_occurrence(
        uses, source, page=2, segment=second_part, span=(0, 3), run=run
    )
    no_span = rows.concept_occurrence(amplifier, source, page=2, segment=first_part, run=run)
    earliest = rows.concept_occurrence(
        mosfet, source, page=2, segment=first_part, span=(0, 5), run=run
    )
    # Not on page 2 of this book: another page, another document, no page at all.
    rows.concept_occurrence(mosfet, source, page=1, segment=page_one, span=(0, 5), run=run)
    rows.concept_occurrence(mosfet, other_source, page=2)
    rows.knowledge_occurrence(definition, source)

    found = queries.evidence_on_page(connection, book.id, 2)

    # Segment ordinal, then span; an unrecorded span or segment comes after a recorded one.
    assert [row["id"] for row in found] == [
        earliest.id, later.id, no_span.id, in_second_part.id, unlocated.id,
    ]
    assert [(row["subject_kind"], row["subject_id"]) for row in found] == [
        ("CONCEPT", mosfet.id),
        ("KNOWLEDGE_OBJECT", definition.id),
        ("CONCEPT", amplifier.id),
        ("RELATIONSHIP", uses.id),
        ("KNOWLEDGE_OBJECT", definition.id),
    ]
    # Provenance exactly as stored, every identifier included.
    assert found[1] == {
        "id": later.id,
        "subject_kind": "KNOWLEDGE_OBJECT",
        "subject_id": definition.id,
        "source_id": source.id,
        "document_id": book.id,
        "evidence_text": definition.statement,
        "extraction_method": "manual",
        "extraction_timestamp": later.extraction_timestamp,
        "document_version_id": None,
        "segment_id": first_part.id,
        "page_number": 2,
        "section": None,
        "char_start": 10,
        "char_end": 20,
        "extraction_run_id": run.id,
    }
    # The same rows the `evidence` view gives per subject: nothing added or rewritten.
    for row in found:
        (same,) = [e for e in queries.evidence_for(connection, row["subject_id"])
                   if e["id"] == row["id"]]
        assert row == same


def test_evidence_on_page_breaks_ties_by_subject_kind_then_counter(repo):
    rows = _Rows(repo)
    book = rows.document("book")
    source = rows.source(book)
    part = rows.segment(book, 4)
    gate = rows.concept("Gate")
    definition = rows.knowledge(KnowledgeType.DEFINITION, "A gate controls the channel.")
    knowledge_first = rows.knowledge_occurrence(
        definition, source, page=4, segment=part, span=(0, 4)
    )
    concept_second = rows.concept_occurrence(gate, source, page=4, segment=part, span=(0, 4))
    concept_third = rows.concept_occurrence(gate, source, page=4, segment=part, span=(0, 4))

    found = queries.evidence_on_page(repo.connection, book.id, 4)

    assert [row["id"] for row in found] == [
        concept_second.id, concept_third.id, knowledge_first.id,
    ]


def test_a_page_without_evidence_is_empty(repo):
    rows = _Rows(repo)
    book = rows.document("book")
    rows.segment(book, 1)

    assert queries.evidence_on_page(repo.connection, book.id, 1) == ()
    assert queries.evidence_on_page(repo.connection, "DOC-99999999", 1) == ()


# ------------------------------------------------------- evidence context (P9-18)


def test_context_holds_the_source_document_and_run_rows_the_evidence_names(repo):
    rows = _Rows(repo)
    connection = repo.connection
    book_a, book_b, unrelated = (rows.document(name) for name in ("a", "b", "c"))
    source_a, source_b = rows.source(book_a), rows.source(book_b)
    rows.source(unrelated)
    older = rows.run(book_a, 1, version="3", status=ExtractionRunStatus.PARTIAL)
    newer = rows.run(book_a, 2)
    run_b = rows.run(book_b)
    rows.run(unrelated)
    transistor = rows.concept("Transistor")
    definition = rows.knowledge(
        KnowledgeType.DEFINITION, "A transistor is a semiconductor device."
    )
    rows.knowledge_occurrence(definition, source_a, page=1, run=older)
    rows.knowledge_occurrence(definition, source_a, page=1, run=newer)
    rows.concept_occurrence(transistor, source_b, page=7, run=run_b)
    rows.concept_occurrence(transistor, source_b, page=7, run=run_b)  # named twice
    rows.concept_occurrence(transistor, source_b, page=7)  # no run recorded

    evidence = (
        queries.evidence_on_page(connection, book_a.id, 1)
        + queries.evidence_for(connection, transistor.id)
    )
    context = queries.evidence_context(connection, evidence)

    # Each row once, in counter order; nothing the evidence does not name.
    assert context.sources == (source_a, source_b)
    assert context.documents == (book_a, book_b)
    assert context.runs == (older, newer, run_b)
    # As stored: each run's own number, status and extractor version; stored availability.
    assert [(r.run_number, r.status, r.extractor_version) for r in context.runs] == [
        (1, ExtractionRunStatus.PARTIAL, "3"),
        (2, ExtractionRunStatus.COMPLETED, "4"),
        (1, ExtractionRunStatus.COMPLETED, "4"),
    ]
    assert [(s.authorization, s.availability) for s in context.sources] == [
        (Authorization.AUTHORIZED, SourceAvailability.AVAILABLE)
    ] * 2
    assert context.editions == ()


def test_declared_editions_are_matched_to_their_document_by_file_hash(repo):
    rows = _Rows(repo)
    connection = repo.connection
    work, edition, single = (rows.document(name) for name in ("work", "edition", "single"))
    declare_edition(
        repo, edition_id=edition.id, work_id=work.id,
        label="2nd edition", work_label="1st edition",
    )
    (work_row,) = queries.document_versions_by_hash(connection, work.file_hash)
    (edition_row,) = queries.document_versions_by_hash(connection, edition.file_hash)
    definition = rows.knowledge(KnowledgeType.DEFINITION, "A MOSFET is a transistor.")
    for document in (work, edition, single):
        rows.knowledge_occurrence(definition, rows.source(document), page=1)

    def context_of(*documents: Document) -> EvidenceContext:
        evidence: tuple = ()
        for document in documents:
            evidence += queries.evidence_on_page(connection, document.id, 1)
        return queries.evidence_context(connection, evidence)

    assert context_of(edition).editions == (edition_row,)
    # Filed under the work with the user's label, exactly as `edition` wrote it.
    assert (edition_row.document_id, edition_row.version_label, edition_row.file_hash) == (
        work.id, "2nd edition", edition.file_hash,
    )
    assert context_of(work).editions == (work_row,)
    assert (work_row.document_id, work_row.version_label) == (work.id, "1st edition")
    assert context_of(work, edition).editions == _by_counter((work_row, edition_row))
    # No edition declared is reported as absence, never filled in.
    assert context_of(single).editions == ()
    assert context_of(single).documents == (single,)


def test_context_rows_are_in_numeric_counter_order(repo):
    rows = _Rows(repo)
    book = rows.document("book")
    high = rows.source(book, format_id(EntityKind.SOURCE, 100_000_000))
    low = rows.source(book, format_id(EntityKind.SOURCE, 99_999_999))
    diode = rows.concept("Diode")
    rows.concept_occurrence(diode, high, page=1)
    rows.concept_occurrence(diode, low, page=1)

    evidence = queries.evidence_for(repo.connection, diode.id)

    assert queries.evidence_context(repo.connection, evidence).sources == (low, high)


def test_no_evidence_gives_an_empty_context_and_any_iterable_is_read_once(repo):
    rows = _Rows(repo)
    connection = repo.connection
    assert queries.evidence_context(connection, ()) == EvidenceContext()

    book = rows.document("book")
    diode = rows.concept("Diode")
    rows.concept_occurrence(diode, rows.source(book), page=1, run=rows.run(book))
    evidence = queries.evidence_for(connection, diode.id)

    from_generator = queries.evidence_context(connection, (row for row in evidence))
    assert from_generator == queries.evidence_context(connection, evidence)
    assert from_generator.documents == (book,)


# ---------------------------------------------------------------- read-only


def test_the_new_queries_write_nothing_and_run_on_a_read_only_connection(repo, db_path):
    """The query engine opens `knowledge.db` read-only (ADR 0037 P9-31; I7-B)."""
    rows = _Rows(repo)
    book = rows.document("book")
    source, run = rows.source(book), rows.run(book)
    part = rows.segment(book, 1)
    a, b = rows.concept("MOSFET"), rows.concept("MOSFET")
    rows.equivalence(a, b, run=run)
    owned = rows.knowledge(
        KnowledgeType.PROPERTY, "The characteristic of a MOSFET is its high input impedance."
    )
    rows.edge(HAS_PROPERTY, from_concept_id=a.id, to_knowledge_id=owned.id)
    rows.knowledge_occurrence(owned, source, page=1, segment=part, span=(0, 10), run=run)
    rows.concept_occurrence(a, source, page=1, segment=part, span=(21, 27), run=run)
    repo.connection.commit()

    def ask(connection) -> tuple:
        on_page = queries.evidence_on_page(connection, book.id, 1)
        return (
            queries.equivalences_of_concept(connection, a.id),
            queries.has_property_edges_to(connection, owned.id, active_only=False),
            queries.segments_on_page(connection, book.id, 1),
            on_page,
            queries.evidence_context(connection, on_page),
        )

    writable = repo.connection
    before = writable.total_changes
    answer = ask(writable)
    assert all(answer[:4]) and answer[4] != EvidenceContext()  # non-empty, so equality means something
    assert ask(writable) == answer  # the same answer every time
    assert writable.total_changes == before

    reader = connect(db_path, read_only=True)
    try:
        assert ask(reader) == answer
    finally:
        reader.close()


# ------------------------------------------------ added with the engine (steps 2-8)


def test_linked_knowledge_types_are_those_an_active_edge_joins_to_a_concept(repo):
    rows = _Rows(repo)
    concept = rows.concept("MOSFET")
    definition = rows.knowledge(KnowledgeType.DEFINITION, "A MOSFET is a transistor.")
    example = rows.knowledge(KnowledgeType.EXAMPLE, "A MOSFET switches a load.")
    equation = rows.knowledge(KnowledgeType.EQUATION, "I = V / R")
    rows.knowledge(KnowledgeType.VARIABLE, "where R is the resistance")  # no edge at all
    rows.edge(RelationType.DEFINED_BY, from_concept_id=concept.id, to_knowledge_id=definition.id)
    rows.edge(RelationType.RELATED_TO, from_knowledge_id=example.id, to_concept_id=concept.id)
    rows.edge(RelationType.USES, status=LifecycleStatus.DEPRECATED,
              from_concept_id=concept.id, to_knowledge_id=equation.id)
    rows.edge(RelationType.DERIVED_FROM, from_knowledge_id=equation.id, to_knowledge_id=definition.id)

    # Either direction counts; a DEPRECATED edge and an object-to-object edge do not.
    assert queries.knowledge_types_linked_to_concepts(repo.connection) == frozenset(
        {KnowledgeType.DEFINITION, KnowledgeType.EXAMPLE}
    )


def test_a_read_only_connection_says_so(repo, db_path):
    repo.connection.commit()
    assert queries.connection_is_read_only(repo.connection) is False
    reader = connect(db_path, read_only=True)
    try:
        assert queries.connection_is_read_only(reader) is True
    finally:
        reader.close()
