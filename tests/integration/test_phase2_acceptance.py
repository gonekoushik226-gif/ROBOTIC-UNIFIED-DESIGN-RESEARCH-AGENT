"""Phase 2 acceptance test (master specification Part 5 section 185).

    Create objects.
    Persist objects.
    Retrieve objects.
    Update objects.
    Identify objects by stable ID.
    Serialize/deserialize them.
    Validate required fields.

Each is checked below against a real database file, reopened between steps where
persistence is the point, rather than against an in-memory connection that never
has to survive anything.

The second half checks the Part 7 rules the schema exists to enforce, because a
data model that lets a deleted file take knowledge with it would satisfy section
185 and still be wrong.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.errors import RudraError
from app.models import (
    ALL_ENTITIES,
    ENTITIES,
    Concept,
    Document,
    KnowledgeObject,
    LifecycleStatus,
    Source,
    SourceAvailability,
    SourceOccurrence,
)
from app.models.base import utc_now
from app.models.identifiers import EntityKind, parse_id
from app.storage import Repository, connect, from_row, migrate, to_row
from tests.conftest import PROJECT_ROOT


# ------------------------------------------------------- Part 5 section 185


def test_create_and_persist(repo, db_path: Path):
    """Criteria 1 and 2, verified by looking in the file afterwards."""
    concept = repo.add(
        Concept(
            id=repo.new_id(Concept),
            created_at=utc_now(),
            updated_at=utc_now(),
            canonical_name="Flip-flop",
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
    )
    repo.connection.commit()

    raw = sqlite3.connect(db_path)
    try:
        stored = raw.execute(
            "SELECT canonical_name FROM concept WHERE id = ?", (concept.id,)
        ).fetchone()
    finally:
        raw.close()
    assert stored is not None and stored[0] == "Flip-flop"


def test_retrieve_after_reopening_the_database(db_path: Path):
    """Criterion 3. Objects must outlive the connection that created them."""
    first = connect(db_path)
    try:
        migrate(first, database_path=db_path)
        repo = Repository(first)
        concept = repo.add(
            Concept(
                id=repo.new_id(Concept),
                created_at=utc_now(),
                updated_at=utc_now(),
                canonical_name="Persisted",
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )
        first.commit()
        identifier = concept.id
    finally:
        first.close()

    second = connect(db_path)
    try:
        restored = Repository(second).get(Concept, identifier)
        assert restored is not None
        assert restored.canonical_name == "Persisted"
        assert restored.id == identifier
    finally:
        second.close()


def test_update(repo, sample):
    """Criterion 4."""
    document = sample["document"]
    updated = repo.update(replace(document, document_title="Digital Electronics, 2e"))
    repo.connection.commit()

    assert repo.get(Document, document.id).document_title == "Digital Electronics, 2e"
    assert updated.id == document.id
    assert repo.count(Document) == 1


def test_identify_objects_by_stable_id(repo, sample):
    """Criterion 5: identifiers are typed, canonical and stable."""
    for entity in sample.values():
        kind, number = parse_id(entity.id)
        assert kind is type(entity).KIND
        assert number >= 1
        assert repo.get(type(entity), entity.id).id == entity.id


def test_serialize_and_deserialize(sample):
    """Criterion 6, including that the round trip is JSON-safe."""
    for entity in sample.values():
        row = to_row(entity)
        assert json.loads(json.dumps(row)) == row  # plain, serialisable values
        assert from_row(type(entity), row) == entity


def test_validate_required_fields(repo):
    """Criterion 7: an invalid object is refused, and every problem is named."""
    now = utc_now()
    broken = Concept(
        id="not-an-identifier",
        created_at=now,
        updated_at=now,
        canonical_name="",
        lifecycle_status=LifecycleStatus.ACTIVE,
    )
    with pytest.raises(RudraError) as caught:
        repo.add(broken)

    report = caught.value.report
    assert len(report.missing) >= 2
    assert report.data_changed is False
    assert repo.count(Concept) == 0


def test_all_twenty_two_entities_satisfy_the_criteria(repo):
    """Section 184 lists 22 entities; section 185 must hold for each of them."""
    from tests.unit.entity_samples import build_all

    stored = build_all(repo)
    repo.connection.commit()
    covered = {type(e) for e in stored}
    # The section 184 twenty-two remain separately provable (decision D-29).
    assert set(ALL_ENTITIES) <= covered
    assert len(ALL_ENTITIES) == 22
    assert covered == set(ENTITIES)

    for entity in stored:
        restored = repo.get(type(entity), entity.id)
        assert restored == entity, type(entity).__name__
        assert from_row(type(entity), to_row(entity)) == entity


# ------------------------------------------------------------ Part 7 rules


def test_deleting_a_source_row_is_blocked_while_evidence_exists(repo, sample):
    """Part 7: knowledge must not disappear because a source row was removed."""
    with pytest.raises(sqlite3.IntegrityError):
        repo.connection.execute(
            "DELETE FROM source WHERE id = ?", (sample["source"].id,)
        )
    repo.connection.rollback()
    assert repo.count(SourceOccurrence) == 1


def test_deleting_the_file_preserves_knowledge_and_provenance(repo, sample):
    """The supported operation, and the heart of Part 7 sections 4 to 7.

    Marking the file gone must leave the knowledge, the evidence, the hash and
    the metadata exactly as they were.
    """
    source = sample["source"]
    knowledge_before = repo.count(KnowledgeObject)
    occurrences_before = repo.count(SourceOccurrence)

    repo.update(replace(source, availability=SourceAvailability.DELETED_BY_USER))
    repo.connection.commit()

    after = repo.get(Source, source.id)
    assert after.availability is SourceAvailability.DELETED_BY_USER
    # Part 7 section 6: the hash survives and is never recreated.
    assert after.file_hash == source.file_hash
    assert after.name == source.name

    assert repo.count(KnowledgeObject) == knowledge_before
    assert repo.count(SourceOccurrence) == occurrences_before

    occurrence = repo.get(SourceOccurrence, sample["occurrence"].id)
    assert occurrence is not None
    assert occurrence.page_number == 214
    assert occurrence.original_text == "A flip-flop is a bistable circuit."


def test_knowledge_status_is_untouched_by_file_deletion(repo, sample):
    """Part 7 section 9: the two lifecycles are independent."""
    repo.update(
        replace(sample["source"], availability=SourceAvailability.DELETED_BY_USER)
    )
    repo.connection.commit()

    knowledge = repo.get(KnowledgeObject, sample["knowledge"].id)
    assert knowledge.lifecycle_status is LifecycleStatus.ACTIVE


def test_evidence_accessibility_can_be_derived_from_availability(repo, sample):
    """Part 7 sections 11-12: knowledge stays ACTIVE while its evidence is not
    reachable, and the difference must remain visible."""
    repo.update(
        replace(sample["source"], availability=SourceAvailability.DELETED_BY_USER)
    )
    repo.connection.commit()

    reachable = repo.connection.execute(
        "SELECT count(*) FROM source_occurrence o JOIN source s ON s.id = o.source_id "
        "WHERE o.knowledge_id = ? AND s.availability = 'AVAILABLE'",
        (sample["knowledge"].id,),
    ).fetchone()[0]
    assert reachable == 0
    # The knowledge itself is still here and still active.
    assert repo.get(KnowledgeObject, sample["knowledge"].id) is not None


def test_deleting_a_document_with_evidence_is_blocked(repo, sample):
    with pytest.raises(sqlite3.IntegrityError):
        repo.connection.execute(
            "DELETE FROM document WHERE id = ?", (sample["document"].id,)
        )
    repo.connection.rollback()
    assert repo.count(Document) == 1


def test_one_knowledge_object_can_carry_several_occurrences(repo, sample):
    """Part 3 sections 69-71: repeated knowledge is stored once, with many sources."""
    from app.models import Authorization, SourceCategory

    now = utc_now()
    second_document = repo.add(
        replace(
            sample["document"],
            id=repo.new_id(Document),
            filename="other.pdf",
            original_filename="Other.pdf",
            file_hash="a-different-hash",
        )
    )
    second_source = repo.add(
        Source(
            id=repo.new_id(Source),
            created_at=now,
            updated_at=now,
            name="A second textbook",
            source_category=SourceCategory.USER_PROVIDED_SOURCE,
            authorization=Authorization.AUTHORIZED,
            availability=SourceAvailability.AVAILABLE,
            document_id=second_document.id,
            file_hash="a-different-hash",
        )
    )
    repo.add(
        SourceOccurrence(
            id=repo.new_id(SourceOccurrence),
            created_at=now,
            updated_at=now,
            knowledge_id=sample["knowledge"].id,
            source_id=second_source.id,
            document_id=second_document.id,
            original_text="A flip-flop is a bistable circuit.",
            extraction_method="manual",
            extraction_timestamp=now,
            page_number=88,
        )
    )
    repo.connection.commit()

    assert repo.count(KnowledgeObject) == 1
    assert repo.count(SourceOccurrence) == 2


# ---------------------------------------------------------------- the CLI


def run(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    for key in [k for k in env if k.startswith("RUDRA_")]:
        del env[key]
    return subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(project_root)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project_root),
        timeout=120,
    )


def test_db_command_creates_and_reports_the_database(project_root: Path):
    run(project_root)  # create the directory layout first
    result = run(project_root, "db", "--json")
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["created"] is True
    assert payload["schema_version"] == payload["expected_schema_version"]
    assert payload["integrity"] == "ok"
    assert payload["instance_id"]
    # Every persisted entity is reported, not just the section 184 twenty-two -
    # otherwise the CLI would under-report the database by three tables (D-29).
    assert len(payload["row_counts"]) == len(ENTITIES)
    assert {e.__name__ for e in ENTITIES} == set(payload["row_counts"])
    # Phase 2's twenty-two are still all there, asserted separately.
    assert {e.__name__ for e in ALL_ENTITIES} <= set(payload["row_counts"])
    assert set(payload["next_identifiers"]) == {k.value for k in EntityKind}
    assert all(count == 0 for count in payload["row_counts"].values())


def test_db_command_is_idempotent(project_root: Path):
    run(project_root)
    first = json.loads(run(project_root, "db", "--json").stdout)
    second = json.loads(run(project_root, "db", "--json").stdout)

    assert first["created"] is True
    assert second["created"] is False
    assert second["migrations_applied_now"] == []
    assert second["schema_version"] == first["schema_version"]
    assert second["instance_id"] == first["instance_id"]


def test_index_database_is_not_created_in_phase_2(project_root: Path):
    """ADR 0004 defines index.db, but nothing indexes anything yet.

    Creating an empty file would be the placeholder architecture Part 1
    section 22 forbids.
    """
    run(project_root)
    run(project_root, "db")
    assert (project_root / "data" / "database" / "knowledge.db").is_file()
    assert not (project_root / "data" / "indexes" / "index.db").exists()


def test_start_still_behaves_as_phase_1_defined(project_root: Path):
    """Phase 1 behaviour must be preserved: start does not touch the database."""
    result = run(project_root)
    assert result.returncode == 0, result.stderr
    assert "Startup complete" in result.stdout
    assert not (project_root / "data" / "database" / "knowledge.db").exists()
