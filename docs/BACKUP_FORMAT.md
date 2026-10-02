# RUDRA knowledge-base backup format

A RUDRA backup is a single file with the extension `.rudrabackup`. It is a standard ZIP
archive, written and read by `app/storage/archive.py`.

## Contents

| Member | What it is |
|---|---|
| `manifest.json` | The description of the backup (below) |
| `database/knowledge.db` | A consistent snapshot of the knowledge database, taken with SQLite's online backup API. A single file: no `-wal` or `-shm` companion. |
| `documents/<sha256>.<ext>` | RUDRA's preserved copy of each imported document, named by its SHA-256, with the extension of its format (`.pdf`, `.docx`, `.pptx`, `.xlsx`, `.epub`, `.html`, `.htm`, `.md`, `.txt`, `.csv`, `.rtf`, `.png`, `.jpg`, `.tiff`) |
| `extracted/<sha256>/page-N.ocr.json` | The lines and word boxes OCR recognized on page (or image) N of that document |
| `extracted/<sha256>/page-N.math.json` | The display equations rebuilt from PDF page N's layout: the rebuilt form, the page's original text, certainty, evidence and reasons |

AI settings and API keys are never part of a backup: the settings belong to the machine and
the keys to the Windows Credential Manager.

Nothing else is ever written into a backup, and a restore refuses any other member name.

Inside the snapshot, each document's location is stored relative to the backup
(`documents/<name>`). On restore it is pointed at the document store of the installation
that restores it.

## Manifest

```json
{
  "format": "rudra-knowledge-backup",
  "format_version": 1,
  "app_version": "1.1.0",
  "schema_version": 6,
  "created_at": "2026-09-27T12:00:00Z",
  "components": ["database", "documents"],
  "files": [
    {"path": "database/knowledge.db", "size": 577536, "sha256": "…"},
    {"path": "documents/7e9e…56ea.pdf", "size": 843, "sha256": "…"}
  ],
  "counts": {"document": 1, "knowledge_object": 4, "source_occurrence": 4, "…": 0}
}
```

| Field | Meaning |
|---|---|
| `format` | Always `rudra-knowledge-backup` |
| `format_version` | Version of this file format. A reader refuses a newer format. |
| `app_version` | The RUDRA version that wrote the backup |
| `schema_version` | The knowledge database's schema version. A reader refuses a newer schema and migrates an older one with RUDRA's ordinary migrations. |
| `components` | What the backup holds: `database`, `documents`, and `ocr` / `math` when layout records are included |
| `files` | Every member except the manifest, with its size and SHA-256 |
| `counts` | The number of rows in every table of the snapshot |

The four version numbers RUDRA keeps are independent: the application version
(`app/version.py`), the database schema version (the migrations in
`app/storage/schema/`), the configuration file version (`config_schema_version`) and this
backup format version.

## Restore checks

Before the existing knowledge base is touched, a restore checks, in order:

1. the file is a readable ZIP archive with a manifest in this format;
2. the format version and schema version are not newer than this RUDRA understands;
3. every member name is one RUDRA writes (no absolute paths, no `..`, no folders, no
   other file types or names), each is listed once, and the manifest and archive list the
   same files;
4. every file's size and SHA-256 match the manifest (checked again while extracting);
5. the extracted database passes SQLite's integrity check, has the stated schema version
   and holds exactly the row counts the manifest lists;
6. after any migration, the database's foreign keys are consistent, and every document
   file matches the SHA-256 recorded for it in the database.

Extraction goes to a staging folder inside RUDRA's data folder. Only when every check has
passed is the current knowledge database, its document store and the search index moved
to `data/backups/pre-restore-<time>/`, and the restored ones moved into place. If moving
fails, the previous knowledge base is moved back. Finally the restored database is opened
where it now lives and checked again.
