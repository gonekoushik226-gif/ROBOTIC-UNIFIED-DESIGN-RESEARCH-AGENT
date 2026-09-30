"""Directory layout tests.

The storage-class separation these tests protect is what makes the Part 7 rule
possible: deleting a source file must never delete knowledge, and deleting a
rebuildable artefact must never destroy anything authoritative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.schema import defaults
from app.core.errors import StorageError
from app.core.paths import PathLayout, StorageClass, free_disk_mb


def _layout(root: Path) -> PathLayout:
    return PathLayout.from_config(root, defaults())


def test_ensure_creates_every_managed_directory(project_root: Path):
    layout = _layout(project_root)
    report = layout.ensure()
    assert report.created
    assert not report.existing
    for spec in layout.managed_directories():
        assert spec.path.is_dir(), spec.key


def test_ensure_is_idempotent_and_reports_honestly(project_root: Path):
    layout = _layout(project_root)
    first = layout.ensure()
    second = layout.ensure()
    assert not second.created
    assert len(second.existing) == len(first.created)


def test_ensure_never_deletes_existing_content(project_root: Path):
    """RUDRA must never destroy user data while starting up."""
    layout = _layout(project_root)
    layout.ensure()
    marker = layout.documents_dir / "user-file.txt"
    marker.write_text("important", encoding="utf-8")

    layout.ensure()

    assert marker.read_text(encoding="utf-8") == "important"


def test_source_documents_are_not_classified_as_a_cache(project_root: Path):
    """Part 4 section 154: data/documents holds authoritative artefacts."""
    layout = _layout(project_root)
    by_key = {spec.key: spec for spec in layout.managed_directories()}
    assert by_key["documents"].storage_class is StorageClass.SOURCE_DATA
    assert by_key["database"].storage_class is StorageClass.PERSISTENT_KNOWLEDGE
    assert by_key["indexes"].storage_class is StorageClass.REBUILDABLE
    assert by_key["cache"].storage_class is StorageClass.TEMPORARY


def test_every_managed_directory_has_a_class_and_a_purpose(project_root: Path):
    for spec in _layout(project_root).managed_directories():
        assert isinstance(spec.storage_class, StorageClass)
        assert spec.purpose.strip()
        assert spec.first_used.strip()


def test_directory_keys_and_paths_are_unique(project_root: Path):
    specs = _layout(project_root).managed_directories()
    assert len({s.key for s in specs}) == len(specs)
    assert len({s.path for s in specs}) == len(specs)


def test_relative_data_root_is_resolved_against_the_project(project_root: Path):
    layout = _layout(project_root)
    assert layout.data_root == (project_root / "data").resolve()
    assert layout.documents_dir.parent == layout.data_root


def test_absolute_data_root_is_respected(project_root: Path, tmp_path: Path):
    from dataclasses import replace

    elsewhere = tmp_path / "other-volume"
    config = defaults()
    config = replace(config, paths=replace(config.paths, data_root=str(elsewhere)))
    layout = PathLayout.from_config(project_root, config)
    assert layout.data_root == elsewhere.resolve()
    assert layout.logs_dir == (project_root / "logs").resolve()


def test_unwritable_location_produces_an_actionable_failure(project_root: Path):
    """A blocked directory must name itself, not surface a bare OSError."""
    from dataclasses import replace

    blocker = project_root / "blocked"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")

    config = defaults()
    config = replace(
        config, paths=replace(config.paths, data_root=str(blocker / "data"))
    )
    layout = PathLayout.from_config(project_root, config)

    with pytest.raises(StorageError) as caught:
        layout.ensure()
    report = caught.value.report
    assert report.stage == "startup.directories"
    assert report.missing
    assert report.retry_safe is True
    assert report.next_options


def test_free_disk_is_a_number_or_honestly_unknown(project_root: Path):
    value = free_disk_mb(project_root)
    assert value is None or (isinstance(value, int) and value >= 0)


def test_free_disk_works_for_a_path_that_does_not_exist_yet(project_root: Path):
    """The data directory may not exist when space is first checked."""
    assert free_disk_mb(project_root / "not" / "created" / "yet") is not None


def test_layout_is_immutable(project_root: Path):
    layout = _layout(project_root)
    with pytest.raises(AttributeError):
        layout.data_root = project_root  # type: ignore[misc]
