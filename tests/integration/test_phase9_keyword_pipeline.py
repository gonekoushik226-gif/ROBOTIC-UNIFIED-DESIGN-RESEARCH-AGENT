"""The derived index and keyword search over real pipeline output (steps 9-10; scratch only).

Generated one-page PDFs go through the real Phase 4 ingestion, Phase 5 extraction and
stage 15 (the Phase 8 `world` fixture). The index is built explicitly from
`knowledge.db` opened read-only; two builds from byte-identical copies of the database
agree in their metadata, entries and pages, and give the same keyword answers; no
build or query changes a byte of `knowledge.db`. This is not the section 199
acceptance.
"""

from __future__ import annotations

import hashlib
import shutil
from contextlib import closing

import pytest

from app.core.errors import StorageError
from app.models import KnowledgeType
from app.query import AnswerStatus, IndexState, QueryEngine, QueryRequest, as_plain, build_index
from app.storage import connect
from app.storage import keyword_index as store
from tests.integration.test_phase8_acceptance import (  # noqa: F401 - `world` is a fixture
    CAPACITOR,
    COMPANION_LINES,
    OWNED_PROPERTY,
    page,
    world,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _without_paths(value):
    """A result's plain form with its file paths removed, to compare two scratch roots."""
    if isinstance(value, dict):
        return {k: _without_paths(v) for k, v in value.items() if k not in ("path", "index")}
    if isinstance(value, list):
        return [_without_paths(v) for v in value]
    return value


def test_two_builds_from_byte_identical_databases_agree_and_nothing_is_written(world, tmp_path):
    world["both"](page("Book K1", CAPACITOR, OWNED_PROPERTY, *COMPANION_LINES), "K1")
    world["both"](page("Book K2", CAPACITOR, OWNED_PROPERTY), "K2")
    world["connection"].execute("PRAGMA wal_checkpoint(TRUNCATE)")
    original = world["db_path"]
    copy = tmp_path / "copy" / "knowledge.db"
    copy.parent.mkdir()
    shutil.copyfile(original, copy)
    assert _sha256(copy) == _sha256(original)

    answers, contents = [], []
    for db in (original, copy):
        index = db.parent.parent / "indexes" / "index.db"
        index.parent.mkdir(exist_ok=True)
        before = _sha256(db)
        with closing(connect(db, read_only=True)) as reader:
            report = build_index(reader, index)
            assert report.status.state is IndexState.FRESH
            engine = QueryEngine(reader, database_path=db, index_path=index)
            answers.append([
                as_plain(engine.run(QueryRequest.keyword(term, prefix=prefix)))
                for term, prefix in (("capacitor", False), ("charge", False), ("resist", True))
            ])
        with closing(connect(index, read_only=True)) as built:
            contents.append((store.read_metadata(built), store.entry_rows(built), store.page_rows(built)))
        assert _sha256(db) == before  # the build and every query read only

    assert contents[0] == contents[1]
    assert _without_paths(answers[0]) == _without_paths(answers[1])

    capacitor, charge, resist = answers[0]
    assert capacitor["status"] == AnswerStatus.FOUND.value
    statements = {hit["knowledge"]["statement"] for hit in capacitor["keyword"]["knowledge"]}
    assert CAPACITOR in statements and OWNED_PROPERTY in statements
    assert [c["concept"]["canonical_name"] for c in capacitor["keyword"]["concepts"]] == ["Capacitor"] * 2
    assert len(capacitor["keyword"]["pages"]) == 2  # one page in each book
    assert any(OWNED_PROPERTY == h["knowledge"]["statement"] for h in charge["keyword"]["knowledge"])
    # A variable legend and a rule from the companion lines are reachable as keyword hits
    # (D1), labelled as such; "resist" is a prefix of "resistance".
    kinds = {h["knowledge"]["knowledge_type"] for h in resist["keyword"]["knowledge"]}
    assert KnowledgeType.VARIABLE.value in kinds
    assert all(h["label"].startswith("KEYWORD HIT") for h in resist["keyword"]["knowledge"])


def test_an_extraction_after_the_build_leaves_keyword_search_refused_until_rebuilt(world):
    world["both"](page("Book K1", CAPACITOR), "K1")
    index = world["db_path"].parent.parent / "indexes" / "index.db"
    index.parent.mkdir(exist_ok=True)
    with closing(connect(world["db_path"], read_only=True)) as reader:
        build_index(reader, index)
    world["both"](page("Book K2", OWNED_PROPERTY), "K2")  # a later extraction writes knowledge.db

    with closing(connect(world["db_path"], read_only=True)) as reader:
        engine = QueryEngine(reader, database_path=world["db_path"], index_path=index)
        with pytest.raises(StorageError) as caught:
            engine.run(QueryRequest.keyword("capacitor"))
        assert caught.value.report.detail.startswith("STALE")
        assert engine.run(QueryRequest.concept("Capacitor")).status is AnswerStatus.FOUND
        build_index(reader, index)
        again = engine.run(QueryRequest.keyword("capacitor"))
    assert again.status is AnswerStatus.FOUND
    assert OWNED_PROPERTY in {h.knowledge.statement for h in again.keyword.knowledge}
