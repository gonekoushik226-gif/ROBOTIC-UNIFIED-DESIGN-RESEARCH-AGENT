"""Phase 6 on the one real technical document - invariants only, never its yield.

Decision P6-10 (ADR 0026): section 193 is accepted on a synthetic fixture; the
real-document run is **reported with honest yields, not an acceptance gate, and
nothing is tuned to it**. So this module asserts only what must hold on any input -
no Phase 5 row changes, every edge has a resolvable basis, one edge per unordered
pair, repeatability - and prints the yield for the record instead of asserting it.

Everything runs in a temporary database; the project's own database is untouched.
The document is extracted afresh here by the unchanged Phase 5 pipeline. If the PDF
is not on this machine the module is skipped, and says so.
"""

from __future__ import annotations

import time

import pytest

from app.classification import Classifier, select_run
from app.documents import IngestionPipeline, PypdfParser
from app.extraction import ExtractionPipeline
from app.extraction.normalize import collapse
from app.models import ExtractionRunStatus, normalize_alias
from app.storage import Repository, connect, migrate, queries
from tests.integration.test_phase5_acceptance import ACCEPTANCE_PDF, ACCEPTANCE_SHA256, _sha256

pytestmark = pytest.mark.skipif(
    not ACCEPTANCE_PDF.exists(),
    reason=f"the section 191 acceptance document is not on this machine: {ACCEPTANCE_PDF}",
)


def _rows(connection) -> dict[str, frozenset]:
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: frozenset(tuple(r) for r in connection.execute(f'SELECT * FROM "{t}"')) for t in tables}


@pytest.fixture(scope="module")
def classified(tmp_path_factory):
    assert _sha256(ACCEPTANCE_PDF) == ACCEPTANCE_SHA256, "this is not the accepted document"
    root = tmp_path_factory.mktemp("phase6_real")
    db = root / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    connection = connect(db)
    migrate(connection, database_path=db)
    connection.commit()
    repository = Repository(connection)
    document = IngestionPipeline(repository, PypdfParser(), root / "documents").ingest(ACCEPTANCE_PDF).document
    connection.commit()
    pipeline = ExtractionPipeline(repository)
    run = pipeline.start(document.id)
    connection.commit()
    pipeline.execute(run)
    connection.commit()

    before = _rows(connection)
    started = time.perf_counter()
    report = Classifier(repository).classify(
        select_run(repository, run_id=None, document_id=document.id)
    )
    seconds = time.perf_counter() - started
    yield {"connection": connection, "repository": repository, "document": document,
           "report": report, "before": before, "seconds": seconds}
    connection.close()


def test_the_real_run_is_partial_and_is_classified_with_that_disclosed(classified):
    report = classified["report"]
    assert report.run.status is ExtractionRunStatus.PARTIAL
    assert report.partial and sum(report.issue_counts.values()) > 0


def test_no_phase_5_row_changes_on_the_real_document(classified):
    connection, before = classified["connection"], classified["before"]
    after = _rows(connection)
    for table, rows in before.items():
        if table in ("relationship", "relationship_inference", "id_sequence"):
            continue
        assert after[table] == rows, table
    assert before["relationship"] <= after["relationship"]
    new = after["relationship"] - before["relationship"]
    assert len(new) == classified["report"].edges_created


def test_every_real_inferred_edge_has_a_basis_resolving_to_its_page_and_span(classified):
    connection = classified["connection"]
    integrity = queries.graph_integrity(connection)
    assert integrity.inferred_without_basis == () and integrity.is_clean
    rows = connection.execute(
        "SELECT i.matched_text, s.original_text, s.char_start, s.char_end, g.text, x.status "
        "FROM relationship_inference i "
        "JOIN source_occurrence s ON s.id = i.basis_occurrence_id "
        "JOIN extraction_run x ON x.id = s.extraction_run_id "
        "JOIN document d ON d.id = x.document_id "
        "JOIN document_segment g ON g.id = s.segment_id"
    ).fetchall()
    assert len(rows) == classified["report"].basis_rows_added
    names = {normalize_alias(r[0]) for r in connection.execute("SELECT canonical_name FROM concept")}
    for matched, original, start, end, page_text, status in rows:
        assert collapse(page_text[start:end]) == collapse(original)
        assert status == "PARTIAL"
        assert matched in original  # I6-B: the source's own text
        assert normalize_alias(matched) in names


def test_only_related_to_is_inferred_once_per_unordered_pair(classified):
    connection = classified["connection"]
    edges = connection.execute(
        "SELECT relation_type, from_concept_id, to_concept_id FROM relationship "
        "WHERE origin = 'INFERRED'").fetchall()
    assert {e[0] for e in edges} <= {"RELATED_TO"}
    assert all(e[1] < e[2] for e in edges)
    assert len({frozenset(e[1:]) for e in edges}) == len(edges)


def test_classifying_the_real_run_again_adds_nothing(classified):
    connection, repository = classified["connection"], classified["repository"]
    digest = _rows(connection)
    again = Classifier(repository).classify(classified["report"].run)
    assert (again.edges_created, again.basis_rows_added) == (0, 0)
    assert _rows(connection) == digest


def test_report_the_real_yield(classified, capsys):
    """Printed, not asserted (decision P6-10)."""
    report = classified["report"]
    connection = classified["connection"]
    touched = {c for p in report.pairs for c in p.concept_ids}
    with capsys.disabled():
        print(
            f"\n  R1 on the real document: {len(report.pairs)} pairs touching {len(touched)} "
            f"of {report.concepts} concepts; {report.edges_created} edges, "
            f"{report.basis_rows_added} bases; {report.mentions} mentions, "
            f"{report.own_name_matches} own-name, {report.ambiguous_mentions} ambiguous, "
            f"{report.unmappable_mentions} unprovable; "
            f"{classified['seconds'] * 1000:.0f} ms"
        )
    assert report.concepts == len(queries.concepts_of_run(connection, report.run.id))
