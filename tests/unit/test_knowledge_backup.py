"""Knowledge-base backup files: export, validation, and restore into clean and existing installations.

Every project is a temporary folder; documents are generated and imported through the
command line's own `extract`. The live database is never opened.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from app.core.config import load_config
from app.core.paths import PathLayout
from app.storage import archive
from app.storage.archive import BackupError, export_knowledge, inspect_backup, restore_knowledge
from app.storage.migrator import CODE_SCHEMA_VERSION
from app.ui.gui import commands
from tests.unit.pdf_fixtures import make_pdf

RESISTANCE = (
    "Resistance\n"
    "Resistance is defined as the opposition offered by a material to the flow of current.\n"
    "Current is defined as the rate of flow of charge.\n"
    "Resistance depends on current.\n"
)
POWER = "Power\nPower is defined as the rate of doing work.\n"


def layout(root: Path) -> PathLayout:
    return PathLayout.from_config(root, load_config(project_root=root).config)


def extract(project: Path, pdf: Path) -> None:
    result = commands.run(["extract", str(pdf)], project)
    assert result.ok, result.stdout + result.stderr


def provenance(project: Path, identifier: str = "K-00000001") -> dict:
    result = commands.run(["provenance", identifier, "--json"], project)
    assert result.ok, result.stdout + result.stderr
    return json.loads(result.stdout)


def rows(project: Path, sql: str) -> list:
    database = project / "data" / "database" / "knowledge.db"
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        return connection.execute(sql).fetchall()


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    """A project with two imported documents, and one exported backup of it."""
    base = tmp_path_factory.mktemp("backup-source")
    project = base / "machine-a"
    for name, page in (("circuits", RESISTANCE), ("power", POWER)):
        pdf = base / f"{name}.pdf"
        pdf.write_bytes(make_pdf([page]))
        extract(project, pdf)
    return project


@pytest.fixture(scope="module")
def backup(source, tmp_path_factory) -> Path:
    usb = tmp_path_factory.mktemp("usb-drive")
    report = export_knowledge(layout(source), usb / "my-knowledge", app_version="0.1.0")
    assert report.path == usb / "my-knowledge.rudrabackup"
    return report.path


def rebuild(original: Path, target: Path, *, members: dict[str, bytes | None] | None = None,
            manifest=None, extra: dict[str, bytes] | None = None) -> Path:
    """A copy of a backup with some members replaced, removed (None) or added, and an optional new manifest."""
    with zipfile.ZipFile(original) as source:
        contents = {info.filename: source.read(info) for info in source.infolist()}
    for name, data in (members or {}).items():
        if data is None:
            contents.pop(name, None)
        else:
            contents[name] = data
    if manifest is not None:
        document = json.loads(contents[archive.MANIFEST])
        manifest(document)
        contents[archive.MANIFEST] = json.dumps(document).encode("utf-8")
    contents.update(extra or {})
    with zipfile.ZipFile(target, "w") as out:
        for name, data in contents.items():
            out.writestr(name, data)
    return target


# ---------------------------------------------------------------- export


def test_the_backup_holds_the_database_the_documents_and_a_manifest(backup, source):
    with zipfile.ZipFile(backup) as file:
        names = sorted(file.namelist())
        manifest = json.loads(file.read("manifest.json"))
    documents = [name for name in names if name.startswith("documents/")]
    assert names == sorted(["manifest.json", "database/knowledge.db", *documents]) and len(documents) == 2
    assert manifest["format"] == "rudra-knowledge-backup" and manifest["format_version"] == archive.FORMAT_VERSION
    assert manifest["app_version"] == "0.1.0" and manifest["schema_version"] == CODE_SCHEMA_VERSION
    assert manifest["components"] == ["database", "documents"]
    assert {entry["path"] for entry in manifest["files"]} == set(names) - {"manifest.json"}
    assert manifest["counts"]["document"] == 2 and manifest["counts"]["knowledge_object"] >= 3
    for entry in manifest["files"]:
        assert len(entry["sha256"]) == 64 and entry["size"] > 0


def test_the_backup_holds_nothing_else(backup):
    with zipfile.ZipFile(backup) as file:
        names = file.namelist()
        snapshot = file.read("database/knowledge.db")
    for unwanted in ("index.db", ".log", "-wal", "-shm", "config", "cache", ".exe", ".py"):
        assert not any(unwanted in name for name in names), unwanted
    # The snapshot names no folder of the machine it came from.
    assert b"machine-a" not in snapshot


def test_export_does_not_change_the_knowledge_base(source, tmp_path):
    database = source / "data" / "database" / "knowledge.db"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    export_knowledge(layout(source), tmp_path / "again.rudrabackup", app_version="0.1.0")
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert rows(source, "SELECT count(*) FROM document WHERE file_path LIKE 'documents/%'") == [(0,)]


def test_export_refuses_an_empty_installation_and_an_existing_file(tmp_path, backup, source):
    with pytest.raises(BackupError, match="no knowledge base to export"):
        export_knowledge(layout(tmp_path / "empty"), tmp_path / "x.rudrabackup", app_version="0.1.0")
    with pytest.raises(BackupError, match="already exists"):
        export_knowledge(layout(source), backup, app_version="0.1.0")


# ---------------------------------------------------------------- restore


def test_restore_into_a_clean_installation(backup, source, tmp_path):
    target = tmp_path / "machine-b"
    report = restore_knowledge(layout(target), backup)
    assert not report.replaced_existing and report.previous_saved_to is None and report.migrated_from is None
    assert report.verified["integrity"] == "ok" and report.verified["schema_version"] == CODE_SCHEMA_VERSION
    assert report.verified["files_verified"] == 2 and report.documents == 2
    # Knowledge, provenance and source associations are all as they were.
    for table in ("knowledge_object", "concept", "relationship", "source_occurrence", "document", "source"):
        assert rows(target, f"SELECT * FROM {table} ORDER BY id") != [] or table == "relationship"
    for table in ("knowledge_object", "concept", "source_occurrence", "source"):
        assert rows(target, f"SELECT * FROM {table} ORDER BY id") == rows(source, f"SELECT * FROM {table} ORDER BY id")
    # Documents now point at this installation's own document store.
    for (stored,) in rows(target, "SELECT file_path FROM document"):
        assert Path(stored).parent == (target / "data" / "documents").resolve()
        assert Path(stored).is_file()
    restored = provenance(target)
    original = provenance(source)
    assert restored["status"] == original["status"] == "AVAILABLE"
    assert restored["verification"] == "VERIFIED"  # quote and preserved-file hash checked again, here
    assert restored["citations"] == original["citations"] or len(restored["citations"]) == len(original["citations"])
    answer = commands.run(["ask", "What is resistance?", "--json", "--dry-run"], target)
    assert json.loads(answer.stdout)["parts"][0]["status"] == "ANSWERED"


def test_restore_never_silently_replaces_an_existing_knowledge_base(backup, tmp_path):
    target = tmp_path / "machine-c"
    pdf = tmp_path / "other.pdf"
    pdf.write_bytes(make_pdf(["Voltage\nVoltage is defined as the potential difference.\n"]))
    extract(target, pdf)
    before = rows(target, "SELECT id, canonical_name FROM knowledge_object ORDER BY id")
    with pytest.raises(BackupError, match="already has a knowledge base"):
        restore_knowledge(layout(target), backup)
    assert rows(target, "SELECT id, canonical_name FROM knowledge_object ORDER BY id") == before

    report = restore_knowledge(layout(target), backup, replace_existing=True)
    assert report.replaced_existing and report.previous_saved_to is not None
    saved = report.previous_saved_to
    assert (saved / "database" / "knowledge.db").is_file() and any((saved / "documents").iterdir())
    with closing(sqlite3.connect(saved / "database" / "knowledge.db")) as old:
        assert old.execute("SELECT id, canonical_name FROM knowledge_object ORDER BY id").fetchall() == before
    assert provenance(target)["verification"] == "VERIFIED"
    assert not (target / "data" / "indexes" / "index.db").exists()  # the stale index went with the old base


def test_a_failed_restore_leaves_the_existing_knowledge_base_untouched(backup, tmp_path):
    target = tmp_path / "machine-d"
    pdf = tmp_path / "other.pdf"
    pdf.write_bytes(make_pdf(["Voltage\nVoltage is defined as the potential difference.\n"]))
    extract(target, pdf)
    database = target / "data" / "database" / "knowledge.db"
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    broken = rebuild(backup, tmp_path / "broken.rudrabackup", members={"database/knowledge.db": b"not a database"})
    with pytest.raises(BackupError):
        restore_knowledge(layout(target), broken, replace_existing=True)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest
    assert not any(path.name.startswith(".restore-") for path in (target / "data").iterdir())


# ---------------------------------------------------------------- refusals


def test_a_corrupted_file_is_refused(tmp_path):
    bad = tmp_path / "corrupt.rudrabackup"
    bad.write_bytes(b"PK\x03\x04 this is not really a zip file")
    with pytest.raises(BackupError, match="not a readable RUDRA backup"):
        inspect_backup(bad)
    with pytest.raises(BackupError, match="does not exist"):
        inspect_backup(tmp_path / "absent.rudrabackup")


def test_a_file_that_is_not_a_rudra_backup_is_refused(tmp_path):
    other = tmp_path / "photos.zip"
    with zipfile.ZipFile(other, "w") as file:
        file.writestr("holiday.jpg", b"...")
    with pytest.raises(BackupError, match="not a RUDRA backup"):
        inspect_backup(other)


def test_a_checksum_mismatch_is_refused(backup, tmp_path):
    with zipfile.ZipFile(backup) as file:
        name = next(n for n in file.namelist() if n.startswith("documents/"))
        data = bytearray(file.read(name))
    data[-2] ^= 0xFF  # same size, different content
    tampered = rebuild(backup, tmp_path / "tampered.rudrabackup", members={name: bytes(data)})
    with pytest.raises(BackupError, match="checksum does not match"):
        inspect_backup(tampered)
    target = tmp_path / "clean"
    with pytest.raises(BackupError):
        restore_knowledge(layout(target), tampered)
    assert not (target / "data" / "database" / "knowledge.db").exists()


def test_a_backup_from_a_newer_rudra_is_refused_with_the_reason(backup, tmp_path):
    newer_schema = rebuild(backup, tmp_path / "newer-schema.rudrabackup",
                           manifest=lambda m: m.update(schema_version=CODE_SCHEMA_VERSION + 1, app_version="9.0.0"))
    with pytest.raises(BackupError, match="newer version of RUDRA") as refused:
        inspect_backup(newer_schema)
    assert "9.0.0" in str(refused.value)
    newer_format = rebuild(backup, tmp_path / "newer-format.rudrabackup",
                           manifest=lambda m: m.update(format_version=archive.FORMAT_VERSION + 1))
    with pytest.raises(BackupError, match="newer version of RUDRA"):
        inspect_backup(newer_format)


def test_missing_files_are_refused(backup, tmp_path):
    with zipfile.ZipFile(backup) as file:
        document = next(n for n in file.namelist() if n.startswith("documents/"))
    missing_document = rebuild(backup, tmp_path / "missing.rudrabackup", members={document: None})
    with pytest.raises(BackupError, match="incomplete"):
        inspect_backup(missing_document)
    no_database = rebuild(backup, tmp_path / "no-db.rudrabackup", members={"database/knowledge.db": None},
                          manifest=lambda m: m.update(files=[f for f in m["files"]
                                                             if f["path"] != "database/knowledge.db"]))
    with pytest.raises(BackupError, match="no knowledge database"):
        inspect_backup(no_database)
    no_manifest = rebuild(backup, tmp_path / "no-manifest.rudrabackup", members={"manifest.json": None})
    with pytest.raises(BackupError, match="no manifest"):
        inspect_backup(no_manifest)


def test_an_unlisted_file_is_refused(backup, tmp_path):
    extra = rebuild(backup, tmp_path / "extra.rudrabackup",
                    extra={"documents/" + "a" * 64 + ".pdf": b"%PDF-1.4 unlisted"})
    with pytest.raises(BackupError, match="does not list"):
        inspect_backup(extra)


@pytest.mark.parametrize("name", [
    "../outside.pdf", "documents/../../escape.pdf", "/absolute.pdf", "C:/Windows/evil.pdf",
    "documents\\..\\evil.pdf", "documents/run.exe", "documents/" + "a" * 64 + ".exe", "database/other.db",
    "documents/sub/" + "a" * 64 + ".pdf", "startup.bat", "documents/" + "a" * 64 + ".pdf.lnk",
])
def test_names_rudra_never_writes_are_refused(backup, tmp_path, name):
    payload = b"MZ\x90\x00 not to be written anywhere"
    entry = {"path": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    hostile = rebuild(backup, tmp_path / "hostile.rudrabackup", extra={name: payload},
                      manifest=lambda m: m["files"].append(entry))
    with pytest.raises(BackupError, match="never write"):
        inspect_backup(hostile)
    target = tmp_path / "victim"
    with pytest.raises(BackupError):
        restore_knowledge(layout(target), hostile)
    assert not (tmp_path / "outside.pdf").exists() and not (tmp_path / "escape.pdf").exists()
    assert not (target / "data" / "database" / "knowledge.db").exists()


def test_a_manifest_that_disagrees_with_the_database_is_refused(backup, tmp_path):
    inflated = rebuild(backup, tmp_path / "inflated.rudrabackup",
                       manifest=lambda m: m["counts"].update(knowledge_object=m["counts"]["knowledge_object"] + 5))
    with pytest.raises(BackupError, match="inconsistent"):
        restore_knowledge(layout(tmp_path / "target"), inflated)


def test_a_damaged_database_is_refused(backup, tmp_path):
    with zipfile.ZipFile(backup) as file:
        database = bytearray(file.read("database/knowledge.db"))
    database[len(database) // 2: len(database) // 2 + 4096] = b"\x00" * 4096
    manifest_fix = {"sha256": hashlib.sha256(bytes(database)).hexdigest(), "size": len(database)}
    damaged = rebuild(backup, tmp_path / "damaged.rudrabackup", members={"database/knowledge.db": bytes(database)},
                      manifest=lambda m: next(f for f in m["files"] if f["path"] == "database/knowledge.db"
                                              ).update(manifest_fix))
    target = tmp_path / "target"
    with pytest.raises(BackupError):
        restore_knowledge(layout(target), damaged)
    assert not (target / "data" / "database" / "knowledge.db").exists()


def test_a_backup_from_an_older_schema_is_migrated_by_the_ordinary_migrator(tmp_path, monkeypatch):
    from app.storage import migrator
    from app.storage.connection import connect

    older = CODE_SCHEMA_VERSION - 1
    database = tmp_path / "old.db"
    with monkeypatch.context() as patch:
        patch.setattr(migrator, "available_migrations",
                      lambda original=migrator.available_migrations: original()[:older])
        patch.setattr(migrator, "CODE_SCHEMA_VERSION", older)  # an older RUDRA made this database
        connection = connect(database)
        try:
            migrator.migrate(connection, database_path=database)
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode = DELETE")
            assert migrator.schema_version(connection) == older
            counts = archive._table_counts(connection)
        finally:
            connection.close()
    data = database.read_bytes()
    manifest = {"format": archive.FORMAT, "format_version": 1, "app_version": "0.0.9", "schema_version": older,
                "created_at": "2026-01-01T00:00:00Z", "components": ["database", "documents"], "counts": counts,
                "files": [{"path": "database/knowledge.db", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}
    old_backup = tmp_path / "old.rudrabackup"
    with zipfile.ZipFile(old_backup, "w") as file:
        file.writestr("manifest.json", json.dumps(manifest))
        file.writestr("database/knowledge.db", data)
    target = tmp_path / "upgraded"
    report = restore_knowledge(layout(target), old_backup)
    assert report.migrated_from == older and report.verified["schema_version"] == CODE_SCHEMA_VERSION
    assert report.verified["integrity"] == "ok"


def test_the_window_offers_export_and_import(tmp_path, source, capsys):
    import tkinter as tk
    import time

    from app.ui.gui.window import RudraWindow

    try:
        with capsys.disabled():
            root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - a machine without a display
        pytest.skip(f"Tk cannot open a window here: {exc}")
    root.withdraw()
    try:
        window = RudraWindow(root, source, full=True, autostart=False)
        page = window.full.pages["backup"]

        def wait():
            deadline = time.monotonic() + 120
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
            root.update()

        page.ask_save = lambda initial: str(tmp_path / initial)
        assert page.export()
        wait()
        (made,) = tmp_path.glob("RUDRA-knowledge-*.rudrabackup")
        assert "Your knowledge is backed up." in window.full.output.text_content()

        other = RudraWindow(root, tmp_path / "second", full=True, autostart=False)
        importer = other.full.pages["backup"]
        importer.ask_open = lambda: str(made)
        questions = []
        importer.ask_yes = lambda title, message: questions.append(message) or True
        window = other
        assert importer.restore()
        wait()
        wait()
        assert questions and "Restore this backup?" in questions[0]
        assert "Your knowledge is restored and checked." in other.full.output.text_content()

        # An existing knowledge base: the question says it will be replaced; declining changes nothing.
        questions.clear()
        importer.ask_yes = lambda title, message: questions.append(message) or False
        assert importer.restore()
        wait()
        assert "REPLACES" in questions[0] and "Nothing was changed" in other.full.output.text_content()
    finally:
        root.destroy()


# ---------------------------------------------------------------- new document types and layout records


def test_word_documents_and_pdf_equation_layouts_travel_with_the_backup(tmp_path):
    from tests.unit.format_fixtures import make_docx, w_paragraph
    from tests.unit.layout_pdf_fixtures import Bar, Text, prose, write_pdf

    project = tmp_path / "machine-a"
    pdf = write_pdf(tmp_path / "bandwidth.pdf", [[prose(720, "The bandwidth of a series circuit is"),
                                                  Text(250, 680, "BW", 12, "I"), Text(272, 680, "=", 12, "S"),
                                                  Text(290, 688, "R", 12, "I"), Bar(287, 301, 684),
                                                  Text(290, 670, "L", 12, "I")]])
    extract(project, pdf)
    extract(project, make_docx(tmp_path / "notes.docx", [w_paragraph("Inductance is defined as the ratio of flux "
                                                                     "linkage to current.")]))
    report = export_knowledge(layout(project), tmp_path / "usb" / "kb", app_version="0.1.0")
    with zipfile.ZipFile(report.path) as file:
        names = file.namelist()
        manifest = json.loads(file.read("manifest.json"))
    records = [name for name in names if name.endswith(".math.json")]
    assert len(records) == 1 and "math" in manifest["components"]
    assert any(name.endswith(".docx") for name in names)
    assert not any("ai.json" in name or "credential" in name.lower() for name in names)

    target = tmp_path / "machine-b"
    restore_knowledge(layout(target), report.path)
    restored = target / "data" / records[0]
    assert restored.read_bytes() == (project / "data" / records[0]).read_bytes()
    statement = r"BW = \frac{R}{L}"
    assert (statement, "REPORTED_BY_SOURCE") in rows(
        target, "SELECT statement, certainty FROM knowledge_object WHERE knowledge_type = 'EQUATION'")
    (document_id,) = rows(target, "SELECT id FROM document WHERE source_type = 'PDF'")[0]
    again = commands.run(["extract", "--re-extract", document_id], target)
    assert again.ok, again.stdout + again.stderr
    answer = commands.run(["ask", "What is inductance?", "--json", "--dry-run"], target)
    assert "flux linkage" in json.loads(answer.stdout)["parts"][0]["answer"]
