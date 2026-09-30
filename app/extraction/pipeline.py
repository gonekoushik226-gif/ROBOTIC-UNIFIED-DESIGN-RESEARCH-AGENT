"""The knowledge-extraction pipeline, stages 9-15 (Part 5 section 190, ADR 0017).

    9  content classification   classify.classify_pages
    10 knowledge extraction      detectors.detect_all
    11 relationship extraction   detectors.detect_relationships / _prerequisites
    12 validation                validate.validate_page / unknown_variables
    13 normalization             normalize
    14 provenance attachment     this module
    15 deduplication             app.deduplication.Stage15, called inline by the
                                 writer before each knowledge object is created
                                 (Phase 8, decision P8-11, ADR 0033)

Part 2 section 31 applies here as it did to Phase 4: *"Do not make the entire
ingestion process one giant function."* `execute()` coordinates; the work lives in
the stage modules, each separately testable.

Rules this module will not break:

* **Every stored claim carries evidence** - a `source_occurrence` for each
  knowledge object and a `relationship_occurrence` for each edge, with document,
  page, segment, character span and run (Part 2 sections 34 and 49, Part 5
  section 165, ADRs 0018-0019).
* **Every relationship is `EXPLICIT`** (ADR 0017). Phase 5 creates no `INFERRED`
  edge; that is Phase 6's work.
* **`normalized_hash` is never written** (decision D-36, ADR 0020; P8-18).
* **An exact duplicate is linked, never recreated** (stage 15, P8-12): the item
  becomes a new source occurrence of the existing object, its companion row is
  reused, and a `DEFINITION`'s or owned `PROPERTY`'s concept gets its own edge to
  that object - or, if it already has one, a new occurrence of it (D-24).
* **`document.processing_status` may be lowered to `PARTIALLY_PROCESSED` and is
  never set to `PROCESSED` or `FAILED`** (decision D-44 as amended, ADR 0024).
* **Nothing commits here.** The caller owns the transaction, which is what lets a
  run be recorded `RUNNING`, committed, executed, and committed again - so a crash
  mid-extraction leaves an honest `RUNNING` row and no half-written knowledge
  (Part 6 section 19).

**Concept identity within a run (an interpretation, recorded in PHASE_5.md).** A
term the document defines on several pages becomes one concept *within one run*,
matched by decision D-30's normalisation only (NFKC, casefold, whitespace) - the
same rule Phase 3's `resolve()` uses. Concepts are never merged *across* runs or
documents: stage 15 records such pairs `POSSIBLE_EQUIVALENT` (P8-16) and leaves
each run's concepts as they are.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.core.errors import InvalidInputError
from app.deduplication import Item, Stage15
from app.extraction import normalize
from app.extraction.candidates import EquationCandidate, Located, PageCandidates, PropertyCandidate, Span
from app.extraction.classify import classify_pages
from app.extraction.detectors import detect_all, is_numeric_instance
from app.extraction.validate import (
    DISCARDING_ISSUES,
    Finding,
    unknown_variables,
    validate_page,
)
from app.knowledge.concepts import ConceptService, Evidence
from app.models.base import utc_now
from app.models.entities import (
    Concept,
    Document,
    Equation,
    ExtractionIssue,
    ExtractionRun,
    KnowledgeObject,
    Procedure,
    Rule,
    SourceOccurrence,
    Variable,
)
from app.models.enums import (
    CertaintyState,
    DocumentProcessingStatus,
    ExtractionIssueType,
    ExtractionRunStatus,
    ExtractionTrigger,
    KnowledgeType,
    LifecycleStatus,
    ProcedureDocumentationStatus,
    RelationType,
    RelationshipOrigin,
    TextOrigin,
)
from app.models.naming import normalize_alias
from app.storage import queries
from app.storage.repository import Repository

#: The extractor's version, recorded on every run (Part 2 section 48's
#: `extraction_version`). Bump it whenever a detector's behaviour changes, so a
#: re-extraction can be told apart from the run it replaces.
#:
#: Versions 1 and 2 were interim builds, each run once on the acceptance document
#: during Phase 5 development and each superseded by defects that run exposed:
#: v1 mis-named two variables and counted mere mentions of a theorem as rules; v2
#: stored flattened equations with a prose definition's certainty. Both runs are
#: preserved in `data/backups/` rather than discarded. Version 3 was Phase 5's.
#:
#: Version 4 is Phase 8's (decision P8-7, ADR 0032): stage 15 changes what an
#: extraction writes - links instead of new objects, assessment records, conflicts,
#: `HAS_PROPERTY` edges - so a run must say which extractor wrote it. "Fully
#: ingested" needs a `COMPLETED` run at version 4 or later *and* indexing, which is
#: Phase 9's; no run is reported fully ingested before then (P8-6).
EXTRACTOR_VERSION = "6"

#: Readers whose text is the document's own, exactly as written (not a PDF text layer,
#: which loses fraction and integral layout, and not OCR): an equation read from them is
#: the source's statement, stored as REPORTED_BY_SOURCE.
EXACT_TEXT_READERS = frozenset({"docx", "pptx", "xlsx", "epub", "html", "markdown", "txt", "csv", "rtf"})

#: The eleven Part 5 section 190 categories, in the order section 190 lists them.
CATEGORIES: tuple[str, ...] = (
    "concepts", "definitions", "equations", "variables", "units", "properties",
    "rules", "examples", "procedures", "prerequisites", "relationships",
)

#: The only document status Phase 5 may write (ADR 0024, amended).
_PERMITTED_DOCUMENT_STATUS = DocumentProcessingStatus.PARTIALLY_PROCESSED

_RAW_PDF_DATE = re.compile(r"^D:\d{4}")


@dataclass(frozen=True, slots=True)
class ExtractionReport:
    """What one run actually did. Every number is a count of real rows or candidates."""

    run: ExtractionRun
    document_id: str
    pages_examined: int
    #: Pages whose text stage 9 masked entirely as question-bank material.
    question_bank_pages: int
    masked_characters: int
    total_characters: int
    #: Worked-solution values (`VL = 10 V`) seen and deliberately not stored.
    numeric_equations_skipped: int
    found: dict[str, int] = field(default_factory=dict)
    stored: dict[str, int] = field(default_factory=dict)
    issues: dict[str, int] = field(default_factory=dict)
    #: `DEFINED_BY` edges are counted separately from the other relationships so
    #: that "a relationship was extracted" is never satisfied silently by them.
    defined_by_edges: int = 0
    semantic_edges: int = 0
    document_status_before: DocumentProcessingStatus | None = None
    document_status_after: DocumentProcessingStatus | None = None
    #: Stage 15 (Phase 8). Per category, the stored items that were linked to an
    #: existing identical object as a new source occurrence instead of creating one.
    #: `stored` counts every stored item, created or linked.
    linked: dict[str, int] = field(default_factory=dict)
    #: Assessment records written, by outcome.
    assessments: dict[str, int] = field(default_factory=dict)
    #: Conflicts rule C1 created, in order: (conflict, claim A, claim B).
    conflicts: tuple[tuple[str, str, str], ...] = ()
    #: POSSIBLE_EQUIVALENT concept records written.
    concept_equivalences: int = 0
    #: Property sentences recorded as a `HAS_PROPERTY` edge (or a new occurrence of
    #: one) from their owner concept.
    has_property_edges: int = 0

    @property
    def status(self) -> ExtractionRunStatus:
        return self.run.status


class ExtractionPipeline:
    """Stages 9-14 over one ingested document. Holds no state between runs."""

    __slots__ = ("_repository", "_concepts", "_parser_name", "_layout_dir")

    def __init__(self, repository: Repository, *, parser_name: str = "pypdf", layout_dir: Path | None = None) -> None:
        self._repository = repository
        self._concepts = ConceptService(repository)
        self._parser_name = parser_name
        #: The extracted/ folder holding what ingestion recorded about PDF equation layout
        #: (page-N.math.json); without it, every PDF equation is stored as uncertain.
        self._layout_dir = None if layout_dir is None else Path(layout_dir)

    @property
    def _connection(self):
        return self._repository.connection

    # ------------------------------------------------------------ run lifecycle

    def start(
        self, document_id: str, *, trigger: ExtractionTrigger = ExtractionTrigger.FIRST_EXTRACTION
    ) -> ExtractionRun:
        """Record a new `RUNNING` run. The caller should commit before `execute`.

        Committing first is what makes a crash visible afterwards: a run left
        `RUNNING` with no `completed_at` is an honest record of an attempt that did
        not finish (Part 2 section 61), with no half-written knowledge beside it.
        """
        document = self._document(document_id)
        now = utc_now()
        return self._repository.add(
            ExtractionRun(
                id=self._repository.new_id(ExtractionRun),
                created_at=now,
                updated_at=now,
                document_id=document.id,
                run_number=queries.next_run_number(self._connection, document.id),
                trigger=trigger,
                extractor_version=EXTRACTOR_VERSION,
                parser_name=self._parser_name,
                started_at=now,
                status=ExtractionRunStatus.RUNNING,
            )
        )

    def _laid_out(self, document) -> dict[int, list[dict]]:
        """What ingestion recorded about each PDF page's rebuilt equations, by page."""
        if self._layout_dir is None or not document.file_hash:
            return {}
        folder = self._layout_dir / document.file_hash
        if not folder.is_dir():
            return {}
        found: dict[int, list[dict]] = {}
        for path in sorted(folder.glob("page-*.math.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # An unreadable record only loses the layout: those equations stay uncertain.
                continue
            if isinstance(data, dict) and data.get("format") == 1 and isinstance(data.get("page"), int):
                found[data["page"]] = [e for e in data.get("equations", ()) if isinstance(e, dict)]
        return found

    def mark_failed(self, run: ExtractionRun) -> ExtractionRun:
        """Record that a run failed. Its knowledge was rolled back by the caller."""
        return self._repository.update(
            replace(run, status=ExtractionRunStatus.FAILED, completed_at=utc_now())
        )

    def execute(self, run: ExtractionRun) -> ExtractionReport:
        """Run stages 9-14 for a started run. Does not commit."""
        if run.status is not ExtractionRunStatus.RUNNING:
            raise InvalidInputError.of(
                "That extraction run has already finished.",
                f"Run {run.id} is {run.status}, not RUNNING.",
                stage="extraction.pipeline.execute",
                data_changed=False,
                retry_safe=False,
                next_options=("Start a new run with --re-extract.",),
            )
        document = self._document(run.document_id)
        sources = queries.sources_for_document(self._connection, document.id)
        segments = queries.segments_for_document(self._connection, document.id)
        by_page = {segment.page_number: segment for segment in segments}

        # Stage 9 - classification.
        views = classify_pages(tuple((s.page_number, s.text) for s in segments))

        # Stages 10-11 - detection.
        detected = tuple(
            detect_all(
                v.page_number, by_page[v.page_number].id, v.expository, v.instructional
            )
            for v in views
        )
        laid_out = self._laid_out(document)
        if laid_out:
            detected = tuple(
                _with_layout(page, laid_out.get(page.page_number, ()), by_page[page.page_number].text, view.expository)
                for page, view in zip(detected, views)
            )
        known_terms = frozenset(
            normalize_alias(c.name) for page in detected for c in page.concepts
        )

        # Stage 12 - validation.
        survivors: list[PageCandidates] = []
        findings: list[Finding] = []
        numeric = 0
        for page, view in zip(detected, views):
            segment = by_page[page.page_number]
            result = validate_page(
                page if sources else replace(page, segment_id=""),
                known_terms=known_terms,
                needs_ocr=segment.text_origin is TextOrigin.UNKNOWN,
                page_text=view.expository,
            )
            survivors.append(result.survivors)
            findings.extend(result.findings)
            numeric += result.numeric_equations
        findings.extend(unknown_variables(tuple(survivors)))
        findings.extend(self._metadata_findings(document))

        # Stages 13-15 - normalisation, provenance attachment and, before each
        # knowledge object is created, deduplication (P8-11).
        stored: dict[str, int] = {c: 0 for c in CATEGORIES}
        linked: dict[str, int] = {c: 0 for c in CATEGORIES}
        defined_by = semantic = has_property = concept_equivalences = 0
        assessments: dict[str, int] = {}
        conflicts: tuple[tuple[str, str, str], ...] = ()
        if sources:
            writer = _Writer(self, run, document.id, sources[0].id, by_page, known_terms)
            for page in survivors:
                writer.write(page)
            stage15 = writer.finish()
            stored, linked = writer.stored, writer.linked
            defined_by, semantic, has_property = writer.defined_by, writer.semantic, writer.has_property
            assessments = dict(sorted(stage15.assessments.items()))
            conflicts = tuple(stage15.conflicts)
            concept_equivalences = stage15.concept_records
            findings.extend(
                Finding(
                    issue_type=note.issue_type,
                    detail=note.detail,
                    page_number=note.page_number,
                    excerpt=note.excerpt,
                )
                for note in stage15.issues
            )

        for finding in findings:
            self._record(run, document.id, finding, by_page)

        # Final run status (Part 2 section 61).
        partial = any(f.issue_type in DISCARDING_ISSUES for f in findings)
        final = replace(
            run,
            status=ExtractionRunStatus.PARTIAL if partial else ExtractionRunStatus.COMPLETED,
            completed_at=utc_now(),
        )
        final = self._repository.update(final)
        before = document.processing_status
        after = self._lower_document_status(document, partial)

        found = {c: sum(len(getattr(p, c)) for p in detected) for c in CATEGORIES}
        issue_counts: dict[str, int] = {}
        for finding in findings:
            issue_counts[finding.issue_type.value] = issue_counts.get(finding.issue_type.value, 0) + 1
        return ExtractionReport(
            run=final,
            document_id=document.id,
            pages_examined=len(views),
            question_bank_pages=sum(1 for v in views if v.fully_question),
            masked_characters=sum(v.masked_chars for v in views),
            total_characters=sum(len(v.original) for v in views),
            numeric_equations_skipped=numeric,
            found=found,
            stored=stored,
            issues=dict(sorted(issue_counts.items())),
            defined_by_edges=defined_by,
            semantic_edges=semantic,
            document_status_before=before,
            document_status_after=after,
            linked=linked,
            assessments=assessments,
            conflicts=conflicts,
            concept_equivalences=concept_equivalences,
            has_property_edges=has_property,
        )

    # --------------------------------------------------------------- internals

    def _document(self, document_id: str) -> Document:
        document = self._repository.get(Document, document_id)
        if document is None:
            raise InvalidInputError.of(
                "There is no document with that identifier to extract from.",
                f"No document row has id {document_id!r}.",
                stage="extraction.pipeline",
                missing=(document_id,),
                data_changed=False,
                retry_safe=True,
                next_options=("Ingest the PDF first: python -m app extract <path-to-pdf>.",),
            )
        return document

    def _lower_document_status(
        self, document: Document, partial: bool
    ) -> DocumentProcessingStatus:
        """The one write Phase 5 makes to a document row (ADR 0024, amended).

        Only ever *lowers* `PROCESSED` to `PARTIALLY_PROCESSED`, which Part 2
        section 61 names literally. `PROCESSED` and `FAILED` are never written:
        the first would be the "complete" value the ingestion-completeness rule
        forbids before Phase 8, and the second would misreport a document that
        parsed perfectly well. The downgrade is permanent by design.
        """
        if not partial or document.processing_status is not DocumentProcessingStatus.PROCESSED:
            return document.processing_status
        updated = self._repository.update(
            replace(document, processing_status=_PERMITTED_DOCUMENT_STATUS)
        )
        return updated.processing_status

    def _metadata_findings(self, document: Document) -> tuple[Finding, ...]:
        """Part 2 section 60's *malformed metadata* check, document-level.

        Reported, never corrected: section 29 says unknown metadata must remain
        unknown, and rewriting a stored value would be exactly that invention.
        """
        out: list[Finding] = []
        if document.publication_date and _RAW_PDF_DATE.match(document.publication_date):
            out.append(
                Finding(
                    issue_type=ExtractionIssueType.MALFORMED_METADATA,
                    detail=("publication_date is stored in raw PDF date syntax, not as a "
                            "date; it has not been interpreted"),
                    excerpt=document.publication_date,
                )
            )
        if document.publisher and "unknown" in document.publisher.casefold():
            out.append(
                Finding(
                    issue_type=ExtractionIssueType.MALFORMED_METADATA,
                    detail=("publisher holds a placeholder value; Phase 4 records the PDF "
                            "/Producer field here, which names the software that generated "
                            "the file rather than a publisher"),
                    excerpt=document.publisher,
                )
            )
        return tuple(out)

    def _record(self, run: ExtractionRun, document_id: str, finding: Finding, by_page) -> None:
        segment = by_page.get(finding.page_number) if finding.page_number is not None else None
        now = utc_now()
        self._repository.add(
            ExtractionIssue(
                id=self._repository.new_id(ExtractionIssue),
                created_at=now,
                updated_at=now,
                extraction_run_id=run.id,
                document_id=document_id,
                issue_type=finding.issue_type,
                detail=finding.detail,
                page_number=finding.page_number,
                segment_id=segment.id if segment is not None else None,
                excerpt=finding.excerpt,
            )
        )


def _bare(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text) if not c.isspace())


def _with_layout(page: PageCandidates, placements, text: str, expository: str) -> PageCandidates:
    """A page's equation candidates, told apart by what ingestion recorded about their layout.

    A detected equation inside a certain rebuilt span is marked "certain". A tentative
    (uncertain) rebuilt equation becomes a candidate of its own, quoting the page text's
    flat form, and replaces the flattened fragments the detector found inside that span.
    Records that no longer match the stored text are ignored.
    """
    certain: list[tuple[int, int]] = []
    tentative: list[tuple[int, int, str]] = []
    for record in placements:
        span = record.get("span")
        linear = str(record.get("linear") or "")
        if not (isinstance(span, list) and len(span) == 2 and all(isinstance(v, int) for v in span)):
            continue
        start, end = span
        if not 0 <= start < end <= len(text):
            continue
        if record.get("certain") is True and (text[start:end] == linear if record.get("substituted") is True
                                              else _bare(text[start:end]) == _bare(str(record.get("flat") or ""))):
            certain.append((start, end))
        elif record.get("certain") is False and _bare(text[start:end]) == _bare(str(record.get("flat") or "")) \
                and expository[start:end].strip() and any(op in linear for op in ("=", "\\le", "\\ge", "<", ">")):
            tentative.append((start, end, linear))
    if not certain and not tentative:
        return page
    equations: list[EquationCandidate] = []
    for e in page.equations:
        if any(a <= e.span.start and e.span.end <= b for a, b in certain):
            equations.append(replace(e, layout="certain"))
        elif not any(e.span.start < b and a < e.span.end for a, b, _ in tentative):
            equations.append(e)
    for start, end, linear in tentative:
        rhs = linear.split("=", 1)[1] if "=" in linear else ""
        equations.append(EquationCandidate(page_number=page.page_number, span=Span(start, end), text=text[start:end],
                                           expression=linear, numeric=bool(rhs) and is_numeric_instance(rhs),
                                           layout="uncertain"))
    equations.sort(key=lambda e: e.span.start)
    return replace(page, equations=tuple(equations))


class _Writer:
    """Stages 14 and 15 for one run: validated candidates become rows with evidence,
    each compared with existing knowledge before any object is created for it."""

    def __init__(self, pipeline: ExtractionPipeline, run, document_id, source_id, by_page,
                 known_terms: frozenset[str] = frozenset()):
        self._repo = pipeline._repository
        self._service = pipeline._concepts
        self._run = run
        self._document_id = document_id
        self._source_id = source_id
        self._by_page = by_page
        #: The normalised names of the concepts this document defines - the test a
        #: property's owner must pass, as a stated relationship's ends must (ADR 0017
        #: row 11; P8-24).
        self._known_terms = known_terms
        self._concept_ids: dict[str, str] = {}
        #: (concept, page, span start) already cited - one sentence is one piece of
        #: evidence, however many detectors noticed it.
        self._cited: set[tuple[str, int, int]] = set()
        self._stage15 = Stage15(self._repo, run)
        self.stored: dict[str, int] = {c: 0 for c in CATEGORIES}
        self.linked: dict[str, int] = {c: 0 for c in CATEGORIES}
        self.defined_by = 0
        self.semantic = 0
        self.has_property = 0

    # -------------------------------------------------------------- evidence

    def _ocr(self, candidate: Located) -> bool:
        return self._by_page[candidate.page_number].text_origin is TextOrigin.OCR

    def _method(self, candidate: Located) -> str:
        """How the claim was obtained, with what makes it uncertain: +ocr (recognized text),
        +weak (a general sentence shape rather than a defining verb), +layout (an equation
        rebuilt from the PDF page's glyph layout) and +layout? (rebuilt, with a doubt)."""
        layout = getattr(candidate, "layout", None)
        tags = ("+ocr" if self._ocr(candidate) else "") + ("+weak" if getattr(candidate, "weak", False) else "") \
            + {"certain": "+layout", "uncertain": "+layout?"}.get(layout, "")
        return f"deterministic/{type(candidate).__name__}@v{EXTRACTOR_VERSION}{tags}"

    def _certainty(self, candidate: Located, default: CertaintyState) -> CertaintyState:
        """Recognized (OCR) text and weakly recognised claims are never stored as certain."""
        if self._ocr(candidate) or getattr(candidate, "weak", False):
            return CertaintyState.UNCERTAIN
        return default

    def _evidence(self, candidate: Located, text: str | None = None) -> Evidence:
        segment = self._by_page[candidate.page_number]
        return Evidence(
            source_id=self._source_id,
            document_id=self._document_id,
            text=text if text is not None else candidate.text,
            extraction_method=self._method(candidate),
            segment_id=segment.id,
            page_number=candidate.page_number,
            char_start=candidate.span.start,
            char_end=candidate.span.end,
            extraction_run_id=self._run.id,
        )

    def _item(
        self, knowledge_type: KnowledgeType, statement: str, candidate: Located,
        concept_id: str | None = None,
    ) -> Item:
        """What stage 15 compares: the statement as it would be stored, and where."""
        return Item(
            knowledge_type=knowledge_type,
            statement=statement,
            location=(
                self._document_id, candidate.page_number, candidate.span.start, candidate.span.end,
            ),
            concept_id=concept_id,
        )

    def _knowledge(
        self,
        knowledge_type: KnowledgeType,
        prefix: str,
        statement: str,
        candidate: Located,
        certainty: CertaintyState = CertaintyState.REPORTED_BY_SOURCE,
        concept_id: str | None = None,
    ) -> tuple[str, bool]:
        """A knowledge object plus its source occurrence - or, for an exact duplicate
        of a stored object, only a new source occurrence of that object (P8-12).

        Returns the object's identifier and whether it was linked rather than
        created. `normalized_hash` is left None (D-36, P8-18): populating it would
        let the live unique index deduplicate by IntegrityError, which Part 3
        sections 72-73 forbid.
        """
        certainty = self._certainty(candidate, certainty)
        body = normalize.collapse(statement) or normalize.collapse(candidate.text)
        item = self._item(knowledge_type, body, candidate, concept_id)
        decision = self._stage15.decide(item)
        if decision.link_to is not None:
            self._link(item, decision.link_to, candidate)
            return decision.link_to, True
        now = utc_now()
        knowledge = self._repo.add(
            KnowledgeObject(
                id=self._repo.new_id(KnowledgeObject),
                created_at=now,
                updated_at=now,
                knowledge_type=knowledge_type,
                canonical_name=normalize.label(prefix, body),
                statement=body,
                lifecycle_status=LifecycleStatus.ACTIVE,
                certainty=certainty,
                knowledge_version=1,
            )
        )
        self._occurrence(knowledge.id, candidate)
        self._stage15.created(item, knowledge.id, decision, excerpt=candidate.text)
        return knowledge.id, False

    def _link(self, item: Item, knowledge_id: str, candidate: Located) -> None:
        """Section 79's "LINK NEW SOURCE": the evidence joins the existing object."""
        occurrence = self._occurrence(knowledge_id, candidate)
        self._stage15.linked(item, knowledge_id, occurrence.id)

    def _occurrence(self, knowledge_id: str, candidate: Located) -> SourceOccurrence:
        evidence = self._evidence(candidate)
        now = utc_now()
        return self._repo.add(
            SourceOccurrence(
                id=self._repo.new_id(SourceOccurrence),
                created_at=now,
                updated_at=now,
                knowledge_id=knowledge_id,
                source_id=evidence.source_id,
                document_id=evidence.document_id,
                original_text=evidence.text,
                extraction_method=evidence.extraction_method,
                extraction_timestamp=now,
                segment_id=evidence.segment_id,
                page_number=evidence.page_number,
                char_start=evidence.char_start,
                char_end=evidence.char_end,
                extraction_run_id=evidence.extraction_run_id,
            )
        )

    def _concept(self, name: str, candidate: Located) -> str:
        """The run's concept for this term, created with its evidence on first sight."""
        key = normalize_alias(name)
        concept_id = self._concept_ids.get(key)
        if concept_id is None:
            concept: Concept = self._service.create_concept(name)
            concept_id = concept.id
            self._concept_ids[key] = concept_id
            self.stored["concepts"] += 1
        cited = (concept_id, candidate.page_number, candidate.span.start)
        if cited not in self._cited:
            self._cited.add(cited)
            self._service.attach_concept_provenance(
                concept_id, self._evidence(candidate, text=name)
            )
        return concept_id

    def _edge(self, relation: RelationType, from_id: str, to_id: str, candidate: Located) -> None:
        """One EXPLICIT edge; a repeat statement adds evidence, not a second edge (D-24)."""
        existing = queries.active_relationship_between(
            self._repo.connection, relation_type=relation,
            from_concept_id=from_id, to_concept_id=to_id,
        )
        if existing is not None:
            self._service.attach_relationship_provenance(existing.id, self._evidence(candidate))
            return
        self._service.attach_relationship(
            relation_type=relation,
            origin=RelationshipOrigin.EXPLICIT,
            evidence=self._evidence(candidate),
            from_concept_id=from_id,
            to_concept_id=to_id,
        )
        self.semantic += 1

    def _knowledge_edge(
        self, relation: RelationType, concept_id: str, knowledge_id: str, candidate: Located
    ) -> None:
        """An EXPLICIT concept -> knowledge edge (`DEFINED_BY`, `HAS_PROPERTY`) with the
        sentence as its evidence; if the concept already holds it, the sentence is
        added as another occurrence of that edge (D-24, ADR 0033 P8-12)."""
        existing = queries.active_relationship_to_knowledge(
            self._repo.connection, relation_type=relation,
            from_concept_id=concept_id, to_knowledge_id=knowledge_id,
        )
        if existing is not None:
            self._service.attach_relationship_provenance(existing.id, self._evidence(candidate))
        else:
            self._service.attach_relationship(
                relation_type=relation,
                origin=RelationshipOrigin.EXPLICIT,
                evidence=self._evidence(candidate),
                from_concept_id=concept_id,
                to_knowledge_id=knowledge_id,
            )
        self._stage15.attach_concept(knowledge_id, concept_id)

    def _owner(self, candidate: PropertyCandidate) -> str | None:
        """The run's concept a property belongs to, or None (P8-24).

        Only a detector-recorded owner (*"the property of X is ..."*) counts, and
        only when X names a concept this document defines. No concept is created
        for an owner the run does not define, and an unresolved owner raises no
        issue: the property is still stored, so nothing is discarded.
        """
        if candidate.owner is None or normalize_alias(candidate.owner) not in self._known_terms:
            return None
        return self._concept(candidate.owner, candidate)

    # ---------------------------------------------------------------- write

    def write(self, page: PageCandidates) -> None:
        for c in page.concepts:
            self._concept(c.name, c)

        for d in page.definitions:
            concept_id = self._concept(d.concept_name, d)
            statement = normalize.collapse(d.statement)
            item = self._item(KnowledgeType.DEFINITION, statement, d, concept_id)
            decision = self._stage15.decide(item)
            if decision.link_to is not None:
                self._knowledge_edge(RelationType.DEFINED_BY, concept_id, decision.link_to, d)
                self._link(item, decision.link_to, d)
                self.linked["definitions"] += 1
            else:
                knowledge, _edge = self._service.attach_definition(
                    concept_id,
                    statement,
                    label=normalize.label("Definition", d.concept_name),
                    evidence=self._evidence(d),
                    certainty=self._certainty(d, CertaintyState.REPORTED_BY_SOURCE),
                )
                self._occurrence(knowledge.id, d)
                self._stage15.created(item, knowledge.id, decision, excerpt=d.text)
                self._stage15.attach_concept(knowledge.id, concept_id)
            self.stored["definitions"] += 1
            self.defined_by += 1

        for e in page.equations:
            # UNCERTAIN, not REPORTED_BY_SOURCE. The source printed something here,
            # but the text layer is measured to lose fraction and integral structure:
            # in a random sample of 40 equations stored from the acceptance document,
            # about 18 were visibly flattened ("BW = R" where the book prints R/L),
            # and many are indistinguishable by shape from valid ones ("RN = Rth").
            # Reporting them with a prose definition's certainty would present a
            # fragment as the source's claim (Part 1 section 5; Part 6 section 13).
            # No confidence number is invented to go with it (Part 6 section 12).
            # Text from a document's own markup (Word, HTML, EPUB, Markdown, ...) is exact:
            # nothing was laid out and flattened, so the source's statement is stored as such.
            # An equation rebuilt from a PDF page's glyph layout with every glyph's place
            # settled is the source's statement too (app/documents/pdfmath.py).
            segment = self._by_page[e.page_number]
            exact = (segment.text_origin is TextOrigin.NATIVE_TEXT and segment.extraction_method in EXACT_TEXT_READERS) \
                or e.layout == "certain"
            knowledge_id, linked = self._knowledge(
                KnowledgeType.EQUATION, "Equation", e.expression, e,
                certainty=CertaintyState.REPORTED_BY_SOURCE if exact else CertaintyState.UNCERTAIN,
            )
            if not linked:
                now = utc_now()
                self._repo.add(
                    Equation(
                        id=self._repo.new_id(Equation),
                        created_at=now, updated_at=now,
                        expression=e.expression,
                        lifecycle_status=LifecycleStatus.ACTIVE,
                        # Parsing into a canonical form is Phase 11 (section 202).
                        canonical_form=None,
                        knowledge_id=knowledge_id,
                    )
                )
            self._count("equations", linked)

        for v in page.variables:
            knowledge_id, linked = self._knowledge(KnowledgeType.VARIABLE, "Variable", v.text, v)
            if not linked:
                now = utc_now()
                self._repo.add(
                    Variable(
                        id=self._repo.new_id(Variable),
                        created_at=now, updated_at=now,
                        symbol=v.symbol,
                        lifecycle_status=LifecycleStatus.ACTIVE,
                        name=v.name,
                        unit=v.unit,
                        knowledge_id=knowledge_id,
                    )
                )
            self._count("variables", linked)

        for u in page.units:
            _knowledge_id, linked = self._knowledge(KnowledgeType.UNIT, f"Unit {u.unit}", u.text, u)
            self._count("units", linked)

        for p in page.properties:
            owner = self._owner(p)
            knowledge_id, linked = self._knowledge(
                KnowledgeType.PROPERTY, f"Property of {p.subject}", p.statement, p,
                concept_id=owner,
            )
            if owner is not None:
                self._knowledge_edge(RelationType.HAS_PROPERTY, owner, knowledge_id, p)
                self.has_property += 1
            self._count("properties", linked)

        for r in page.rules:
            knowledge_id, linked = self._knowledge(KnowledgeType.RULE, "Rule", r.statement, r)
            if not linked:
                now = utc_now()
                self._repo.add(
                    Rule(
                        id=self._repo.new_id(Rule),
                        created_at=now, updated_at=now,
                        name=normalize.collapse(r.name)[:200] or "Rule",
                        statement=normalize.collapse(r.statement),
                        lifecycle_status=LifecycleStatus.ACTIVE,
                        preconditions=r.preconditions,
                        output=r.output,
                        knowledge_id=knowledge_id,
                    )
                )
            self._count("rules", linked)

        for x in page.examples:
            _knowledge_id, linked = self._knowledge(KnowledgeType.EXAMPLE, x.label, x.statement, x)
            self._count("examples", linked)

        for pr in page.procedures:
            knowledge_id, linked = self._knowledge(
                KnowledgeType.PROCEDURE, "Procedure", " / ".join(pr.steps), pr
            )
            if not linked:
                now = utc_now()
                self._repo.add(
                    Procedure(
                        id=self._repo.new_id(Procedure),
                        created_at=now, updated_at=now,
                        name=pr.name,
                        # Part 3 section 111: from a document, so DOCUMENTED - never
                        # VERIFIED, which requires a successful, verified execution.
                        documentation_status=ProcedureDocumentationStatus.DOCUMENTED_PROCEDURE,
                        lifecycle_status=LifecycleStatus.ACTIVE,
                        execution_count=0,
                        successful_executions=0,
                        failed_executions=0,
                        knowledge_id=knowledge_id,
                    )
                )
            self._count("procedures", linked)

        for q in page.prerequisites:
            self._edge(
                RelationType.PREREQUISITE_OF,
                self._concept(q.prerequisite_name, q),
                self._concept(q.concept_name, q),
                q,
            )
            self.stored["prerequisites"] += 1

        for rel in page.relationships:
            self._edge(
                rel.relation_type,
                self._concept(rel.subject, rel),
                self._concept(rel.object, rel),
                rel,
            )
            self.stored["relationships"] += 1

    def finish(self) -> Stage15:
        """End of the run: concept-equivalence records for the run's concepts (P8-16)."""
        self._stage15.finish(sorted(self._concept_ids.values()))
        return self._stage15

    def _count(self, category: str, linked: bool) -> None:
        self.stored[category] += 1
        if linked:
            self.linked[category] += 1
