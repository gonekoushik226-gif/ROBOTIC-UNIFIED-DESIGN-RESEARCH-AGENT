"""Repository tests: the Part 5 section 185 acceptance criteria at unit level.

Create, persist, retrieve, update, identify by stable ID, serialise/deserialise,
validate required fields.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields, replace

import pytest

from app.core.errors import InvalidInputError, RudraError
from app.models import (
    ALL_ENTITIES,
    ENTITIES,
    Authorization,
    Concept,
    Document,
    LifecycleStatus,
    Source,
    SourceAvailability,
    SourceCategory,
)
from app.models.base import utc_now
from app.storage import Repository, connect, from_row, migrate, to_row


def _concept(repo, name="Flip-flop"):
    now = utc_now()
    return Concept(
        id=repo.new_id(Concept),
        created_at=now,
        updated_at=now,
        canonical_name=name,
        lifecycle_status=LifecycleStatus.ACTIVE,
    )


# ------------------------------------------------------------------ create/persist


def test_add_persists_and_returns_the_entity(repo):
    concept = repo.add(_concept(repo))
    assert repo.exists(Concept, concept.id)
    assert repo.count(Concept) == 1


def test_get_returns_an_equal_object(repo):
    concept = repo.add(_concept(repo))
    assert repo.get(Concept, concept.id) == concept


def test_get_returns_none_for_an_unknown_identifier(repo):
    """Absence is an ordinary answer, not a failure."""
    assert repo.get(Concept, "CPT-99999999") is None


def test_identifiers_are_stable_across_reads(repo):
    concept = repo.add(_concept(repo))
    first = repo.get(Concept, concept.id)
    second = repo.get(Concept, concept.id)
    assert first.id == second.id == concept.id


# ------------------------------------------------------------------------ update


def test_update_changes_the_row_and_refreshes_updated_at(repo):
    concept = repo.add(_concept(repo))
    changed = repo.update(replace(concept, canonical_name="Bistable circuit"))

    assert changed.canonical_name == "Bistable circuit"
    assert changed.updated_at >= concept.updated_at
    assert repo.get(Concept, concept.id).canonical_name == "Bistable circuit"
    assert repo.count(Concept) == 1


def test_update_does_not_change_created_at(repo):
    concept = repo.add(_concept(repo))
    changed = repo.update(replace(concept, canonical_name="Other"))
    assert changed.created_at == concept.created_at


def test_updating_something_that_does_not_exist_is_refused(repo):
    """An update that changed nothing must not look like a success."""
    now = utc_now()
    ghost = Concept(
        id="CPT-99999999",
        created_at=now,
        updated_at=now,
        canonical_name="Never stored",
        lifecycle_status=LifecycleStatus.ACTIVE,
    )
    with pytest.raises(InvalidInputError) as caught:
        repo.update(ghost)
    report = caught.value.report
    assert report.data_changed is False
    assert "CPT-99999999" in report.missing


# -------------------------------------------------------------------- validation


def test_invalid_entities_are_never_persisted(repo):
    concept = replace(_concept(repo), canonical_name="   ")
    with pytest.raises(RudraError) as caught:
        repo.add(concept)
    assert caught.value.report.stage == "models.validate"
    assert repo.count(Concept) == 0


def test_the_failure_lists_every_problem(repo):
    broken = replace(_concept(repo), id="not-an-id", canonical_name="")
    with pytest.raises(RudraError) as caught:
        repo.add(broken)
    assert len(caught.value.report.missing) >= 2


def test_update_validates_too(repo):
    concept = repo.add(_concept(repo))
    with pytest.raises(RudraError):
        repo.update(replace(concept, canonical_name=""))
    assert repo.get(Concept, concept.id).canonical_name == "Flip-flop"


def test_a_missing_foreign_key_is_reported_clearly(repo):
    """Referencing a document that does not exist must fail, and say why."""
    now = utc_now()
    orphan = Source(
        id=repo.new_id(Source),
        created_at=now,
        updated_at=now,
        name="Orphan",
        source_category=SourceCategory.LOCAL_SOURCE,
        authorization=Authorization.AUTHORIZED,
        availability=SourceAvailability.AVAILABLE,
        document_id="DOC-99999999",
    )
    with pytest.raises(InvalidInputError) as caught:
        repo.add(orphan)
    report = caught.value.report
    assert "referenced record does not exist" in report.reason
    assert report.data_changed is False


def test_duplicate_file_hash_is_refused(repo, sample):
    """Part 3 section 80: the same file must not become two documents."""
    original = sample["document"]
    twin = replace(
        original,
        id=repo.new_id(Document),
        filename="copy.pdf",
        original_filename="Copy.pdf",
    )
    with pytest.raises(InvalidInputError) as caught:
        repo.add(twin)
    assert "already exists" in caught.value.report.reason


# ------------------------------------------------------- serialise / deserialise


def test_round_trip_preserves_values_and_types(sample):
    for entity in sample.values():
        row = to_row(entity)
        restored = from_row(type(entity), row)
        assert restored == entity
        for field in fields(entity):
            assert type(getattr(restored, field.name)) is type(getattr(entity, field.name))


def test_enums_are_stored_as_readable_text(repo, sample):
    row = repo.connection.execute(
        "SELECT processing_status FROM document WHERE id = ?",
        (sample["document"].id,),
    ).fetchone()
    assert row["processing_status"] == "PROCESSED"


def test_booleans_are_stored_as_zero_or_one(repo):
    from app.models import Intent, RiskLevel

    now = utc_now()
    intent = repo.add(
        Intent(
            id=repo.new_id(Intent),
            created_at=now,
            updated_at=now,
            intent_type="OPEN_APPLICATION",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            target="Chrome",
        )
    )
    stored = repo.connection.execute(
        "SELECT requires_confirmation FROM intent WHERE id = ?", (intent.id,)
    ).fetchone()[0]
    assert stored == 0
    assert repo.get(Intent, intent.id).requires_confirmation is False


def test_none_stays_none(repo):
    concept = repo.add(_concept(repo))
    assert repo.get(Concept, concept.id).description is None


def test_a_row_missing_a_column_is_reported(repo):
    concept = repo.add(_concept(repo))
    row = dict(to_row(concept))
    del row["canonical_name"]
    with pytest.raises(InvalidInputError) as caught:
        from_row(Concept, row)
    assert "canonical_name" in caught.value.report.missing


# --------------------------------------------------------------- every entity


def test_every_entity_can_be_stored_and_read_back(repo):
    """Part 5 section 185 must hold for every entity, not a chosen few."""
    from tests.unit.entity_samples import build_all

    stored = build_all(repo)
    # Every entity type must be covered; the list is longer because the
    # conflicting-claims pair needs two KnowledgeObjects.
    covered = {type(e) for e in stored}
    # Phase 2's guarantee, asserted separately so a later phase cannot erode it.
    assert set(ALL_ENTITIES) <= covered
    assert covered == set(ENTITIES)
    repo.connection.commit()

    for entity in stored:
        restored = repo.get(type(entity), entity.id)
        assert restored == entity, type(entity).__name__


def test_the_database_enforces_types_independently(repo):
    """STRICT tables reject what a dataclass would silently carry (ADR 0004)."""
    now = utc_now()
    with pytest.raises(sqlite3.IntegrityError):
        repo.connection.execute(
            "INSERT INTO document_segment(id, created_at, updated_at, document_id, "
            "page_number, ordinal, text, extraction_method, text_origin) "
            "VALUES ('SEG-00000001', ?, ?, 'DOC-00000001', 'not a number', 0, '', 'x', "
            "'NATIVE_TEXT')",
            (now, now),
        )
    repo.connection.rollback()


def test_the_database_enforces_the_enum_vocabulary(repo):
    """An `origin` outside the Part 2 section 40 vocabulary must be refused.

    The endpoints are REAL rows on purpose. Before Phase 3 this test used
    identifiers that did not exist; once decision D-22 made endpoints foreign keys
    it would still have raised `IntegrityError`, still have passed, and no longer
    have tested the enum at all - it would have been testing the foreign key. The
    assertion on the message is what keeps the two apart.
    """
    now = utc_now()
    first = repo.add(_concept(repo, name="Sequential logic"))
    second = repo.add(_concept(repo, name="Combinational logic"))

    with pytest.raises(sqlite3.IntegrityError) as caught:
        repo.connection.execute(
            "INSERT INTO relationship(id, created_at, updated_at, from_concept_id, "
            "to_concept_id, relation_type, origin, lifecycle_status) VALUES "
            "('REL-00000001', ?, ?, ?, ?, 'RELATED_TO', 'GUESSED', 'ACTIVE')",
            (now, now, first.id, second.id),
        )
    message = str(caught.value)
    assert "CHECK constraint failed" in message, message
    assert "FOREIGN KEY" not in message, message
    repo.connection.rollback()


def test_the_database_enforces_relationship_endpoint_existence(repo):
    """Decision D-22: endpoints are real foreign keys, not format checks."""
    now = utc_now()
    concept = repo.add(_concept(repo))

    with pytest.raises(sqlite3.IntegrityError) as caught:
        repo.connection.execute(
            "INSERT INTO relationship(id, created_at, updated_at, from_concept_id, "
            "to_knowledge_id, relation_type, origin, lifecycle_status) VALUES "
            "('REL-00000001', ?, ?, ?, 'K-00009999', 'DEFINED_BY', 'EXPLICIT', 'ACTIVE')",
            (now, now, concept.id),
        )
    assert "FOREIGN KEY" in str(caught.value)
    repo.connection.rollback()


def test_persistence_survives_closing_the_database(db_path):
    """Objects must outlive the process that created them."""
    first = connect(db_path)
    try:
        migrate(first, database_path=db_path)
        repo = Repository(first)
        concept = repo.add(_concept(repo, name="Persisted"))
        first.commit()
        identifier = concept.id
    finally:
        first.close()

    second = connect(db_path)
    try:
        restored = Repository(second).get(Concept, identifier)
        assert restored is not None
        assert restored.canonical_name == "Persisted"
    finally:
        second.close()
