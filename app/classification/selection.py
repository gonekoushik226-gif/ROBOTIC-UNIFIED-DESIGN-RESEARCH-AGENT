"""Which extraction run to classify: exactly one, explicitly named (ADR 0029).

Accepted inputs, and nothing else:

* a run identifier - `classify --run <RUN-id>`;
* a document identifier - `classify <DOC-id>` - **only** when that document has
  exactly one extraction run. With one run there is nothing to choose, so this is
  not a policy.

Refused, with an actionable report and nothing written (Part 4 section 136, Part 6
section 38): no selection at all; both forms at once; an unknown identifier; a
document with more than one run when no run is named (Part 3 section 97: "do not
guess"); a document with no run; and a run that is `RUNNING` or `FAILED`.

**There is no current-run policy** (ADR 0029). Nothing here picks the latest, the
newest completed or a preferred run, and nothing records one. The document form
refuses whenever the document has more than one run, even if all but one failed -
kept that simple deliberately, so no hidden selection logic can grow inside it.
A `PARTIAL` run is accepted; disclosing that it is partial is the classifier's and
the interface's job (decision P6-3b).

Everything here only reads.
"""

from app.core.errors import InvalidInputError
from app.models.entities import Document, ExtractionRun
from app.models.enums import ExtractionRunStatus
from app.models.identifiers import EntityKind, is_valid_id
from app.storage import queries
from app.storage.repository import Repository

#: Run statuses that carry no committed knowledge (ADR 0029).
REFUSED_STATUSES: frozenset[ExtractionRunStatus] = frozenset(
    {ExtractionRunStatus.RUNNING, ExtractionRunStatus.FAILED}
)

_STAGE = "classification.select_run"

_NAME_A_RUN = "Name one run: python -m app classify --run <RUN-id>"
_NAME_A_DOCUMENT = (
    "Or name a document that has exactly one run: python -m app classify <DOC-id>"
)


def select_run(
    repository: Repository, *, run_id: str | None, document_id: str | None
) -> ExtractionRun:
    """The one run this invocation classifies, or a refusal that changes nothing."""
    if run_id and document_id:
        raise _refusal(
            "Both a run and a document were given; nothing was classified.",
            "Phase 6 classifies exactly one explicitly named extraction run. Two "
            "selections are not combined or reconciled (ADR 0029).",
            available=(f"--run {run_id}", f"document {document_id}"),
            next_options=(_NAME_A_RUN, _NAME_A_DOCUMENT),
        )
    if not run_id and not document_id:
        raise _refusal(
            "No extraction run was named; nothing was classified.",
            "Phase 6 never chooses a run by itself - there is no current-run policy "
            "(ADR 0029).",
            missing=("an extraction run identifier",),
            next_options=(_NAME_A_RUN, _NAME_A_DOCUMENT),
        )
    run = _named_run(repository, run_id) if run_id else _only_run(repository, document_id)
    if run.status in REFUSED_STATUSES:
        raise _status_refusal(run)
    return run


def _named_run(repository: Repository, run_id: str) -> ExtractionRun:
    run = (
        repository.get(ExtractionRun, run_id)
        if is_valid_id(run_id, kind=EntityKind.EXTRACTION_RUN)
        else None
    )
    if run is None:
        raise _refusal(
            "There is no extraction run with that identifier; nothing was classified.",
            f"No extraction run has id {run_id!r}.",
            missing=(run_id,),
            next_options=(
                "Check the identifier; run identifiers look like RUN-00000001.",
                "Run 'python -m app db' to see what the database holds.",
            ),
        )
    return run


def _only_run(repository: Repository, document_id: str) -> ExtractionRun:
    document = (
        repository.get(Document, document_id)
        if is_valid_id(document_id, kind=EntityKind.DOCUMENT)
        else None
    )
    if document is None:
        raise _refusal(
            "There is no document with that identifier; nothing was classified.",
            f"No document has id {document_id!r}.",
            missing=(document_id,),
            next_options=(
                "Check the identifier; document identifiers look like DOC-00000001.",
                _NAME_A_RUN,
            ),
        )
    runs = queries.runs_for_document(repository.connection, document.id)
    if not runs:
        raise _refusal(
            "That document has no extraction run; nothing was classified.",
            f"Document {document.id} was ingested but never extracted, so there is no "
            "stored knowledge to organise.",
            missing=("an extraction run of this document",),
            next_options=(
                "Extract it first: python -m app extract <path-to-its-pdf>",
                f"Or: python -m app extract --re-extract {document.id}",
            ),
        )
    if len(runs) > 1:
        raise _refusal(
            "That document has more than one extraction run and none was named; "
            "nothing was classified.",
            f"Document {document.id} has {len(runs)} runs. Choosing one would change "
            "the result, so Phase 6 does not guess (Part 3 section 97, ADR 0029).",
            available=tuple(
                f"{run.id}: run {run.run_number}, trigger {run.trigger}, "
                f"extractor v{run.extractor_version}, status {run.status}"
                for run in runs
            ),
            next_options=(
                "Name one of the runs above: python -m app classify --run <RUN-id>",
            ),
        )
    return runs[0]


def _status_refusal(run: ExtractionRun) -> InvalidInputError:
    if run.status is ExtractionRunStatus.RUNNING:
        why = (
            f"Run {run.id} is RUNNING: it was recorded before extraction and either is "
            "still in progress or was interrupted, so it has no committed knowledge."
        )
    else:
        why = (
            f"Run {run.id} is FAILED: its knowledge was rolled back before it was "
            "marked failed, so there is nothing to organise."
        )
    return _refusal(
        f"Extraction run {run.id} cannot be classified; nothing was classified.",
        why,
        available=(f"run status: {run.status}",),
        next_options=(
            f"Extract the document again: python -m app extract --re-extract {run.document_id}",
            "Then classify the new run by name: python -m app classify --run <RUN-id>",
        ),
    )


def _refusal(summary: str, reason: str, **fields: object) -> InvalidInputError:
    return InvalidInputError.of(  # type: ignore[return-value]
        summary,
        reason,
        stage=_STAGE,
        data_changed=False,
        retry_safe=True,
        **fields,
    )
