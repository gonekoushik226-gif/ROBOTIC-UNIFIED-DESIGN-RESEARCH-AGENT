"""Phase 7 lookup: the read-only exact-lookup command (ADR 0031, P7-1, I7-A...I7-G).

Every fixture is a temporary project built through the unchanged Phase 4 ingestion
and Phase 5 extraction (`python -m app extract`), so the lookup reads what RUDRA
really stores. Nothing here touches the live database.

Exit-code contract (I7-A, I7-D): 0 one or more matches, 2 invalid input, 3 no
match, 5 storage/schema/open failure, 70 unexpected error.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from unittest import mock

import pytest

from app.knowledge import ConceptService
from app.models import RelationshipInference, RelationshipOrigin, RelationType
from app.models.base import utc_now
from app.storage import Repository, connect, migrate, migrator
from app.ui.cli.main import main
from tests.conftest import PROJECT_ROOT
from tests.integration.test_phase6_acceptance import SECTION_193_PAGES
from tests.unit.pdf_fixtures import make_pdf


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _db(project: pathlib.Path) -> pathlib.Path:
    return project / "data" / "database" / "knowledge.db"


@pytest.fixture
def project(tmp_path: pathlib.Path, capsys) -> pathlib.Path:
    """A fresh project holding one extracted synthetic document."""
    root = tmp_path / "project"
    pdf = tmp_path / "adders.pdf"
    pdf.write_bytes(make_pdf(SECTION_193_PAGES))
    assert main(["extract", str(pdf), "--project-root", str(root)]) == 0
    capsys.readouterr()
    return root


def _lookup(project: pathlib.Path, capsys, *args: str) -> tuple[int, str, str]:
    code = main(["lookup", *args, "--project-root", str(project)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _lookup_json(project: pathlib.Path, capsys, *args: str) -> tuple[int, dict]:
    code, out, _ = _lookup(project, capsys, *args, "--json")
    return code, json.loads(out)


# ------------------------------------------------------------- exact lookups


def test_lookup_by_exact_name_returns_the_concept_with_its_provenance(project, capsys, tmp_path):
    code, data = _lookup_json(project, capsys, "--name", "Full adder")
    assert code == 0
    assert data["database"]["read_only"] is True
    assert data["match_count"] == 1
    match = data["matches"][0]
    assert match["concept"]["canonical_name"] == "Full adder"
    definition = match["definitions"][0]
    assert definition["knowledge"]["statement"].startswith("A full adder is a circuit")
    assert definition["relationship"]["relation_type"] == "DEFINED_BY"
    assert definition["relationship"]["origin"] == "EXPLICIT"
    (row,) = definition["evidence"]
    assert row["page_number"] == 1
    assert row["segment_id"] and row["char_start"] < row["char_end"]
    assert row["extraction_run_id"] == "RUN-00000001"
    assert [run["id"] for run in match["runs"]] == ["RUN-00000001"]
    (item,) = match["documents"]
    document = item["document"]
    assert item["preserved_file_present"] is True
    original = _sha256(tmp_path / "adders.pdf")
    assert document["file_hash"] == original
    assert _sha256(pathlib.Path(document["file_path"])) == original


def test_the_name_is_matched_after_d30_normalisation(project, capsys):
    _, plain = _lookup_json(project, capsys, "--name", "Full adder")
    code, spaced = _lookup_json(project, capsys, "--name", "  FULL   adder ")
    assert code == 0
    assert spaced["lookup"]["normalized"] == "full adder"
    assert spaced["matches"] == plain["matches"]


def test_lookup_by_id_returns_the_same_match_as_by_name(project, capsys):
    _, by_name = _lookup_json(project, capsys, "--name", "Full adder")
    concept_id = by_name["matches"][0]["concept"]["id"]
    code, by_id = _lookup_json(project, capsys, concept_id)
    assert code == 0
    assert by_id["lookup"] == {"by": "id", "value": concept_id}
    assert by_id["matches"] == by_name["matches"]


def test_text_and_json_carry_the_same_facts(project, capsys):
    _, data = _lookup_json(project, capsys, "--name", "Full adder")
    code, text, _ = _lookup(project, capsys, "--name", "Full adder")
    assert code == 0
    match = data["matches"][0]
    assert match["concept"]["id"] in text
    for item in match["definitions"]:
        assert item["knowledge"]["id"] in text
        assert item["knowledge"]["statement"] in text
        for row in item["evidence"]:
            assert row["id"] in text
    for item in match["relationships"]:
        assert item["relationship"]["id"] in text
        for row in item["evidence"]:
            assert row["id"] in text
    for row in match["occurrences"]:
        assert row["id"] in text
    for run in match["runs"]:
        assert run["id"] in text and run["status"] in text
    for item in match["documents"]:
        assert item["document"]["file_hash"] in text
    assert "preserved file present: yes" in text
    assert "opened read-only" in text


def test_every_concept_answering_to_a_name_is_shown_and_none_is_chosen(project, capsys):
    connection = connect(_db(project))
    ConceptService(Repository(connection)).create_concept(
        "Carry", context="a second, unrelated meaning added by hand"
    )
    connection.commit()
    connection.close()

    code, data = _lookup_json(project, capsys, "--name", "carry")
    assert code == 0
    assert data["match_count"] == 2
    extracted, manual = data["matches"]  # resolve's order: created_at, then id
    assert extracted["concept"]["context"] is None
    assert [run["id"] for run in extracted["runs"]] == ["RUN-00000001"]
    assert manual["concept"]["context"] == "a second, unrelated meaning added by hand"
    assert manual["runs"] == [] and manual["documents"] == []

    code, text, _ = _lookup(project, capsys, "--name", "carry")
    assert code == 0
    assert "2 concepts answer to this name; none is chosen." in text
    assert "--- Match 1 of 2 ---" in text and "--- Match 2 of 2 ---" in text


def test_no_match_is_reported_as_absence_with_exit_code_3(project, capsys):
    code, text, _ = _lookup(project, capsys, "--name", "flip-flop")
    assert code == 3
    assert "Matches    : 0" in text and "no concept answers to this name" in text

    code, data = _lookup_json(project, capsys, "--name", "flip-flop")
    assert code == 3
    assert data["match_count"] == 0 and data["matches"] == []

    code, text, _ = _lookup(project, capsys, "CPT-99999999")
    assert code == 3
    assert "no concept has this id" in text


@pytest.mark.parametrize(
    "args",
    [
        ("CPT-00000001", "--name", "Full adder"),  # both forms
        (),  # neither form
        ("K-00000001",),  # an identifier of another kind
        ("not-an-id",),
        ("--name", "   "),  # nothing but whitespace
        ("--name", ""),
    ],
)
def test_invalid_input_is_refused_with_exit_code_2(project, capsys, args):
    before = _sha256(_db(project))
    code, out, err = _lookup(project, capsys, *args)
    assert code == 2
    assert "INVALID_INPUT" in err
    assert out == ""
    assert _sha256(_db(project)) == before


def test_invalid_input_is_refused_before_any_database_is_opened(tmp_path, capsys):
    empty = tmp_path / "empty-project"
    code, _, err = _lookup(empty, capsys, "--name", "  ")
    assert code == 2 and "INVALID_INPUT" in err
    assert not _db(empty).exists()


# --------------------------------------------------- storage and schema refusals


def test_a_missing_database_is_refused_and_never_created(tmp_path, capsys):
    empty = tmp_path / "empty-project"
    code, out, err = _lookup(empty, capsys, "--name", "Full adder")
    assert code == 5
    assert "DATABASE_FAILURE" in err and "never creates one" in err
    assert out == ""
    assert not _db(empty).exists()


def test_a_file_that_is_not_a_database_is_refused(tmp_path, capsys):
    root = tmp_path / "junk-project"
    _db(root).parent.mkdir(parents=True)
    _db(root).write_bytes(b"not a database at all " * 50)
    before = _sha256(_db(root))
    code, _, err = _lookup(root, capsys, "--name", "Full adder")
    assert code == 5 and "DATABASE_FAILURE" in err
    assert _sha256(_db(root)) == before


def test_an_older_schema_is_refused_and_never_migrated(tmp_path, capsys):
    root = tmp_path / "old-project"
    path = _db(root)
    path.parent.mkdir(parents=True)
    connection = connect(path)
    real = migrator.available_migrations()
    with mock.patch.object(migrator, "available_migrations",
                           lambda: tuple(m for m in real if m.version <= 4)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 4):
        migrate(connection, database_path=path)
    connection.commit()
    connection.close()
    before = _sha256(path)

    code, _, err = _lookup(root, capsys, "--name", "Full adder")
    assert code == 5
    assert "never migrates" in err and "schema version is 4" in err
    assert _sha256(path) == before
    check = sqlite3.connect(path)
    try:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        check.close()
    assert not list((root / "data" / "backups").glob("*.db"))  # no migration backup either


def test_a_newer_schema_is_refused(project, capsys):
    raw = sqlite3.connect(_db(project))
    raw.execute(f"PRAGMA user_version = {migrator.CODE_SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()
    code, _, err = _lookup(project, capsys, "--name", "Full adder")
    assert code == 5
    assert "newer than this build" in err


def test_an_unexpected_error_exits_70(project, capsys, monkeypatch):
    def boom(self, concept_id):
        raise RuntimeError("simulated failure inside retrieval")

    monkeypatch.setattr(ConceptService, "retrieve", boom)
    code, _, err = _lookup(project, capsys, "--name", "Full adder")
    assert code == 70
    assert "UNKNOWN_ERROR" in err


# ------------------------------------------------------------- read-only proof


def test_a_lookup_never_writes_the_database_file(project, capsys):
    path = _db(project)
    before = _sha256(path)
    _, data = _lookup_json(project, capsys, "--name", "Full adder")
    concept_id = data["matches"][0]["concept"]["id"]
    for args in (("--name", "Full adder"), (concept_id,), ("--name", "flip-flop"),
                 (concept_id, "--json"), ("--name", "carry", "--json")):
        _lookup(project, capsys, *args)
    assert _sha256(path) == before
    assert not list((project / "data" / "backups").glob("*.db"))


def test_the_lookup_path_never_calls_migrate(project, capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a lookup must never migrate")

    monkeypatch.setattr(migrator, "migrate", forbidden)
    monkeypatch.setattr("app.storage.migrate", forbidden)
    code, _, _ = _lookup(project, capsys, "--name", "Full adder")
    assert code == 0


# ---------------------------------------------------------------- provenance


def test_an_inferred_edge_is_shown_with_its_recorded_basis(project, capsys):
    """Built directly in the fixture's database - no classification is run."""
    _, full = _lookup_json(project, capsys, "--name", "Full adder")
    _, carry = _lookup_json(project, capsys, "--name", "Carry")
    full_id = full["matches"][0]["concept"]["id"]
    carry_id = carry["matches"][0]["concept"]["id"]
    occurrence_id = full["matches"][0]["definitions"][0]["evidence"][0]["id"]

    connection = connect(_db(project))
    repository = Repository(connection)
    edge = ConceptService(repository).attach_relationship(
        relation_type=RelationType.RELATED_TO,
        origin=RelationshipOrigin.INFERRED,
        from_concept_id=min(full_id, carry_id),
        to_concept_id=max(full_id, carry_id),
    )
    now = utc_now()
    repository.add(
        RelationshipInference(
            id=repository.new_id(RelationshipInference),
            created_at=now,
            updated_at=now,
            relationship_id=edge.id,
            rule="R1",
            rule_version="1",
            basis_occurrence_id=occurrence_id,
            matched_text="carry",
        )
    )
    connection.commit()
    connection.close()

    code, data = _lookup_json(project, capsys, full_id)
    assert code == 0
    (inferred,) = [
        item for item in data["matches"][0]["relationships"]
        if item["relationship"]["origin"] == "INFERRED"
    ]
    assert inferred["relationship"]["relation_type"] == "RELATED_TO"
    assert inferred["other"] == {
        "this_concept_is": "from" if full_id < carry_id else "to",
        "kind": "concept", "id": carry_id, "name": "Carry",
    }
    assert inferred["evidence"] == []  # an inferred edge has no stating occurrence
    (basis,) = inferred["bases"]
    assert basis["inference"]["rule"] == "R1" and basis["inference"]["matched_text"] == "carry"
    assert basis["basis_occurrence"]["id"] == occurrence_id

    code, text, _ = _lookup(project, capsys, full_id)
    assert code == 0
    assert "RELATED_TO INFERRED" in text and 'R1 v1' in text and 'matched "carry"' in text


def test_a_missing_preserved_file_is_reported(project, capsys):
    _, data = _lookup_json(project, capsys, "--name", "Full adder")
    pathlib.Path(data["matches"][0]["documents"][0]["document"]["file_path"]).unlink()
    code, data = _lookup_json(project, capsys, "--name", "Full adder")
    assert code == 0
    assert data["matches"][0]["documents"][0]["preserved_file_present"] is False
    code, text, _ = _lookup(project, capsys, "--name", "Full adder")
    assert "preserved file present: NO" in text


# ----------------------------------------------------------- its own process


def test_the_lookup_runs_as_its_own_process(project):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "app", "lookup", "--name", "Full adder", "--json",
         "--project-root", str(project)],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["matches"][0]["concept"]["canonical_name"] == "Full adder"
