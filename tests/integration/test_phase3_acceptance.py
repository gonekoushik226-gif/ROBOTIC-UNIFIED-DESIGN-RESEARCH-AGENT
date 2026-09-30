"""Phase 3 acceptance (master specification Part 5 section 187).

Section 187 names eight steps and says "All operations must be testable". Each step
is asserted separately below, and the flow is then driven end to end against a real
database file that is **closed and reopened** between Persist and Retrieve - because
"Persist" that never survives a closed connection has not been demonstrated.

Also covers the two migration paths, since Phase 3 is the first schema change this
project has ever applied to an existing database.
"""

from __future__ import annotations

import pathlib
import sqlite3
from unittest import mock

import pytest

from app.knowledge import ConceptService, Evidence
from app.models import (
    Authorization,
    Concept,
    ConceptOccurrence,
    Document,
    DocumentProcessingStatus,
    KnowledgeType,
    RelationType,
    Relationship,
    RelationshipOccurrence,
    RelationshipOrigin,
    Source,
    SourceAvailability,
    SourceCategory,
)
from app.models.base import utc_now
from app.models.identifiers import EntityKind
from app.storage import Repository, connect, migrate, queries
from app.storage.ids import IdAllocator
from app.storage.migrator import CODE_SCHEMA_VERSION


def _fixture_document_and_source(repo):
    """Phase 4 owns PDFs. Phase 3 needs a document ROW, and reads no file."""
    now = utc_now()
    document = repo.add(
        Document(
            id=repo.new_id(Document), created_at=now, updated_at=now,
            filename="microelectronics.pdf", original_filename="Microelectronics.pdf",
            source_type="PDF", file_path="/documents/microelectronics.pdf",
            file_hash="hash-micro", file_size=8192, mime_type="application/pdf",
            ingested_at=now, processing_status=DocumentProcessingStatus.PROCESSED,
            processing_version=1, document_title="Microelectronic Circuits",
            page_count=1400,
        )
    )
    source = repo.add(
        Source(
            id=repo.new_id(Source), created_at=now, updated_at=now,
            name="Microelectronic Circuits, 7th edition",
            source_category=SourceCategory.USER_PROVIDED_SOURCE,
            authorization=Authorization.AUTHORIZED,
            availability=SourceAvailability.AVAILABLE,
            document_id=document.id, file_hash="hash-micro",
        )
    )
    return document, source


def test_the_section_187_flow_end_to_end(db_path: pathlib.Path):
    """Create concept -> definition -> equation -> relationship -> prerequisite
    -> provenance -> persist -> retrieve."""
    connection = connect(db_path)
    try:
        migrate(connection, database_path=db_path)
        repo = Repository(connection)
        service = ConceptService(repo)
        document, source = _fixture_document_and_source(repo)

        def evidence(text, page):
            return Evidence(
                source_id=source.id, document_id=document.id, text=text,
                page_number=page, section="5.1",
            )

        # 1. Create concept
        mosfet = service.create_concept("MOSFET", context="Analog electronics")
        assert mosfet.canonical_name == "MOSFET"
        assert mosfet.context == "Analog electronics"
        # S-1: the canonical name is itself an alias row, so one lookup path serves.
        assert [a.alias for a in queries.aliases_for_concept(connection, mosfet.id)] == [
            "MOSFET"
        ]

        # 2. Attach definition
        definition, definition_edge = service.attach_definition(
            mosfet.id,
            "A MOSFET is a voltage-controlled semiconductor device.",
            label="MOSFET definition",
            evidence=evidence("A MOSFET is a voltage-controlled semiconductor device.", 412),
        )
        assert definition.knowledge_type is KnowledgeType.DEFINITION
        assert definition_edge.relation_type is RelationType.DEFINED_BY
        assert definition_edge.from_id == mosfet.id
        assert definition_edge.to_id == definition.id

        # 3. Attach equation
        # relation_type is required: the specification names no relation between a
        # concept and its equations (section 39's list is introduced as "Examples:"),
        # so the caller states which one it means.
        equation, equation_knowledge, _ = service.attach_equation(
            mosfet.id,
            "I_D = 0.5 * k * (V_GS - V_th)^2",
            label="Drain current in saturation",
            relation_type=RelationType.USES,
            evidence=evidence("I_D = 0.5k(V_GS - V_th)^2", 415),
        )
        assert equation.knowledge_id == equation_knowledge.id
        # Parsing is Phase 11. Phase 3 stores the equation as written.
        assert equation.canonical_form is None

        # 4. Attach relationship
        transistor = service.create_concept("Transistor")
        hierarchy = service.attach_relationship(
            from_concept_id=transistor.id, to_concept_id=mosfet.id,
            relation_type=RelationType.PARENT_OF,
            origin=RelationshipOrigin.EXPLICIT,
            evidence=evidence("Transistors include MOSFETs and BJTs.", 400),
        )
        assert hierarchy.origin is RelationshipOrigin.EXPLICIT

        # 5. Attach prerequisite
        physics = service.create_concept("Semiconductor physics")
        prerequisite = service.attach_prerequisite(
            mosfet.id, physics.id,
            origin=RelationshipOrigin.EXPLICIT,
            evidence=evidence("MOSFET operation assumes semiconductor physics.", 399),
        )
        assert prerequisite.from_id == physics.id
        assert prerequisite.to_id == mosfet.id

        # 6. Attach provenance
        occurrence = service.attach_concept_provenance(mosfet.id, evidence("MOSFET", 412))
        assert occurrence.surface_form == "MOSFET"
        assert occurrence.page_number == 412

        # 7. Persist
        connection.commit()
        concept_id = mosfet.id
    finally:
        connection.close()

    # 8. Retrieve, through a connection that never saw the writes.
    reopened = connect(db_path)
    try:
        service = ConceptService(Repository(reopened))
        view = service.retrieve(concept_id)

        assert view is not None
        assert view.concept.canonical_name == "MOSFET"
        assert view.concept.context == "Analog electronics"
        assert [a.alias for a in view.aliases] == ["MOSFET"]
        assert [d.knowledge.statement for d in view.definitions] == [
            "A MOSFET is a voltage-controlled semiconductor device."
        ]
        assert [d.relationship.origin for d in view.definitions] == [
            RelationshipOrigin.EXPLICIT
        ]
        assert [e.expression for e in view.equations] == ["I_D = 0.5 * k * (V_GS - V_th)^2"]
        assert {r.relation_type for r in view.relationships} == {
            RelationType.DEFINED_BY, RelationType.USES,
            RelationType.PARENT_OF, RelationType.PREREQUISITE_OF,
        }
        assert [r.from_id for r in view.prerequisites] == [
            r.from_id for r in view.prerequisites if r.relation_type is RelationType.PREREQUISITE_OF
        ]
        assert view.has_provenance
        assert [o.surface_form for o in view.occurrences] == ["MOSFET"]

        # Part 2 section 49, in one query, for every subject kind.
        assert len(queries.evidence_for(reopened, concept_id)) == 1
        kinds = {
            row["subject_kind"]
            for row in reopened.execute("SELECT subject_kind FROM evidence")
        }
        assert kinds == {"CONCEPT", "RELATIONSHIP"}

        assert queries.graph_integrity(reopened).is_clean
    finally:
        reopened.close()


def test_every_section_187_step_is_individually_callable(graph):
    """Section 187: "All operations must be testable."

    A step that only exists inside a larger routine is not separately testable, so
    this asserts each one is reachable on its own.
    """
    service = graph["service"]
    for name in (
        "create_concept", "attach_definition", "attach_equation",
        "attach_relationship", "attach_prerequisite",
        "attach_concept_provenance", "attach_relationship_provenance", "retrieve",
    ):
        assert callable(getattr(service, name)), name


# ---------------------------------------------------------------- migration paths


def test_a_fresh_database_reaches_schema_version_two(db_path: pathlib.Path):
    connection = connect(db_path)
    try:
        report = migrate(connection, database_path=db_path)
        assert report.version_after == CODE_SCHEMA_VERSION >= 2
        assert report.integrity == "ok"
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        assert {"concept_alias", "concept_occurrence", "relationship_occurrence",
                "evidence"} <= names
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def _build_version_one(connection, db_path):
    """Migrate only as far as schema version 1, as Phase 2 left it."""
    from app.storage import migrator

    only_first = tuple(m for m in migrator.available_migrations() if m.version == 1)
    with mock.patch.object(migrator, "available_migrations", lambda: only_first), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 1):
        return migrator.migrate(connection, database_path=db_path)


def test_an_existing_phase_2_database_upgrades_without_losing_data(db_path: pathlib.Path):
    """The first schema change this project has applied to a populated database."""
    connection = connect(db_path)
    try:
        _build_version_one(connection, db_path)
        allocator = IdAllocator(connection)
        now = utc_now()
        concept_id = allocator.next_id(EntityKind.CONCEPT)
        knowledge_id = allocator.next_id(EntityKind.KNOWLEDGE_OBJECT)
        relationship_id = allocator.next_id(EntityKind.RELATIONSHIP)
        connection.execute(
            "INSERT INTO concept(id, created_at, updated_at, canonical_name, "
            "lifecycle_status, description) VALUES (?,?,?,?,?,?)",
            (concept_id, now, now, "MOSFET", "ACTIVE", None),
        )
        connection.execute(
            "INSERT INTO knowledge_object(id, created_at, updated_at, knowledge_type, "
            "canonical_name, statement, lifecycle_status, certainty, knowledge_version) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (knowledge_id, now, now, "DEFINITION", "MOSFET definition",
             "A MOSFET is voltage-controlled.", "ACTIVE", "REPORTED_BY_SOURCE", 1),
        )
        # The old untyped shape: from_id / to_id, no foreign keys.
        connection.execute(
            "INSERT INTO relationship(id, created_at, updated_at, from_id, to_id, "
            "relation_type, origin, lifecycle_status) VALUES (?,?,?,?,?,?,?,?)",
            (relationship_id, now, now, concept_id, knowledge_id, "DEFINED_BY",
             "EXPLICIT", "ACTIVE"),
        )
        connection.commit()

        report = migrate(connection, database_path=db_path)

        assert report.version_before == 1
        assert report.version_after == CODE_SCHEMA_VERSION
        assert report.integrity == "ok"
        assert report.backup_path is not None and report.backup_path.is_file()

        # The untyped endpoints were mapped onto typed columns by prefix.
        edge = connection.execute(
            "SELECT * FROM relationship WHERE id = ?", (relationship_id,)
        ).fetchone()
        assert edge["from_concept_id"] == concept_id
        assert edge["to_knowledge_id"] == knowledge_id
        assert edge["from_knowledge_id"] is None
        assert edge["to_concept_id"] is None

        assert connection.execute("SELECT count(*) FROM concept").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM knowledge_object").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_the_backup_taken_before_upgrading_is_a_readable_version_one_database(
    db_path: pathlib.Path,
):
    connection = connect(db_path)
    try:
        _build_version_one(connection, db_path)
        connection.commit()
        report = migrate(connection, database_path=db_path)
        backup = sqlite3.connect(report.backup_path)
        try:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            # The pre-migration shape is preserved in the backup.
            columns = {row[1] for row in backup.execute("PRAGMA table_info(relationship)")}
            assert "from_id" in columns and "from_concept_id" not in columns
        finally:
            backup.close()
    finally:
        connection.close()


def test_a_relationship_endpoint_phase_3_cannot_represent_aborts_the_migration(
    db_path: pathlib.Path,
):
    """An edge pointing at an equation cannot be typed yet.

    Dropping it silently would lose a stored relationship, so the migration aborts
    and schema version 1 survives intact.
    """
    connection = connect(db_path)
    try:
        _build_version_one(connection, db_path)
        allocator = IdAllocator(connection)
        now = utc_now()
        concept_id = allocator.next_id(EntityKind.CONCEPT)
        equation_id = allocator.next_id(EntityKind.EQUATION)
        connection.execute(
            "INSERT INTO concept(id, created_at, updated_at, canonical_name, "
            "lifecycle_status, description) VALUES (?,?,?,?,?,?)",
            (concept_id, now, now, "MOSFET", "ACTIVE", None),
        )
        connection.execute(
            "INSERT INTO equation(id, created_at, updated_at, expression, "
            "lifecycle_status) VALUES (?,?,?,?,?)",
            (equation_id, now, now, "I = V/R", "ACTIVE"),
        )
        connection.execute(
            "INSERT INTO relationship(id, created_at, updated_at, from_id, to_id, "
            "relation_type, origin, lifecycle_status) VALUES (?,?,?,?,?,?,?,?)",
            (allocator.next_id(EntityKind.RELATIONSHIP), now, now, concept_id,
             equation_id, "USES", "EXPLICIT", "ACTIVE"),
        )
        connection.commit()

        from app.core.errors import StorageError

        with pytest.raises(StorageError) as caught:
            migrate(connection, database_path=db_path)

        assert caught.value.report.data_changed is False
        from app.storage.migrator import schema_version

        assert schema_version(connection) == 1
        # Everything that was there is still there.
        assert connection.execute("SELECT count(*) FROM relationship").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM equation").fetchone()[0] == 1
    finally:
        connection.close()


# -------------------------------------------------- identifier counters (A-8)


def test_fresh_and_upgraded_databases_reach_the_same_seed_state(tmp_path: pathlib.Path):
    """Decision A-8, ADR 0013.

    Before the correction these two paths diverged in OPPOSITE directions once an
    entity kind was added: a fresh database already held the new counter, an
    upgraded one did not, and no single migration statement could satisfy both.
    """
    def database(name):
        root = tmp_path / name
        (root / "database").mkdir(parents=True)
        (root / "backups").mkdir(parents=True)
        return root / "database" / "knowledge.db"

    fresh_path = database("fresh")
    fresh = connect(fresh_path)
    upgraded_path = database("upgraded")
    upgraded = connect(upgraded_path)
    try:
        migrate(fresh, database_path=fresh_path)
        _build_version_one(upgraded, upgraded_path)
        upgraded.commit()
        migrate(upgraded, database_path=upgraded_path)

        fresh_counters = IdAllocator(fresh).counters()
        upgraded_counters = IdAllocator(upgraded).counters()

        assert fresh_counters == upgraded_counters
        assert set(fresh_counters) == {kind.value for kind in EntityKind}
        assert all(value == 1 for value in fresh_counters.values())
    finally:
        fresh.close()
        upgraded.close()


def test_an_existing_counter_is_never_reset_by_a_later_migration(db_path: pathlib.Path):
    """`INSERT OR IGNORE` adds what is missing and leaves what is in use alone.

    Resetting a live counter would re-issue identifiers that already exist, which
    ADR 0006 forbids outright.
    """
    connection = connect(db_path)
    try:
        _build_version_one(connection, db_path)
        allocator = IdAllocator(connection)
        for _ in range(6):
            allocator.next_id(EntityKind.QUERY)
        connection.commit()
        assert allocator.peek(EntityKind.QUERY) == 7

        migrate(connection, database_path=db_path)

        after = IdAllocator(connection)
        assert after.peek(EntityKind.QUERY) == 7
        # And a kind that did not exist at version 1 starts at 1.
        assert after.peek(EntityKind.CONCEPT_ALIAS) == 1
    finally:
        connection.close()


def test_the_phase_3_entity_kinds_are_allocatable(graph):
    repo = graph["repo"]
    for kind, prefix in (
        (EntityKind.CONCEPT_ALIAS, "CA"),
        (EntityKind.CONCEPT_OCCURRENCE, "CO"),
        (EntityKind.RELATIONSHIP_OCCURRENCE, "RO"),
    ):
        assert repo.ids.next_id(kind).startswith(f"{prefix}-")


# ------------------------------------------------------- Part 7 still holds


def test_deleting_a_source_is_still_blocked_by_phase_3_evidence(graph):
    """Part 7: the new evidence tables extend the guarantee, they do not dilute it."""
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    service.attach_concept_provenance(concept.id, graph["evidence"]("MOSFET", 412))
    repo.connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        repo.connection.execute(
            "DELETE FROM source WHERE id = ?", (graph["source"].id,)
        )
    repo.connection.rollback()
    assert repo.count(ConceptOccurrence) == 1


def test_marking_the_file_deleted_preserves_concept_evidence(graph):
    """Part 7 sections 4-7: knowledge and its provenance survive file deletion."""
    from dataclasses import replace

    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    service.attach_concept_provenance(concept.id, graph["evidence"]("MOSFET", 412))
    repo.update(
        replace(graph["source"], availability=SourceAvailability.DELETED_BY_USER)
    )
    repo.connection.commit()

    view = service.retrieve(concept.id)
    assert view.has_provenance
    assert view.occurrences[0].page_number == 412
    assert repo.get(Concept, concept.id) is not None
