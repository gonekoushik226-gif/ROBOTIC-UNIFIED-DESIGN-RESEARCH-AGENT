"""Phase 8 on the one real technical document - invariants only, never its yield.

ADR 0032 P8-8: section 197 is accepted on generated documents; one real document
exists, so its run is **reported, not a gate**, and nothing is tuned to it. The
document is extracted, classified, re-extracted, classified again and merged, in a
temporary database; the project's own database is untouched. This module asserts
only what must hold on any input and prints the yields for the record.
If the PDF is not on this machine the module is skipped, and says so.
"""

from __future__ import annotations

import time

import pytest

from app.classification import Classifier
from app.deduplication import Merger
from app.documents import IngestionPipeline, PypdfParser
from app.extraction import CATEGORIES, ExtractionPipeline
from app.models import ExtractionTrigger
from app.storage import Repository, connect, migrate, queries
from tests.integration.test_phase5_acceptance import ACCEPTANCE_PDF, ACCEPTANCE_SHA256, _sha256

pytestmark = pytest.mark.skipif(
    not ACCEPTANCE_PDF.exists(),
    reason=f"the section 191 acceptance document is not on this machine: {ACCEPTANCE_PDF}",
)

#: Categories whose items become knowledge objects (the others are concepts and edges).
KNOWLEDGE_CATEGORIES = tuple(
    c for c in CATEGORIES if c not in ("concepts", "prerequisites", "relationships")
)


def _rows(connection) -> dict[str, frozenset]:
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: frozenset(tuple(r) for r in connection.execute(f'SELECT * FROM "{t}"')) for t in tables}


def _count(connection, sql: str, params=()) -> int:
    return connection.execute(sql, params).fetchone()[0]


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    assert _sha256(ACCEPTANCE_PDF) == ACCEPTANCE_SHA256, "this is not the accepted document"
    root = tmp_path_factory.mktemp("phase8_real")
    db = root / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    connection = connect(db)
    migrate(connection, database_path=db)
    connection.commit()
    repository = Repository(connection)
    document = IngestionPipeline(repository, PypdfParser(), root / "documents").ingest(ACCEPTANCE_PDF).document
    connection.commit()

    def extract(trigger):
        pipeline = ExtractionPipeline(repository)
        run = pipeline.start(document.id, trigger=trigger)
        connection.commit()
        started = time.perf_counter()
        report = pipeline.execute(run)
        connection.commit()
        return report, time.perf_counter() - started

    first, first_seconds = extract(ExtractionTrigger.FIRST_EXTRACTION)
    classified_first = Classifier(repository).classify(first.run)
    after_first = _rows(connection)
    knowledge_after_first = _count(connection, "SELECT count(*) FROM knowledge_object")
    second, second_seconds = extract(ExtractionTrigger.USER_REQUESTED)
    classified_second = Classifier(repository).classify(second.run)
    before_merge = _rows(connection)
    merged = Merger(repository).merge()

    print(f"\n  Phase 8 real document: run 1 {first.run.id} {first.run.status} "
          f"{first_seconds:.1f}s; stored {sum(first.stored.values())}, linked "
          f"{sum(first.linked.values())} ({ {k: v for k, v in first.linked.items() if v} }); "
          f"assessments {first.assessments}; conflicts {len(first.conflicts)}; "
          f"HAS_PROPERTY {first.has_property_edges}; R1 edges {classified_first.edges_created}, "
          f"skipped as ambiguous {len(classified_first.definitions_ambiguous)}")
    print(f"  re-extraction {second.run.id} {second_seconds:.1f}s: linked "
          f"{sum(second.linked.values())} of {sum(second.stored[c] for c in KNOWLEDGE_CATEGORIES)}; "
          f"assessments {second.assessments}; concept records {second.concept_equivalences}; "
          f"R1 edges {classified_second.edges_created}, skipped as ambiguous "
          f"{len(classified_second.definitions_ambiguous)}; merge changed {merged.changed}")
    yield {"connection": connection, "first": first, "second": second,
           "classified_first": classified_first, "classified_second": classified_second,
           "after_first": after_first, "knowledge_after_first": knowledge_after_first,
           "before_merge": before_merge, "merged": merged,
           "seconds": (first_seconds, second_seconds)}
    connection.close()


def test_re_extraction_creates_no_knowledge_and_links_every_stored_item(real):
    connection, second = real["connection"], real["second"]
    assert _count(connection, "SELECT count(*) FROM knowledge_object") == real["knowledge_after_first"]
    for category in KNOWLEDGE_CATEGORIES:
        assert second.linked[category] == second.stored[category], category
    assert second.assessments.get("EXACT_DUPLICATE") == sum(second.linked.values())
    assert second.conflicts == ()
    assert second.concept_equivalences == second.issues.get("DUPLICATE_CONCEPT", 0) > 0


def test_later_runs_rewrite_and_delete_no_evidence(real):
    connection, before = real["connection"], real["after_first"]
    after = real["before_merge"]
    for table in ("source_occurrence", "concept_occurrence", "relationship_occurrence",
                  "relationship_inference", "concept", "concept_alias", "document",
                  "document_segment"):
        assert before[table] <= after[table], table


def test_stage_15_records_are_consistent_with_its_issues(real):
    connection = real["connection"]
    conflicts = _count(connection, "SELECT count(*) FROM conflict")
    assert conflicts == _count(
        connection, "SELECT count(*) FROM knowledge_equivalence WHERE outcome = 'CONTRADICTORY'")
    assert conflicts == _count(
        connection, "SELECT count(*) FROM extraction_issue WHERE issue_type = 'POTENTIAL_CONTRADICTION'")
    assert _count(connection, "SELECT count(*) FROM concept_equivalence") == _count(
        connection, "SELECT count(*) FROM extraction_issue WHERE issue_type = 'DUPLICATE_CONCEPT'")
    # Every record from an extraction names its run; merge added none here.
    assert _count(connection, "SELECT count(*) FROM knowledge_equivalence "
                              "WHERE extraction_run_id IS NULL") == 0


def test_rule_r1_skips_linked_definitions_and_reports_it(real):
    """ADR 0033's disclosed consequence for Phase 6: counted, reported, never guessed."""
    second = real["classified_second"]
    assert second.edges_created == 0 and second.definitions_read == 0
    assert len(second.definitions_ambiguous) > 0


def test_merge_on_data_stage_15_wrote_changes_nothing(real):
    assert not real["merged"].changed
    assert _rows(real["connection"]) == real["before_merge"]


def test_whole_database_invariants_on_the_real_document(real):
    connection = real["connection"]
    assert _count(connection, "SELECT count(*) FROM knowledge_object WHERE normalized_hash IS NOT NULL") == 0
    assert _count(connection, "SELECT count(*) FROM knowledge_object WHERE lifecycle_status <> 'ACTIVE'") == 0
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    report = queries.graph_integrity(connection)
    assert report.is_clean and report.inferred_without_basis == ()
    assert _count(connection,
        "SELECT count(*) FROM knowledge_object k WHERE NOT EXISTS ("
        " SELECT 1 FROM source_occurrence s WHERE s.knowledge_id = k.id AND s.page_number IS NOT NULL"
        " AND s.segment_id IS NOT NULL AND s.extraction_run_id IS NOT NULL AND s.char_start IS NOT NULL)") == 0


def test_the_original_pdf_is_intact_and_the_runs_fit_this_machine(real):
    assert _sha256(ACCEPTANCE_PDF) == ACCEPTANCE_SHA256
    assert all(seconds < 120 for seconds in real["seconds"])  # a regression guard only
