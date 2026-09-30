"""Keyword mode (ADR 0037 P9-25, P9-29, P9-30; ADR 0035 P9-3).

1. The term goes through D-30 and becomes one quoted FTS5 string - a phrase - with
   `*` after it only when the request asks for a prefix. No user text is ever read as
   FTS5 syntax, and the expression reaches SQLite as a bound parameter.
2. The derived index is opened only after it is verified FRESH against
   `knowledge.db` (`open_fresh_index`); a missing, stale, incompatible or incomplete
   index is refused with its reason, never built or repaired here.
3. The index yields identifiers only. Every hit is **re-read from `knowledge.db`**
   and put through the same scope, filters and lifecycle rules as every other mode
   (P9-5, P9-23): evidence from a source that is not authorised is never returned,
   and a hit whose only matching text is out of scope is withheld and counted.
4. A hit is labelled a keyword hit: stored text that matches. It is **never a link
   to a concept** (D1): no concept is resolved, widened or merged, and a concept-name
   hit is reported as the concept whose stored alias matched, nothing more.
5. Order (P9-22): hits by their first evidence row in scope (document, page, character
   offset), then numeric counter; pages by document, page, ordinal and counter. No
   FTS5 score is read or shown.

Tokenizer limits (`docs/phases/PHASE_9.md` section 13.3) hold here: diacritics are
kept (`cafe` is not `café`), there is no stemming (`MOSFET` finds `MOSFETs` only as a
prefix), `VGS` is not `V_GS`, `uF` is not `μF`, and symbols such as `°`, `±` and `=` are
not searchable.
"""

from app.models.entities import ConceptAlias, Document, DocumentSegment, SourceOccurrence
from app.models.enums import LifecycleStatus
from app.query.index import open_fresh_index
from app.query.pages import document_provenance, document_sources_in_scope
from app.query.provenance import cited_rows, provenance_of, run_notes
from app.query.results import (
    AnswerStatus,
    ConceptNameHit,
    IndexStatus,
    KeywordHit,
    KeywordResult,
    PageHit,
)
from app.query.scope import QueryContext, Verdict, counter, evidence_key, source_verdict
from app.storage import keyword_index as store

KEYWORD_NOTE = (
    "Keyword mode matches stored text through the derived index; every hit is re-read "
    "from knowledge.db and scope-checked. A keyword hit is not a link to any concept "
    "(D1, ADR 0035 P9-3): no concept is resolved, widened or merged, and no relevance "
    "score is used (ADR 0036 P9-22)."
)
TOKENIZER_NOTE = (
    "Tokenizer limits (PHASE_9.md section 13.3): diacritics are kept, so 'cafe' does not "
    "match 'café'; there is no stemming, so a plural matches only through a prefix; "
    "'VGS' does not match 'V_GS' and 'uF' does not match 'μF'; symbols such as °, ± "
    "and = are not searchable."
)


def index_note(status: IndexStatus) -> str:
    """The trace's account of the index a keyword query used."""
    return f"used read-only: {status.path} - {status.state.value}: {status.reason}"


def keyword_mode(context: QueryContext, index_path) -> dict:
    """Answer a keyword request, or refuse when the index is not fresh."""
    request = context.request
    normalized = request.normalized_term
    expression = store.match_expression(normalized, prefix=request.prefix)
    with store.read_snapshot(context.connection):
        index, status = open_fresh_index(context.connection, index_path)
        try:
            entries = store.search_entries(index, expression)
            segments = store.search_pages(index, expression)
        finally:
            index.close()
        knowledge, candidates = _knowledge_hits(context, entries)
        concepts, alias_keys = _concept_hits(context, entries)
        pages = _page_hits(context, segments)
    result = KeywordResult(
        term=request.term,
        normalized_term=normalized,
        prefix=request.prefix,
        expression=expression,
        index=status,
        knowledge=knowledge,
        concepts=concepts,
        pages=pages,
        provenance=provenance_of(context, cited_rows((knowledge, concepts))),
    )
    what = f"stored text matching {request.term!r} (searched as {expression})"
    if not entries and not segments:
        answer = (AnswerStatus.NOT_FOUND, f"No {what}; absence is the answer and nothing was guessed.")
    elif not (knowledge or concepts or pages):
        answer = context.withheld_answer(candidates + alias_keys + segments, f"There is {what}")
    else:
        answer = (
            AnswerStatus.FOUND,
            f"{len(knowledge)} knowledge object(s), {len(concepts)} concept name(s) and "
            f"{len(pages)} page(s) with {what}.",
        )
    return {
        "status": answer[0],
        "message": answer[1],
        "keyword": result,
        "notes": (KEYWORD_NOTE, TOKENIZER_NOTE) + run_notes(result.provenance),
    }


def _knowledge_hits(context: QueryContext, entries) -> tuple[tuple[KeywordHit, ...], tuple[str, ...]]:
    statement = {item for kind, item in entries if kind == store.KNOWLEDGE_OBJECT}
    occurrences: dict[str, list[str]] = {}
    for kind, item in entries:
        if kind == store.SOURCE_OCCURRENCE:
            occurrence = context.get(SourceOccurrence, item)
            if occurrence is not None:
                occurrences.setdefault(occurrence.knowledge_id, []).append(item)
    candidates = tuple(sorted(statement | set(occurrences), key=counter))
    hits = []
    for knowledge_id in candidates:
        knowledge = context.knowledge(knowledge_id)
        if knowledge is None:  # pragma: no cover - a fresh index names stored rows only
            continue
        if not context.knowledge_allowed(knowledge):
            context.filter_out(knowledge_id)
            continue
        stored = context.evidence(knowledge_id)
        evidence = context.judge_item(knowledge_id, stored)
        rows = {row.id: row for row in stored}
        matched = [rows[o] for o in occurrences.get(knowledge_id, ()) if o in rows]
        in_scope = tuple(sorted(
            (row.id for row in matched if context.verdict(row) is Verdict.IN_SCOPE), key=counter
        ))
        if not ((knowledge_id in statement and evidence) or in_scope):
            if evidence:  # the object is in scope, but no text that matched is
                filtered = any(context.verdict(row) is Verdict.FILTERED for row in matched)
                context.withhold(knowledge_id, "filtered" if filtered else "not authorized")
            continue
        context.listed(knowledge_id)
        hits.append(KeywordHit(
            knowledge=knowledge,
            statement_matched=knowledge_id in statement,
            matched_occurrence_ids=in_scope,
            evidence=evidence,
        ))
    hits.sort(key=lambda h: (min(evidence_key(r) for r in h.evidence), counter(h.knowledge.id)))
    return tuple(hits), candidates


def _concept_hits(context: QueryContext, entries) -> tuple[tuple[ConceptNameHit, ...], tuple[str, ...]]:
    by_concept: dict[str, list[ConceptAlias]] = {}
    keys = []
    for kind, item in entries:
        if kind != store.CONCEPT_ALIAS:
            continue
        alias = context.get(ConceptAlias, item)
        if alias is None:  # pragma: no cover - a fresh index names stored rows only
            continue
        keys.extend((alias.id, alias.concept_id))
        if alias.lifecycle_status is not LifecycleStatus.ACTIVE:
            context.filter_out(alias.id)
            continue
        if alias.source_id is not None and source_verdict(
            context.source(alias.source_id), context.request.scope
        ) is not Verdict.IN_SCOPE:
            context.withhold(alias.id, "not authorized")
            continue
        by_concept.setdefault(alias.concept_id, []).append(alias)
    hits = []
    for concept_id in sorted(by_concept, key=counter):
        concept = context.concept(concept_id)
        if concept is None or not context.concept_allowed(concept):
            context.filter_out(concept_id)
            continue
        occurrences = context.judge_item(concept_id, context.evidence(concept_id))
        if not occurrences:
            continue
        context.listed(concept_id)
        hits.append(ConceptNameHit(
            concept=concept,
            aliases=tuple(sorted(by_concept[concept_id], key=lambda a: counter(a.id))),
            occurrences=occurrences,
        ))
    hits.sort(key=lambda h: (min(evidence_key(r) for r in h.occurrences), counter(h.concept.id)))
    return tuple(hits), tuple(keys)


def _page_hits(context: QueryContext, segment_ids) -> tuple[PageHit, ...]:
    f = context.filters
    hits = []
    for segment_id in segment_ids:
        segment = context.get(DocumentSegment, segment_id)
        document = None if segment is None else context.get(Document, segment.document_id)
        if segment is None or document is None:  # pragma: no cover - a fresh index names stored rows
            continue
        if (
            (f.document_ids and document.id not in f.document_ids)
            or (f.pages and segment.page_number not in f.pages)
            or f.run_ids or f.run_statuses or f.extractor_versions  # a page has no run
        ):
            context.filter_out(segment_id)
            continue
        if not document_sources_in_scope(context, document):
            context.withhold(segment_id, "not authorized")
            continue
        context.listed(segment_id)
        hits.append(PageHit(segment=segment, document=document_provenance(context, document)))
    hits.sort(key=lambda h: (
        counter(h.segment.document_id), h.segment.page_number, h.segment.ordinal, counter(h.segment.id)
    ))
    return tuple(hits)
