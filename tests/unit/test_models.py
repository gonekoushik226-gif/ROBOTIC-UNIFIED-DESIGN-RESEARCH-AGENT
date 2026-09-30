"""Model tests (Part 5 sections 184-185, ADR 0005).

`validate()` returns `Result[None]`, and an `Err` must list **every** problem it
found, not just the first, so someone correcting a record sees the whole picture.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from app.core.errors import FailureCategory
from app.core.result import Err, Ok
from app.models import ALL_ENTITIES, Document, KnowledgeObject, Relationship, Source
from app.models.base import (
    is_valid_timestamp,
    parse_timestamp,
    utc_now,
)
from app.models.enums import (
    Authorization,
    CertaintyState,
    DocumentProcessingStatus,
    KnowledgeType,
    LifecycleStatus,
    RelationshipOrigin,
    RelationType,
    SourceAvailability,
    SourceCategory,
)
from app.models.identifiers import EntityKind


# --------------------------------------------------------------------- timestamps


def test_utc_now_is_canonical():
    value = utc_now()
    assert is_valid_timestamp(value)
    assert value.endswith("Z")
    assert parse_timestamp(value).tzinfo is not None


def test_timestamps_sort_chronologically():
    """UTC with a Z suffix means lexicographic order is chronological order."""
    earlier = "2026-09-20T04:46:14.030Z"
    later = "2026-09-20T05:00:00.000Z"
    assert sorted([later, earlier]) == [earlier, later]


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-20T10:16:14.030+05:30",  # offset form: would break ORDER BY
        "2026-09-20T04:46:14Z",           # no milliseconds
        "2026-09-20 04:46:14.030Z",       # space instead of T
        "not a timestamp",
        "",
        None,
        12345,
    ],
)
def test_non_canonical_timestamps_are_rejected(value):
    assert not is_valid_timestamp(value)


# ------------------------------------------------------------------ every entity


def test_all_twenty_two_entities_are_present():
    assert len(ALL_ENTITIES) == 22
    assert len({e.__name__ for e in ALL_ENTITIES}) == 22


def test_every_entity_declares_its_kind_and_table():
    for entity in ALL_ENTITIES:
        assert isinstance(entity.KIND, EntityKind), entity.__name__
        assert entity.TABLE and entity.TABLE.islower(), entity.__name__


def test_entity_kinds_and_tables_are_unique():
    kinds = [e.KIND for e in ALL_ENTITIES]
    tables = [e.TABLE for e in ALL_ENTITIES]
    assert len(set(kinds)) == len(kinds)
    assert len(set(tables)) == len(tables)


def test_every_entity_starts_with_id_and_timestamps():
    for entity in ALL_ENTITIES:
        names = [f.name for f in fields(entity)]
        assert names[:3] == ["id", "created_at", "updated_at"], entity.__name__


def test_every_entity_is_frozen_and_slotted(sample):
    """Part 1 section 24: avoid mutable shared state."""
    document = sample["document"]
    with pytest.raises(AttributeError):
        document.filename = "changed"  # type: ignore[misc]
    assert not hasattr(document, "__dict__")


def test_every_entity_validates_its_own_identifier_kind(sample):
    """A Source carrying a Document's identifier must not validate."""
    source = sample["source"]
    wrong = replace(source, id=sample["document"].id)
    outcome = wrong.validate()
    assert isinstance(outcome, Err)
    assert any("id must be a RUDRA identifier" in p for p in outcome.failure.missing)


# -------------------------------------------------------------------- validation


def _document(**overrides):
    now = utc_now()
    base = dict(
        id="DOC-00000001",
        created_at=now,
        updated_at=now,
        filename="a.pdf",
        original_filename="A.pdf",
        source_type="PDF",
        file_path="/x/a.pdf",
        file_hash="abc123",
        file_size=10,
        mime_type="application/pdf",
        ingested_at=now,
        processing_status=DocumentProcessingStatus.PENDING,
        processing_version=1,
    )
    base.update(overrides)
    return Document(**base)


def test_a_valid_entity_returns_ok():
    assert isinstance(_document().validate(), Ok)


def test_validation_reports_every_problem_at_once():
    """ADR 0005: the point of returning a Result is collecting all the errors."""
    broken = _document(
        id="nonsense",
        filename="   ",
        file_hash="",
        file_size=-1,
        created_at="yesterday",
    )
    outcome = broken.validate()
    assert isinstance(outcome, Err)
    problems = outcome.failure.missing
    assert len(problems) >= 5, problems
    joined = " | ".join(problems)
    assert "id must be" in joined
    assert "filename" in joined
    assert "file_hash" in joined
    assert "file_size" in joined
    assert "created_at" in joined


def test_validation_failure_is_actionable():
    outcome = _document(filename="").validate()
    assert isinstance(outcome, Err)
    report = outcome.failure
    assert report.category is FailureCategory.INVALID_INPUT
    assert report.stage == "models.validate"
    assert report.data_changed is False
    assert report.retry_safe is True
    assert report.next_options


def test_blank_strings_are_not_accepted_as_values():
    """A whitespace-only title is missing information, not a value."""
    assert isinstance(_document(filename="   ").validate(), Err)
    assert isinstance(_document(file_hash="\t").validate(), Err)


def test_optional_fields_may_be_none():
    """Part 2 section 29: unknown metadata must remain unknown."""
    document = _document(document_title=None, author=None, page_count=None)
    assert isinstance(document.validate(), Ok)


def test_negative_page_count_is_rejected():
    assert isinstance(_document(page_count=-1).validate(), Err)


def test_booleans_are_not_accepted_as_integers():
    """bool is a subclass of int in Python; accepting True as 1 would hide a bug."""
    assert isinstance(_document(file_size=True).validate(), Err)


def test_knowledge_version_must_be_at_least_one():
    now = utc_now()
    knowledge = KnowledgeObject(
        id="K-00000001",
        created_at=now,
        updated_at=now,
        knowledge_type=KnowledgeType.DEFINITION,
        canonical_name="X",
        statement="A statement.",
        lifecycle_status=LifecycleStatus.ACTIVE,
        certainty=CertaintyState.REPORTED_BY_SOURCE,
        knowledge_version=0,
    )
    assert isinstance(knowledge.validate(), Err)


def test_confidence_is_bounded():
    now = utc_now()

    def build(confidence):
        return KnowledgeObject(
            id="K-00000001",
            created_at=now,
            updated_at=now,
            knowledge_type=KnowledgeType.DEFINITION,
            canonical_name="X",
            statement="A statement.",
            lifecycle_status=LifecycleStatus.ACTIVE,
            certainty=CertaintyState.REPORTED_BY_SOURCE,
            knowledge_version=1,
            confidence=confidence,
        )

    assert isinstance(build(0.0).validate(), Ok)
    assert isinstance(build(1.0).validate(), Ok)
    assert isinstance(build(None).validate(), Ok)
    assert isinstance(build(1.5).validate(), Err)
    assert isinstance(build(-0.1).validate(), Err)


def _relationship(**overrides):
    now = utc_now()
    values = dict(
        id="REL-00000001",
        created_at=now,
        updated_at=now,
        relation_type=RelationType.RELATED_TO,
        origin=RelationshipOrigin.INFERRED,
        lifecycle_status=LifecycleStatus.ACTIVE,
        from_concept_id="CPT-00000001",
        to_knowledge_id="K-00000001",
    )
    values.update(overrides)
    return Relationship(**values)


def test_a_relationship_cannot_point_at_itself():
    relationship = _relationship(
        from_concept_id=None,
        from_knowledge_id="K-00000001",
        to_knowledge_id="K-00000001",
    )
    outcome = relationship.validate()
    assert isinstance(outcome, Err)
    assert any("may not point at itself" in p for p in outcome.failure.missing)


def test_a_relationship_needs_exactly_one_endpoint_per_side():
    """Decision D-22: zero endpoints and two endpoints are both malformed."""
    none_set = _relationship(from_concept_id=None)
    outcome = none_set.validate()
    assert isinstance(outcome, Err)
    assert any("exactly one of from_" in p for p in outcome.failure.missing)

    both_set = _relationship(from_knowledge_id="K-00000002")
    outcome = both_set.validate()
    assert isinstance(outcome, Err)
    assert any("exactly one of from_" in p for p in outcome.failure.missing)


def test_relationship_endpoint_properties_return_whichever_column_is_set():
    """`from_id`/`to_id` survive as read-only properties (decision D-22)."""
    edge = _relationship()
    assert edge.from_id == "CPT-00000001"
    assert edge.to_id == "K-00000001"

    reversed_edge = _relationship(
        from_concept_id=None, from_knowledge_id="K-00000001", to_concept_id="CPT-00000001",
        to_knowledge_id=None,
    )
    assert reversed_edge.from_id == "K-00000001"
    assert reversed_edge.to_id == "CPT-00000001"


def test_endpoint_properties_refuse_to_guess_on_a_malformed_edge():
    """Part 1 section 6: never invent an answer. No endpoint means no answer."""
    with pytest.raises(ValueError, match="0 from endpoints"):
        _relationship(from_concept_id=None).from_id
    with pytest.raises(ValueError, match="2 from endpoints"):
        _relationship(from_knowledge_id="K-00000002").from_id


def test_relationship_endpoints_are_kind_checked():
    """A knowledge identifier in a concept column must not validate."""
    wrong = _relationship(from_concept_id="K-00000009")
    outcome = wrong.validate()
    assert isinstance(outcome, Err)
    assert any("from_concept_id" in p for p in outcome.failure.missing)


def test_relationship_origin_is_mandatory_and_binary():
    """Part 2 section 40: inferred must never be presented as explicit."""
    assert {member.value for member in RelationshipOrigin} == {"EXPLICIT", "INFERRED"}


def test_source_availability_is_independent_of_knowledge_status():
    """Part 7 section 9: the file lifecycle and the knowledge lifecycle are separate.

    They are separate *types*, so one can never be assigned where the other is
    expected. The vocabularies are not disjoint, and deliberately so: the
    specification lists ARCHIVED in both Part 7 section 8 and section 59, where
    it means an archived file and archived knowledge respectively. What matters
    is that the states which express deletion do not cross over.
    """
    availability = {member.value for member in SourceAvailability}
    lifecycle = {member.value for member in LifecycleStatus}

    assert SourceAvailability is not LifecycleStatus
    # Deleting a file is not deleting knowledge (Part 7 section 1).
    assert "DELETED_BY_USER" in availability and "DELETED_BY_USER" not in lifecycle
    assert "DELETED" in lifecycle and "DELETED" not in availability
    # Knowledge states have no meaning for a file.
    assert not {"ACTIVE", "SUPERSEDED", "CONFLICTED", "DEPRECATED"} & availability
    # The one shared spelling is the documented overlap, nothing more.
    assert availability & lifecycle == {"ARCHIVED"}


def test_source_accepts_a_deleted_file_while_staying_valid():
    """A deleted file does not make the source record invalid."""
    now = utc_now()
    source = Source(
        id="SRC-00000001",
        created_at=now,
        updated_at=now,
        name="Book A",
        source_category=SourceCategory.USER_PROVIDED_SOURCE,
        authorization=Authorization.AUTHORIZED,
        availability=SourceAvailability.DELETED_BY_USER,
        file_hash="8ae115ad",
    )
    assert isinstance(source.validate(), Ok)
    assert source.file_hash == "8ae115ad"


def test_procedure_execution_counts_must_be_consistent():
    from app.models import Procedure
    from app.models.enums import ProcedureDocumentationStatus

    now = utc_now()

    def build(total, good, bad):
        return Procedure(
            id="PRC-00000001",
            created_at=now,
            updated_at=now,
            name="Open application",
            documentation_status=ProcedureDocumentationStatus.DOCUMENTED_PROCEDURE,
            lifecycle_status=LifecycleStatus.ACTIVE,
            execution_count=total,
            successful_executions=good,
            failed_executions=bad,
        )

    assert isinstance(build(5, 3, 2).validate(), Ok)
    assert isinstance(build(5, 4, 2).validate(), Err)
