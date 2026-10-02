"""Building the inventory (see the package docstring). Reads only."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.calculation.requests import CalculationScope
from app.calculation.results import EvidenceRow, as_plain
from app.calculation.scope import Verdict, source_verdict
from app.models.entities import Source
from app.models.enums import CertaintyState, ExtractionIssueType, KnowledgeType, RelationType
from app.solving import library as equation_library
from app.storage import queries
from app.storage.repository import Repository

#: The kinds a person can ask to see, in the order they are listed, with their plain names.
KINDS: dict[str, str] = {
    "CONCEPT": "Concepts",
    "DEFINITION": "Definitions",
    "EQUATION": "Equations",
    "VARIABLE": "Variables",
    "UNIT": "Units",
    "PROPERTY": "Properties",
    "RULE": "Rules",
    "RELATIONSHIP": "Relationships",
    "EXAMPLE": "Examples",
    "PROCEDURE": "Procedures",
    "PROBLEM": "Not stored",
}
_KNOWLEDGE_KINDS = ("DEFINITION", "EQUATION", "VARIABLE", "UNIT", "PROPERTY", "RULE", "EXAMPLE", "PROCEDURE")

#: What each recorded problem means for the person who added the document, and whether it
#: meant something was left out (`discarded`) or was kept with a warning.
ISSUE_WORDS: dict[str, tuple[str, bool, str]] = {
    "MISSING_SOURCE_REFERENCE": ("Something was found but could not be tied to its exact place in the document, "
                                 "so it was not stored.", True,
                                 "item(s) that could not be tied to an exact place in the document"),
    "INVALID_EQUATION_STRUCTURE": ("An equation looked broken - for example a fraction or an integral split "
                                   "across lines - so it was not stored rather than guessed at.", True,
                                   "equation(s) that looked broken (a fraction or integral split across lines)"),
    "UNKNOWN_UNIT": ("A unit RUDRA does not know was mentioned, so that statement was not stored.", True,
                     "statement(s) with a unit RUDRA does not know"),
    "BROKEN_RELATIONSHIP": ("A relationship the text seemed to state was incomplete, so it was not stored.", True,
                            "relationship(s) the text seemed to state but left incomplete"),
    "OCR_UNCERTAINTY": ("Text could not be read reliably (scanned text, or symbols the PDF lost), so that "
                        "sentence was not stored.", True, "sentence(s) that could not be read reliably"),
    "UNKNOWN_VARIABLE": ("An equation uses a symbol the document never explains. The equation was stored, but "
                         "RUDRA does not know what the symbol stands for.", False,
                         "equation(s) use a symbol the document never explains"),
    "DUPLICATE_CONCEPT": ("A concept appears under two similar names. Both were kept; they are never merged "
                          "automatically.", False, "concept(s) appear under two similar names"),
    "POTENTIAL_CONTRADICTION": ("Two statements seem to disagree. Both were kept and marked as a conflict.", False,
                                "statement(s) that seem to disagree"),
    "MALFORMED_METADATA": ("The document's own details (title, author, ...) could not be read. Nothing else is "
                           "affected.", False, "detail(s) of the document itself that could not be read"),
}
_STATUS_WORDS = {
    "PROCESSED": "Read completely",
    "PARTIALLY_PROCESSED": "Read, with some content not stored",
    "FAILED": "Could not be read",
    "PENDING": "Waiting to be read",
    "PROCESSING": "Being read",
}
#: Lines about one kind of problem that are shown at most this many times.
EXAMPLES_PER_PROBLEM = 4
#: The largest list of items one inventory returns; `truncated` says when it was cut.
MAX_ITEMS = 5000


@dataclass(frozen=True, slots=True)
class IssueGroup:
    """One kind of problem a document's extraction recorded."""

    issue_type: str
    meaning: str
    #: True when the problem meant something was left out of the knowledge base.
    not_stored: bool
    #: A short phrase to follow the count: "177 equation(s) that looked broken ...".
    short: str
    count: int
    examples: tuple[tuple[int | None, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    id: str
    name: str
    source_type: str
    pages: int | None
    status: str
    status_words: str
    #: The document's own copy is still in RUDRA's store.
    file_present: bool
    runs: int
    #: Kinds of knowledge stored from it, by kind.
    stored: dict[str, int]
    #: Items found again from it that other evidence already supported.
    linked: int
    #: Similar statements kept apart rather than merged.
    possible_duplicates: int
    problems: tuple[IssueGroup, ...] = ()


@dataclass(frozen=True, slots=True)
class Item:
    """One thing RUDRA knows, with where it came from."""

    id: str
    kind: str
    title: str
    statement: str = ""
    certainty: str = ""
    #: Why the extraction was not sure of it; empty when it was.
    doubt: str = ""
    document_id: str | None = None
    document: str | None = None
    page: int | None = None
    #: The exact text it was taken from.
    quote: str = ""
    #: The concepts a definition or property belongs to.
    about: tuple[str, ...] = ()
    #: For an equation: "usable" or why a calculation cannot use it.
    calculation: str = ""
    #: How many places in the documents state it.
    sources: int = 1
    #: For an equation: exactly as the document printed it, when `statement` is the readable form.
    printed: str = ""


@dataclass(frozen=True, slots=True)
class Readiness:
    """How much of the stored mathematics a calculation can use."""

    equations: int
    usable: int
    #: Why the others cannot be used, grouped, most common first.
    reasons: tuple[tuple[str, int], ...] = ()
    #: Quantities some document explains ("V = voltage").
    named_quantities: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Totals:
    documents: int
    counts: dict[str, int]
    uncertain: int
    conflicts: int


@dataclass(frozen=True, slots=True)
class Inventory:
    totals: Totals
    documents: tuple[DocumentSummary, ...]
    items: tuple[Item, ...] = ()
    calculation: Readiness = Readiness(0, 0)
    #: The kinds `items` holds (empty: none were asked for).
    kinds: tuple[str, ...] = ()
    truncated: bool = False
    notes: tuple[str, ...] = field(default=())


def to_json(inventory: Inventory) -> str:
    return json.dumps(as_plain(inventory), ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ doubt


def _doubt(certainty: CertaintyState, methods: str) -> str:
    if certainty not in (CertaintyState.UNCERTAIN, CertaintyState.UNVERIFIED):
        return ""
    if "+ocr" in methods:
        return "read by OCR from a scanned page or image; compare it with the source"
    if "+weak" in methods:
        return "recognised from the sentence's general shape, not from an explicit definition"
    if "+layout?" in methods:
        return "rebuilt from where the PDF places its glyphs, but part of the layout was ambiguous"
    if "EquationCandidate" in methods:
        return "read from a PDF's text layer, which can lose fractions and integrals laid out over several lines"
    return "stored as uncertain"


def _first(rows: tuple[EvidenceRow, ...]) -> EvidenceRow | None:
    return rows[0] if rows else None


# ------------------------------------------------------------------ build


def build(repository: Repository, *, kinds: frozenset[str] = frozenset(), document_id: str | None = None,
          scope: CalculationScope = CalculationScope.MY_BOOKS) -> Inventory:
    """The inventory. `kinds` selects which items to list (none: only the summaries)."""
    connection = repository.connection
    sources: dict[str, Source | None] = {}

    def in_scope(row: EvidenceRow) -> bool:
        if row.source_id not in sources:
            sources[row.source_id] = repository.get(Source, row.source_id)
        return source_verdict(sources[row.source_id], scope) is Verdict.IN_SCOPE

    documents = queries.all_documents(connection)
    names = {d.id: d.original_filename or d.filename for d in documents}
    summaries = tuple(_document(repository, d, names[d.id]) for d in documents)

    library = equation_library.load(repository, scope)
    readiness = _readiness(library)
    counts = _totals(connection)
    counts["EQUATION"] = max(counts.get("EQUATION", 0), len(library.equations))
    uncertain = sum(1 for k in queries.knowledge_objects_of_types(connection, [
        KnowledgeType(kind) for kind in _KNOWLEDGE_KINDS]) if k.certainty is CertaintyState.UNCERTAIN)
    totals = Totals(len(documents), counts, uncertain, queries.open_conflict_count(connection))

    items: list[Item] = []
    truncated = False
    wanted = [k for k in KINDS if k in kinds]
    for kind in wanted:
        if kind == "CONCEPT":
            found = _concepts(connection, names, in_scope)
        elif kind == "RELATIONSHIP":
            found = _relationships(connection, names, in_scope)
        elif kind == "PROBLEM":
            found = _problems(connection, summaries, names)
        elif kind == "EQUATION":
            found = _equations(library, names)
        else:
            found = _knowledge(connection, kind, names, in_scope)
        if document_id is not None:
            found = [item for item in found if item.document_id == document_id]
        items.extend(found)
    if len(items) > MAX_ITEMS:
        items, truncated = items[:MAX_ITEMS], True
    notes = ["Worked numeric examples (for instance 'V = 10 V') and question-bank material are deliberately "
             "not stored as knowledge: they are values for one problem, not statements of how things relate."]
    return Inventory(totals, summaries, tuple(items), readiness, tuple(wanted), truncated, tuple(notes))


def _totals(connection) -> dict[str, int]:
    counts = {kind: 0 for kind in KINDS if kind != "PROBLEM"}
    for kind in _KNOWLEDGE_KINDS:
        counts[kind] = len(queries.knowledge_objects_of_types(connection, [KnowledgeType(kind)]))
    counts["CONCEPT"] = len(queries.active_concepts(connection))
    counts["RELATIONSHIP"] = len(queries.relationships_between_concepts(connection))
    return counts


def _document(repository: Repository, document, name: str) -> DocumentSummary:
    connection = repository.connection
    runs = queries.runs_for_document(connection, document.id)
    stored: dict[str, int] = {}
    linked = possible = 0
    problems: tuple[IssueGroup, ...] = ()
    if runs:
        latest = runs[-1]
        stored = {k: n for k, n in queries.knowledge_type_counts_for_run(connection, latest.id).items()}
        concepts = queries.concept_count_for_run(connection, latest.id)
        if concepts:
            stored["CONCEPT"] = concepts
        related = sum(n for key, n in queries.relationship_counts_for_run(connection, latest.id).items()
                      if not key.startswith(("DEFINED_BY", "HAS_PROPERTY")))
        if related:
            stored["RELATIONSHIP"] = related
        linked = queries.linked_objects_of_run(connection, latest.id)
        possible = queries.possible_duplicates_of_run(connection, latest.id)
        issues = queries.issues_for_run(connection, latest.id)
        grouped: dict[str, list] = {}
        for issue in issues:
            grouped.setdefault(issue.issue_type.value, []).append(issue)
        problems = tuple(sorted((
            IssueGroup(kind, *ISSUE_WORDS.get(kind, ("A problem was recorded.", False, "problem(s)")), len(rows),
                       tuple((i.page_number, " ".join((i.excerpt or i.detail).split())[:160])
                             for i in rows[:EXAMPLES_PER_PROBLEM]))
            for kind, rows in grouped.items()), key=lambda g: (not g.not_stored, -g.count, g.issue_type)))
    return DocumentSummary(
        id=document.id, name=name, source_type=document.source_type, pages=document.page_count,
        status=document.processing_status.value,
        status_words=_STATUS_WORDS.get(document.processing_status.value, document.processing_status.value),
        file_present=Path(document.file_path).is_file(), runs=len(runs), stored=stored, linked=linked,
        possible_duplicates=possible, problems=problems)


def _readiness(library) -> Readiness:
    reasons: dict[str, int] = {}
    for stored in library.unreadable:
        reason = stored.unreadable or "not a plain formula"
        key = reason.split(":")[0].strip().rstrip(".")
        key = key[:110]
        reasons[key] = reasons.get(key, 0) + 1
    ordered = tuple(sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))[:6])
    named = tuple(dict.fromkeys((v.symbol, v.name) for v in library.variables if v.name))
    return Readiness(len(library.equations), len(library.usable), ordered, named)


# ------------------------------------------------------------------ the lists


def _where(rows: tuple[EvidenceRow, ...], names: dict[str, str]) -> dict:
    row = _first(rows)
    if row is None:
        return {}
    return {"document_id": row.document_id, "document": names.get(row.document_id), "page": row.page_number,
            "quote": " ".join(row.evidence_text.split())[:400]}


def _scoped(connection, subject_ids, in_scope) -> dict[str, tuple[EvidenceRow, ...]]:
    found = queries.evidence_for_many(connection, subject_ids)
    scoped = {}
    for subject, rows in found.items():
        kept = tuple(r for r in (EvidenceRow.of(x) for x in rows) if in_scope(r))
        if kept:
            scoped[subject] = tuple(sorted(kept, key=lambda r: (r.document_id, r.page_number or 0, r.char_start or 0)))
    return scoped


def _knowledge(connection, kind: str, names, in_scope) -> list[Item]:
    objects = queries.knowledge_objects_of_types(connection, [KnowledgeType(kind)])
    evidence = _scoped(connection, [k.id for k in objects], in_scope)
    owners = queries.concepts_of_knowledge(connection)
    variables = {}
    if kind == "VARIABLE":
        variables = {k.id: v for k, v in queries.active_variables(connection)}
    items = []
    for knowledge in objects:
        rows = evidence.get(knowledge.id)
        if not rows:
            continue
        methods = " ".join(r.extraction_method for r in rows)
        title = knowledge.statement
        if kind == "VARIABLE" and knowledge.id in variables:
            variable = variables[knowledge.id]
            title = f"{variable.symbol} = {variable.name}" if variable.name else f"{variable.symbol} (not explained)"
            title += f" [{variable.unit}]" if variable.unit else ""
        items.append(Item(
            id=knowledge.id, kind=kind, title=" ".join(title.split())[:200],
            statement=" ".join(knowledge.statement.split()), certainty=knowledge.certainty.value,
            doubt=_doubt(knowledge.certainty, methods), about=owners.get(knowledge.id, ()),
            sources=len(rows), **_where(rows, names)))
    return items


def _equations(library, names) -> list[Item]:
    items = []
    for stored in library.equations:
        row = _first(stored.evidence)
        usable = ("usable" if stored.readings else
                  f"cannot be calculated with: {stored.unreadable}")
        methods = " ".join(r.extraction_method for r in stored.evidence)
        items.append(Item(
            id=stored.knowledge_id, kind="EQUATION", title=readable(stored.stored_text)[:200],
            statement=readable(stored.stored_text), printed=" ".join(stored.stored_text.split()),
            certainty=stored.certainty.value,
            doubt=_doubt(stored.certainty, methods), calculation=usable, sources=len(stored.evidence),
            **_where(stored.evidence, names)))
    return items


def _concepts(connection, names, in_scope) -> list[Item]:
    concepts = queries.active_concepts(connection)
    evidence = _scoped(connection, [c.id for c in concepts], in_scope)
    items = []
    for concept in concepts:
        rows = evidence.get(concept.id)
        if not rows:
            continue
        items.append(Item(id=concept.id, kind="CONCEPT", title=concept.canonical_name,
                          statement=concept.description or "", sources=len(rows), **_where(rows, names)))
    return items


_RELATION_WORDS = {
    RelationType.PREREQUISITE_OF: "is a prerequisite of", RelationType.PART_OF: "is part of",
    RelationType.COMPOSED_OF: "is composed of", RelationType.USES: "uses", RelationType.APPLIES_TO: "applies to",
    RelationType.DEPENDS_ON: "depends on", RelationType.REQUIRES: "requires", RelationType.RELATED_TO: "is related to",
    RelationType.INSTANCE_OF: "is an instance of", RelationType.PARENT_OF: "is the parent of",
    RelationType.PRODUCES: "produces", RelationType.CONSTRAINS: "constrains",
    RelationType.EQUIVALENT_TO: "is equivalent to", RelationType.SIMILAR_TO: "is similar to",
    RelationType.ALTERNATIVE_TO: "is an alternative to", RelationType.CONTRADICTS: "contradicts",
    RelationType.DERIVED_FROM: "is derived from",
}


def _relationships(connection, names, in_scope) -> list[Item]:
    relationships = [r for r in queries.relationships_between_concepts(connection)]
    concept_names = {c.id: c.canonical_name for c in queries.active_concepts(connection)}
    evidence = _scoped(connection, [r.id for r in relationships], in_scope)
    items = []
    for relationship in relationships:
        rows = evidence.get(relationship.id)
        if not rows or relationship.from_concept_id not in concept_names or relationship.to_concept_id not in concept_names:
            continue
        left, right = concept_names[relationship.from_concept_id], concept_names[relationship.to_concept_id]
        verb = _RELATION_WORDS.get(relationship.relation_type, relationship.relation_type.value.lower())
        origin = "stated by the document" if relationship.origin.value == "EXPLICIT" else "worked out by RUDRA"
        items.append(Item(id=relationship.id, kind="RELATIONSHIP", title=f"{left} {verb} {right}",
                          statement=origin, sources=len(rows), about=(left, right), **_where(rows, names)))
    return items


def _problems(connection, summaries, names) -> list[Item]:
    items = []
    for summary in summaries:
        runs = queries.runs_for_document(connection, summary.id)
        if not runs:
            continue
        for issue in queries.issues_for_run(connection, runs[-1].id):
            meaning, discarded, _short = ISSUE_WORDS.get(issue.issue_type.value, ("A problem was recorded.", False, ""))
            items.append(Item(
                id=issue.id, kind="PROBLEM", title=meaning,
                statement=("Not stored. " if discarded else "Stored with a warning. ") + issue.detail,
                certainty="NOT_STORED" if discarded else "WARNING", document_id=summary.id, document=summary.name,
                page=issue.page_number, quote=" ".join((issue.excerpt or "").split())[:400]))
    return items


# ------------------------------------------------------------------ reading an equation

_SUPERSCRIPT = str.maketrans("0123456789-+", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺")
_GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "theta": "θ", "lambda": "λ",
          "mu": "μ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "psi": "ψ", "omega": "ω",
          "Delta": "Δ", "Omega": "Ω", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Theta": "Θ", "Lambda": "Λ"}


def readable(text: str) -> str:
    """A stored equation as a person would read it off a line: `\frac{V^{2}}{R}` is `V²/R`.

    Display only - the stored text is never changed. Notation this does not know is left as it
    was stored rather than half-converted.
    """
    out = " ".join(str(text).split())
    out = re.sub(r"\^\s*\{?\s*(-?[0-9]+)\s*\}?", lambda m: m.group(1).translate(_SUPERSCRIPT), out)
    out = re.sub(r"_\s*\{([^{}]*)\}", r"_\1", out)
    frac = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
    while True:
        changed = frac.sub(lambda m: f"{_group(m.group(1))}/{_group(m.group(2))}", out)
        if changed == out:
            break
        out = changed
    out = re.sub(r"\\(?:left|right|big|Big)\s*", "", out)
    out = re.sub(r"\\[,;:! ]", " ", out)
    for command, glyph in (("cdot", "·"), ("times", "×"), ("int", "∫"), ("partial", "∂"), ("infty", "∞"),
                           ("pm", "±"), ("approx", "≈"), ("propto", "∝"), ("Rightarrow", "⇒"), ("sqrt", "√")):
        out = re.sub(rf"\\{command}(?![A-Za-z])", glyph, out)
    out = re.sub(r"\\([A-Za-z]+)", lambda m: _GREEK.get(m.group(1), m.group(0)), out)
    out = re.sub(r"√\s*\{([^{}]*)\}", r"√(\1)", out)
    out = " ".join(out.split())
    return text if ("\\" in out or "{" in out or "}" in out) else out


def _group(part: str) -> str:
    part = part.strip()
    return part if re.fullmatch(r"[\w.]+", part) else f"({part})"
