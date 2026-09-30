"""Page retrieval (ADR 0036 P9-21).

A page request names a document and a page. The result is the page's **stored**
`document_segment` text and every knowledge, concept and relationship occurrence
located on it, in text order, each with its stored subject and provenance. The
original PDF is never opened. Stored `document_structure` entries are not included
(the smallest choice `PHASE_9.md` section 10 leaves open).

**Scope** (P9-5): a page's text is shown only when its document has a source that is
authorised and in scope; each occurrence is judged by its own source. The request's
evidence filters apply to the occurrences, its knowledge filters to knowledge-object
occurrences and its relationship filters to relationship occurrences.
"""

from pathlib import Path

from app.models.entities import Document
from app.query.provenance import cited_rows, provenance_of
from app.query.results import AnswerStatus, DocumentProvenance, EvidenceRow, PageOccurrence, PageResult
from app.query.scope import QueryContext, Verdict, counter, source_verdict
from app.storage import queries


def document_sources_in_scope(context: QueryContext, document: Document) -> tuple:
    """The document's sources that may be used under the request's scope and filters."""
    categories = context.filters.source_categories
    return tuple(
        source
        for source in sorted(
            queries.sources_for_document(context.connection, document.id), key=lambda s: counter(s.id)
        )
        if source_verdict(source, context.request.scope) is Verdict.IN_SCOPE
        and (not categories or source.source_category in categories)
    )


def document_provenance(context: QueryContext, document: Document) -> DocumentProvenance:
    """A document with its declared editions and preserved-file presence (P9-18, P9-20)."""
    return DocumentProvenance(
        document=document,
        preserved_file_present=Path(document.file_path).is_file(),
        editions=tuple(sorted(
            queries.document_versions_by_hash(context.connection, document.file_hash),
            key=lambda v: counter(v.id),
        )),
    )


def page_result(
    context: QueryContext, document_id: str, page_number: int
) -> tuple[AnswerStatus, str, PageResult | None]:
    """The stored page, or why it cannot be shown."""
    where = f"page {page_number} of {document_id}"
    document = context.get(Document, document_id)
    if document is None:
        return AnswerStatus.NOT_FOUND, f"No document has id {document_id}; nothing was guessed.", None
    if context.filters.document_ids and document_id not in context.filters.document_ids:
        context.filter_out(document_id)
        return AnswerStatus.NOT_FOUND, f"{document_id} is excluded by the request's document filter.", None
    if not document_sources_in_scope(context, document):
        context.withhold(document_id, "not authorized")
        return (
            AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION,
            f"Insufficient authorized information: {document_id} has no source that is "
            f"authorised and in the requested scope ({context.request.scope.value}), so the "
            f"text of {where} is withheld.",
            None,
        )
    context.listed(document_id)
    segments = queries.segments_on_page(context.connection, document_id, page_number)
    occurrences = []
    for stored in queries.evidence_on_page(context.connection, document_id, page_number):
        row = EvidenceRow.of(stored)
        if context.verdict(row) is not Verdict.IN_SCOPE:
            continue
        subject = _subject(context, row)
        if subject is not None:
            context.listed(row.subject_id)
            occurrences.append(PageOccurrence(evidence=row, subject=subject))
    if not segments and not occurrences:
        return AnswerStatus.NOT_FOUND, f"Nothing is stored for {where}.", None
    page = PageResult(
        document=document_provenance(context, document),
        page_number=page_number,
        segments=segments,
        occurrences=tuple(occurrences),
        provenance=provenance_of(context, cited_rows(tuple(occurrences))),
    )
    message = f"{where}: {len(segments)} stored segment(s), {len(occurrences)} occurrence(s)."
    return AnswerStatus.FOUND, message, page


def _subject(context: QueryContext, row: EvidenceRow):
    """The stored subject of an occurrence, or None when the request's filters exclude it."""
    if row.subject_kind == "KNOWLEDGE_OBJECT":
        subject = context.knowledge(row.subject_id)
        allowed = subject is not None and context.knowledge_allowed(subject)
    elif row.subject_kind == "RELATIONSHIP":
        subject = context.relationship(row.subject_id)
        allowed = subject is not None and context.edge_allowed(subject)
    else:
        subject = context.concept(row.subject_id)
        allowed = subject is not None and context.concept_allowed(subject)
    if not allowed:
        context.filter_out(row.subject_id)
        return None
    return subject
