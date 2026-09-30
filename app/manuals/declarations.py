"""The user's declaration that a document is an application's manual (ADR 0049 P17-2).

The declaration is the user's, never the document's (section 109; ADR 0023): only
`extract … --manual APPLICATION` records one. It is a `memory_item` in `SOURCE_MEMORY`,
key `application-manual:DOC-ID`, value the application's name as the user wrote it. A
recorded declaration is never rewritten.
"""

from dataclasses import dataclass

from app.core.errors import InvalidInputError
from app.models.base import utc_now
from app.models.entities import Document, MemoryItem
from app.models.enums import LifecycleStatus, MemoryCategory
from app.models.naming import normalize_alias
from app.storage import queries
from app.storage.repository import Repository

KEY_PREFIX = "application-manual:"
#: The longest application name accepted.
MAX_NAME = 80


@dataclass(frozen=True, slots=True)
class Declaration:
    document_id: str
    application: str
    recorded: str
    memory_id: str


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="manuals.declarations", data_changed=False, retry_safe=True,
        next_options=('python -m app extract "Application Manual.pdf" --manual "Application name"',
                      "python -m app manual"),
    )


def same_application(a: str, b: str) -> bool:
    """Two names for one application, compared after the D-30 normalisation."""
    return normalize_alias(a) == normalize_alias(b)


def declarations(repository: Repository) -> tuple[Declaration, ...]:
    """Every active declaration, oldest first."""
    rows = queries.memory_items(repository.connection, MemoryCategory.SOURCE_MEMORY.value, KEY_PREFIX)
    return tuple(
        Declaration(row.key.removeprefix(KEY_PREFIX), row.value, row.created_at, row.id)
        for row in rows if row.lifecycle_status is LifecycleStatus.ACTIVE
    )


def declaration_of(repository: Repository, document_id: str) -> Declaration | None:
    return next((d for d in declarations(repository) if d.document_id == document_id), None)


def check_name(application: str) -> str:
    """The application's name, whitespace collapsed; refused when empty, too long or not
    on one line - checked before anything is extracted or written."""
    name = " ".join(application.split())
    if not name:
        raise refuse("The application's name is empty.", "Name the application the manual documents.")
    if len(name) > MAX_NAME or "\n" in application or "\r" in application:
        raise refuse("The application's name is too long.", f"At most {MAX_NAME} characters on one line.")
    return name


def check_declaration(repository: Repository, document_id: str, application: str) -> None:
    """Refuse, before any extraction, a declaration `declare` would refuse: a bad name, or a
    different application for a document already declared."""
    name = check_name(application)
    existing = declaration_of(repository, document_id)
    if existing is not None and not same_application(existing.application, name):
        raise refuse(f"{document_id} is already declared the manual of {existing.application!r}.",
                     "A recorded declaration is never rewritten (ADR 0049 P17-2).")


def declare(repository: Repository, document_id: str, application: str) -> tuple[Declaration, bool]:
    """Record that `document_id` is the manual of `application`; returns the declaration
    and whether it was new. The caller commits. The same declaration again changes
    nothing; a different application for the same document is refused."""
    name = check_name(application)
    if repository.get(Document, document_id) is None:
        raise refuse(f"No document has the identifier {document_id}.", "Only an ingested document can be a manual.")
    check_declaration(repository, document_id, name)
    existing = declaration_of(repository, document_id)
    if existing is not None:
        return existing, False
    now = utc_now()
    row = repository.add(MemoryItem(
        id=repository.new_id(MemoryItem), created_at=now, updated_at=now,
        category=MemoryCategory.SOURCE_MEMORY, key=KEY_PREFIX + document_id, value=name,
        lifecycle_status=LifecycleStatus.ACTIVE, observed_at=None,
    ))
    return Declaration(document_id, name, row.created_at, row.id), True
