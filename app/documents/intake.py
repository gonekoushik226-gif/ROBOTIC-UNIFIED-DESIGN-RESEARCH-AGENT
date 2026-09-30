"""Stages 1-3 of the ingestion pipeline: validate, hash, preserve.

Part 2 section 31 requires each stage to have a defined input and output, and says
plainly: *"Do not make the entire ingestion process one giant function."* These are
three separate functions with three separate results, and none of them touches the
database.

None of this needs a PDF parser. That is why it exists as its own module: the first
three of Part 5 section 189's seven checkpoints can be built, tested and trusted
before any parsing library is involved.

Part 2 section 30's seven rules govern this file, and the first and sixth are the
ones everything else is arranged around:

    1. Preserve the original.
    6. Never modify the original file during extraction.

RUDRA therefore **copies** and never moves, and never opens the original for
writing. `preserve()` verifies the copy by hash before reporting success, because
"the original is intact" is the single `must` in Part 5 section 189 and a claim
that important should be checked rather than assumed.
"""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import FailureCategory, InvalidInputError, RudraError

#: Every PDF begins with this. Part 5 section 188 calls this step "PDF detection";
#: the extension is a hint, the magic bytes are the evidence.
PDF_MAGIC: bytes = b"%PDF-"

#: Read in 1 MiB blocks. Part 4 section 157: do not load an enormous document
#: entirely into RAM when it is unnecessary. Hashing never needs to.
_BLOCK_SIZE: int = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    """The output of stage 1. A file RUDRA is willing to read."""

    path: Path
    size: int
    #: Taken from the magic bytes, not from the extension.
    mime_type: str = "application/pdf"
    #: The extension RUDRA's preserved copy is stored with.
    suffix: str = ".pdf"
    #: Recorded as the document's source_type.
    source_type: str = "PDF"


@dataclass(frozen=True, slots=True)
class PreservedFile:
    """The output of stage 3. The original, plus RUDRA's own copy of it."""

    original_path: Path
    stored_path: Path
    file_hash: str
    size: int
    #: Proof, not assumption: the copy was re-hashed and matched.
    verified: bool
    #: True when *this call* wrote the copy; False when an identical copy was
    #: already present and was reused. The caller needs the difference: if a later
    #: stage fails it must remove only what it created, never a copy that was
    #: already there.
    created: bool = True


def validate(path: Path, *, max_bytes: int | None = None, any_format: bool = False) -> ValidatedFile:
    """Stage 1 - is this a file RUDRA can and should read?

    Checks existence, that it is a regular file, that it is non-empty, that it is
    readable, and that its magic bytes say PDF. The extension is never trusted on
    its own: a `.pdf` that is really a ZIP is not a PDF, and a PDF named `.bin` is.
    """
    resolved = _guarded(path)

    if not resolved.exists():
        raise _invalid(
            "That file does not exist.",
            f"No file at {resolved}.",
            resolved,
            ("Check the path.", "Provide the document again."),
        )
    if not resolved.is_file():
        raise _invalid(
            "That path is not a file.",
            f"{resolved} is a directory or another non-file object.",
            resolved,
            ("Provide the path to a PDF file.",),
        )

    size = resolved.stat().st_size
    if size == 0:
        raise _invalid(
            "That file is empty.",
            f"{resolved} is 0 bytes, so there is nothing to ingest.",
            resolved,
            ("Check the file transferred correctly.",),
        )
    if max_bytes is not None and size > max_bytes:
        raise RudraError.of(
            "That file is larger than the configured limit.",
            f"{resolved} is {size} bytes; the limit is {max_bytes} bytes.",
            category=FailureCategory.RESOURCE_LIMIT,
            stage="documents.validate",
            data_changed=False,
            retry_safe=False,
            detail=str(resolved),
            next_options=(
                "Raise the limit in configuration if the document is genuinely needed.",
                "Split the document.",
            ),
        )

    if any_format:
        return _validate_format(resolved, size)

    try:
        with resolved.open("rb") as handle:
            head = handle.read(len(PDF_MAGIC))
    except OSError as exc:
        raise _invalid(
            "That file could not be read.",
            f"{type(exc).__name__}: {exc}",
            resolved,
            ("Check file permissions.", "Check the file is not locked by another program."),
        ) from exc

    if head != PDF_MAGIC:
        raise _invalid(
            "That file is not a PDF.",
            f"It begins with {head!r}, not {PDF_MAGIC!r}. The extension was not "
            "trusted; the file's own bytes were checked.",
            resolved,
            ("Provide a PDF file.", "Phase 4 handles PDFs only."),
        )

    return ValidatedFile(path=resolved, size=size)


def hash_file(path: Path) -> str:
    """Stage 2 - SHA-256 of the file's bytes, streamed.

    Part 2 section 30 rule 2 requires a cryptographic hash. It is read in blocks so
    that a 400 MB textbook costs 1 MiB of memory rather than 400 (Part 4 section
    157), and it is the basis of section 80's duplicate detection.
    """
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while True:
                block = handle.read(_BLOCK_SIZE)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise _invalid(
            "That file could not be read while hashing it.",
            f"{type(exc).__name__}: {exc}",
            Path(path),
            ("Check file permissions.",),
        ) from exc
    return digest.hexdigest()


def preserve(validated: ValidatedFile, file_hash: str, documents_dir: Path) -> PreservedFile:
    """Stage 3 - copy the original into RUDRA's keeping, unmodified.

    Part 2 section 30: *"Preserve the original"* and *"Never modify the original
    file during extraction."* So this **copies**; it never moves, never renames the
    source, and never opens it for writing.

    The stored name is the content hash plus `.pdf`. Two consequences, both wanted:
    two uploads of identical bytes cannot produce two stored copies, and a file
    whose name contains path separators or anything else hostile cannot influence
    where it lands.

    The copy is re-hashed and compared before this reports success. Part 5 section
    189's only `must` is that the original remains intact; a preservation step that
    merely *assumes* it copied correctly would leave that unverified.
    """
    documents_dir = Path(documents_dir)
    documents_dir.mkdir(parents=True, exist_ok=True)
    target = documents_dir / f"{file_hash}{validated.suffix}"

    original_hash_before = file_hash
    created = not target.exists()

    if created:
        try:
            # copy2 preserves timestamps; the SOURCE is only ever read.
            shutil.copy2(validated.path, target)
        except OSError as exc:
            raise RudraError.of(
                "RUDRA could not preserve a copy of that document.",
                f"{type(exc).__name__}: {exc}",
                category=FailureCategory.RESOURCE_LIMIT,
                stage="documents.preserve",
                data_changed=False,
                retry_safe=True,
                cause=repr(exc),
                detail=str(target),
                next_options=(
                    f"Check free space and permissions for {documents_dir}.",
                    "The original file was not modified.",
                ),
            ) from exc

    stored_hash = hash_file(target)
    if stored_hash != file_hash:
        raise RudraError.of(
            "The preserved copy does not match the original.",
            f"Original hash {file_hash}, stored copy {stored_hash}. The copy was "
            "not accepted.",
            category=FailureCategory.VERIFICATION_FAILURE,
            stage="documents.preserve.verify",
            data_changed=True,
            retry_safe=True,
            detail=str(target),
            next_options=(
                "Check the storage device for errors.",
                "The original file was not modified.",
            ),
        )

    # Part 2 section 30 rule 6, verified rather than asserted: the source file's
    # hash is unchanged by anything above.
    if hash_file(validated.path) != original_hash_before:
        raise RudraError.of(
            "The original document changed during preservation.",
            "Part 2 section 30 requires the original never to be modified. Its hash "
            "differs from the one taken moments earlier.",
            category=FailureCategory.VERIFICATION_FAILURE,
            stage="documents.preserve.verify_original",
            data_changed=True,
            retry_safe=False,
            detail=str(validated.path),
            next_options=(
                "Ensure no other program is writing to the file.",
                "Re-run the ingestion once the file is stable.",
            ),
        )

    return PreservedFile(
        original_path=validated.path,
        stored_path=target,
        file_hash=file_hash,
        size=validated.size,
        verified=True,
        created=created,
    )


def discard_preserved(preserved: PreservedFile) -> bool:
    """Remove a preserved copy this run created, after a later stage failed.

    Part 2 section 30 protects the **original**; it says nothing about RUDRA's own
    working copy, and a copy with no document record is not preservation - it is a
    leak. Removing it restores the filesystem to its prior state, which is what
    lets the failure report say `data_changed=False` truthfully.

    Refuses to touch a copy this run did not create (`created` is False): that file
    belongs to some earlier ingestion and is not ours to delete.

    Returns True when the filesystem is back to its prior state. A False return is
    not raised on, because it arrives while another failure is already being
    reported - the caller uses it to tell the user that something *was* left behind.
    """
    if not preserved.created:
        return True
    try:
        preserved.stored_path.unlink(missing_ok=True)
    except OSError:
        return False
    return not preserved.stored_path.exists()


# ---------------------------------------------------------------------- internals


def _validate_format(resolved: Path, size: int) -> ValidatedFile:
    """Any supported format, identified by its bytes (`app/documents/formats.py`)."""
    from app.documents.formats import UnsupportedFormat, detect_format

    try:
        fmt = detect_format(resolved)
    except UnsupportedFormat as exc:
        raise _invalid(
            "RUDRA cannot import that kind of file.",
            str(exc),
            resolved,
            ("Import a supported document instead.", "See docs/FORMATS.md for the supported formats."),
        ) from exc
    return ValidatedFile(path=resolved, size=size, mime_type=fmt.mime_type, suffix=fmt.suffix,
                         source_type=fmt.source_type)


def _guarded(path: Path) -> Path:
    """Resolve a user-supplied path, refusing shapes RUDRA will not follow."""
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise _invalid(
            "That path could not be resolved.",
            f"{type(exc).__name__}: {exc}",
            Path(path),
            ("Provide an absolute path to the document.",),
        ) from exc
    return resolved


def _invalid(summary: str, reason: str, path: Path, options: tuple[str, ...]) -> RudraError:
    return InvalidInputError.of(
        summary,
        reason,
        stage="documents.validate",
        data_changed=False,
        retry_safe=True,
        detail=str(path),
        next_options=options,
    )
