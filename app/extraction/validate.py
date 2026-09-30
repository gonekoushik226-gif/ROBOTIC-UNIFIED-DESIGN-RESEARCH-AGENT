"""Stage 12 - validation and quality checking (Part 2 section 60).

Section 60's stage order is::

    Extraction -> Validation -> Normalization -> Provenance attachment -> Storage

so validation runs **before** normalization, not after provenance. `ARCHITECTURE.md`
section 7.1 previously had it the other way round; the specification wins under
ADR 0007's rule, and the difference is material: validating first stops a malformed
equation being normalized into something that looks valid (ADR 0022).

Section 60's closing line is the whole point of this module:

    Problems should be recorded rather than silently ignored.

Nothing here repairs anything. A broken equation is rejected and recorded with the
text exactly as extracted; Part 1 section 5 forbids presenting a repaired guess as
what the source said.

**Glyph loss in prose.** Font-encoding corruption in the acceptance document turns
`∝` into `a`, `∴` into `\\` and `Ω` into a box. Section 60's vocabulary has no
dedicated type for text-layer character loss, and ADR 0022 fixed that vocabulary
at section 60's nine. Such sentences are therefore rejected and recorded as
`OCR_UNCERTAINTY` - the check about uncertainty in recognised text - with the
detail stating explicitly that **no OCR ran** and the text came from the native
text layer. That mapping is an interpretation, recorded in `docs/phases/PHASE_5.md`
so it can be ratified or replaced by a vocabulary amendment.
"""

import re
from dataclasses import dataclass, replace

from app.extraction.candidates import PageCandidates
from app.extraction.detectors import KNOWN_UNITS
from app.models.enums import ExtractionIssueType
from app.models.naming import normalize_alias

#: Symbols that appear in equations but are never variables in their own right.
_NOT_A_VARIABLE = frozenset(
    {"e", "d", "t", "x", "n", "log", "ln", "sin", "cos", "tan", "exp",
     "max", "min", "avg", "rms", "ac", "dc", "j", "k", "f"}
)

_SYMBOL = re.compile(r"[A-Za-zα-ω][\w₀-₉]{0,3}")
_PREFIXES = frozenset({"k", "M", "m", "u", "n", "p", "µ", "G"})

#: Signatures of a glyph the text layer lost. Each was observed in the acceptance
#: document; none is repaired, all are reported.
_GLYPH_LOSS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile("�"), "a Unicode replacement character"),
    (re.compile("□"), "an empty box where a symbol (typically Ω) was"),
    # A backslash beginning a notation command (\frac, \int, \alpha ...) is structured
    # mathematics from Word, HTML or EPUB; a bare one is a PDF's lost sign.
    (re.compile(r"\\(?![A-Za-z]{2,}|[{},;:!\\])"), "a backslash where a mathematical sign (typically ∴) was"),
    (re.compile(r"\b[A-Z]\s+a\s+[A-Z]\b"), "'a' between two symbols, where ∝ was"),
)

#: Issue types that mean knowledge was discarded, so the run is `PARTIAL`
#: (Part 2 section 61: "potentially incomplete knowledge").
DISCARDING_ISSUES: frozenset[ExtractionIssueType] = frozenset(
    {
        ExtractionIssueType.MISSING_SOURCE_REFERENCE,
        ExtractionIssueType.INVALID_EQUATION_STRUCTURE,
        ExtractionIssueType.UNKNOWN_UNIT,
        ExtractionIssueType.BROKEN_RELATIONSHIP,
        ExtractionIssueType.OCR_UNCERTAINTY,
    }
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem, before it becomes an `ExtractionIssue` row."""

    issue_type: ExtractionIssueType
    detail: str
    page_number: int | None = None
    excerpt: str | None = None


def glyph_loss(text: str) -> str | None:
    """What glyph the text layer appears to have lost, or None."""
    for pattern, description in _GLYPH_LOSS:
        if pattern.search(text):
            return description
    return None


def check_equation_structure(expression: str) -> str | None:
    """Why this equation is unreliable, or None if nothing identifiable is wrong.

    Shallow on purpose: Phase 11 owns parsing (Part 3 sections 91-92, Part 5
    section 202). This decides only whether the *text* can be presented as an
    equation. Each check below is a signature measured on the acceptance document.
    """
    lost = glyph_loss(expression)
    if lost:
        return f"contains {lost}"
    if expression.count("{") != expression.count("}"):
        return "unbalanced braces"
    # Structured notation (from Word, HTML or EPUB mathematics) states its fractions and
    # integrals explicitly; the flattening signatures below must not read them as lost.
    fraction = "/" if re.search(r"\\[dt]?frac\b", expression) else ""
    integral = "∫" if re.search(r"\\(?:i{1,3}nt|oint)\b", expression) else ""
    if "==" in expression:
        return ("two '=' signs run together - what a PDF produces when a fraction's "
                "numerator and denominator are laid out on separate lines")
    if expression.count("(") != expression.count(")"):
        return "unbalanced parentheses"
    if expression.count("[") != expression.count("]"):
        return "unbalanced brackets"
    if "()" in expression:
        return "empty parentheses - their contents were laid out elsewhere"
    if re.search(r"[+\-−–]\s+[+\-−–]", expression):
        return "two operators in a row - the operand between them was laid out elsewhere"
    if re.search(r"[.;]\s+[A-Z][a-z]{2,}\s", expression):
        return "a sentence runs on after the expression, so this is prose, not an equation"
    lhs, _, rhs = expression.partition("=")
    rhs = rhs.strip()
    if not rhs:
        return "nothing on the right-hand side"
    if re.match(r"[⋅×*/^]", rhs):
        return "the right-hand side opens with an operator, so its first operand is missing"
    if re.search(r"[+\-−×*/^=.]\s*$", rhs):
        return "the right-hand side ends in an operator, so its remainder is missing"
    if re.search(r"\b(?:and|or|where|then|with|of)\s*$", rhs, re.I):
        return "the line ends mid-clause, so the rest of the statement is on another line"
    lhs_is_differential = bool(re.match(r"\s*d[A-Za-z]", lhs))
    if (
        re.search(r"(?<![A-Za-z])d[A-Za-zα-ω](?![A-Za-z])", rhs)
        and "/" not in rhs + fraction
        and "∫" not in rhs + integral
        and not lhs_is_differential
    ):
        return ("a differential with no denominator and no integral sign - a derivative "
                "or integral flattened across lines")
    if re.fullmatch(r"d", rhs):
        return "the right-hand side is a lone 'd', the start of a flattened derivative"
    # "W = Pd tV Id t Vt": the "dt" of an integral split by a space, integral sign lost.
    if re.search(r"[A-Za-z]d\s+t\b", rhs) and "/" not in rhs + fraction and "∫" not in rhs + integral:
        return ("'d t' split by a space with no integral sign or denominator - an "
                "integral or derivative flattened across lines")
    # Four or more letters: "Energy" is a word, "Pdt" (P times dt) is symbols.
    if re.fullmatch(r"[A-Z][a-z]{3,}", rhs):
        return (f"the right-hand side is the single word {rhs!r} - a word fraction "
                "(such as Energy / Time) flattened across lines")
    return None


def _unit_is_known(unit: str) -> bool:
    u = unit.strip()
    if u.casefold() in KNOWN_UNITS:
        return True
    return len(u) >= 2 and u[0] in _PREFIXES and u[1:].casefold() in KNOWN_UNITS


def _key(name: str) -> str:
    return normalize_alias(name)


@dataclass(frozen=True, slots=True)
class PageValidation:
    survivors: PageCandidates
    findings: tuple[Finding, ...]
    #: Numeric substitutions (`VL = 10 V`) seen and deliberately not stored.
    numeric_equations: int


def page_glyph_loss(page_number: int, text: str) -> tuple[Finding, ...]:
    """Every glyph-loss signature in a page's extractable text, one finding each.

    Candidate-level checks only see sentences a detector consumed. Corruption in
    any other line would then go unrecorded - measured on the acceptance document,
    where page 83's "(V a I ohm's Law)" was matched by no detector. This scan
    records each distinct signature once per page, with the line it occurs on as
    the excerpt, verbatim.

    `text` should be the *expository* text from stage 9: corruption inside a
    question section cannot reach knowledge, because nothing is extracted there.
    """
    findings: list[Finding] = []
    for pattern, description in _GLYPH_LOSS:
        match = pattern.search(text)
        if match is None:
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line = text[line_start: line_end if line_end >= 0 else len(text)].strip()
        findings.append(
            Finding(
                issue_type=ExtractionIssueType.OCR_UNCERTAINTY,
                detail=(f"the text on this page contains {description}. No OCR ran - "
                        "the native PDF text layer lost the glyph (font encoding), so "
                        "text near it cannot be trusted as printed; it is not repaired"),
                page_number=page_number,
                excerpt=line[:300],
            )
        )
    return tuple(findings)


def validate_page(
    candidates: PageCandidates,
    *,
    known_terms: frozenset[str],
    needs_ocr: bool,
    page_text: str | None = None,
) -> PageValidation:
    """Check one page's candidates against section 60.

    `known_terms` is the normalised name of every concept the document defines in
    this run (decision D-30 normalisation, nothing more). A relationship or
    prerequisite is stored only when **both** endpoints are among them.

    `page_text`, when given, is scanned for glyph loss as a whole (see
    `page_glyph_loss`), so corruption is recorded even where no detector fired.

    `DUPLICATE_CONCEPT` and `POTENTIAL_CONTRADICTION` are **never** produced here:
    both need the equivalence machinery ADR 0020 keeps in Phase 8, and
    `ExtractionIssue.validate()` refuses them outright.
    """
    page = candidates.page_number
    findings: list[Finding] = []

    def lost(category: str, text: str) -> bool:
        why = glyph_loss(text)
        if why is None:
            return False
        findings.append(
            Finding(
                issue_type=ExtractionIssueType.OCR_UNCERTAINTY,
                detail=(f"{category} not stored: the sentence contains {why}. No OCR "
                        "ran - the native PDF text layer lost the glyph (font "
                        "encoding), so the sentence cannot be trusted as printed"),
                page_number=page,
                excerpt=text[:300],
            )
        )
        return True

    # --- Missing source reference ---------------------------------------------
    if not candidates.segment_id:
        if candidates.total:
            findings.append(
                Finding(
                    issue_type=ExtractionIssueType.MISSING_SOURCE_REFERENCE,
                    detail=("candidates were found but no segment could be referenced, "
                            "so none was stored"),
                    page_number=page,
                )
            )
        return PageValidation(
            PageCandidates(page_number=page, segment_id=candidates.segment_id),
            tuple(findings), 0,
        )

    # --- OCR uncertainty (page yielded no usable text) --------------------------
    if needs_ocr:
        findings.append(
            Finding(
                issue_type=ExtractionIssueType.OCR_UNCERTAINTY,
                detail=("the page yielded no usable text, so no knowledge was extracted "
                        "from it; no OCR engine is installed (decision D-04)"),
                page_number=page,
            )
        )

    # --- Glyph loss anywhere in the page's extractable text --------------------
    if page_text:
        findings.extend(page_glyph_loss(page, page_text))

    # --- Invalid equation structure -------------------------------------------
    equations, numeric = [], 0
    for equation in candidates.equations:
        if equation.numeric:
            numeric += 1
            continue
        problem = check_equation_structure(equation.expression)
        if problem is None:
            equations.append(equation)
            continue
        findings.append(
            Finding(
                issue_type=ExtractionIssueType.INVALID_EQUATION_STRUCTURE,
                detail=f"equation not stored: {problem}",
                page_number=page,
                excerpt=equation.expression[:300],
            )
        )

    # --- Unknown unit ---------------------------------------------------------
    units = []
    for unit in candidates.units:
        if lost("unit statement", unit.text):
            continue
        if _unit_is_known(unit.unit):
            units.append(unit)
            continue
        findings.append(
            Finding(
                issue_type=ExtractionIssueType.UNKNOWN_UNIT,
                detail=f"unit {unit.unit!r} is not in the recognised set, so it was not stored",
                page_number=page,
                excerpt=unit.text[:300],
            )
        )

    # --- Broken relationship --------------------------------------------------
    relationships = []
    for relationship in candidates.relationships:
        if lost("relationship", relationship.text):
            continue
        missing = [
            end for end in (relationship.subject, relationship.object)
            if _key(end) not in known_terms
        ]
        if not missing:
            relationships.append(relationship)
            continue
        findings.append(
            Finding(
                issue_type=ExtractionIssueType.BROKEN_RELATIONSHIP,
                detail=(f"{relationship.relation_type} relationship not stored: "
                        f"{', '.join(repr(m) for m in missing)} is not a concept this "
                        "document defines, so the endpoint cannot be resolved"),
                page_number=page,
                excerpt=relationship.text[:300],
            )
        )

    prerequisites = []
    for prerequisite in candidates.prerequisites:
        if lost("prerequisite", prerequisite.text):
            continue
        missing = [
            end for end in (prerequisite.concept_name, prerequisite.prerequisite_name)
            if _key(end) not in known_terms
        ]
        if not missing:
            prerequisites.append(prerequisite)
            continue
        findings.append(
            Finding(
                issue_type=ExtractionIssueType.BROKEN_RELATIONSHIP,
                detail=(f"PREREQUISITE_OF not stored: {', '.join(repr(m) for m in missing)} "
                        "is not a concept this document defines"),
                page_number=page,
                excerpt=prerequisite.text[:300],
            )
        )

    survivors = replace(
        candidates,
        definitions=tuple(d for d in candidates.definitions if not lost("definition", d.text)),
        equations=tuple(equations),
        variables=tuple(v for v in candidates.variables if not lost("variable", v.text)),
        units=tuple(units),
        properties=tuple(p for p in candidates.properties if not lost("property", p.text)),
        rules=tuple(r for r in candidates.rules if not lost("rule", r.text)),
        examples=tuple(e for e in candidates.examples if not lost("example", e.statement)),
        procedures=tuple(p for p in candidates.procedures if not lost("procedure", p.text)),
        prerequisites=tuple(prerequisites),
        relationships=tuple(relationships),
    )
    return PageValidation(survivors, tuple(findings), numeric)


def unknown_variables(pages: tuple[PageCandidates, ...]) -> tuple[Finding, ...]:
    """Symbols used on the left of a stored equation that the document never introduces.

    Checked **document-wide**, once per symbol: a textbook introduces a symbol once
    per chapter, not on every page, so a page-local check reported the same symbol
    hundreds of times. Reported, never given an invented meaning. This finding does
    not discard the equation - the equation is still what the source printed.
    """
    introduced = {v.symbol.casefold() for page in pages for v in page.variables}
    reported: set[str] = set()
    findings: list[Finding] = []
    for page in pages:
        for equation in page.equations:
            m = _SYMBOL.match(equation.expression.partition("=")[0].strip())
            if m is None:
                continue
            symbol = m.group(0)
            key = symbol.casefold()
            if key in introduced or key in _NOT_A_VARIABLE or key in reported:
                continue
            reported.add(key)
            findings.append(
                Finding(
                    issue_type=ExtractionIssueType.UNKNOWN_VARIABLE,
                    detail=(f"symbol {symbol!r} is used in an equation, but the document "
                            "never introduces what it means with a legend"),
                    page_number=page.page_number,
                    excerpt=equation.expression[:300],
                )
            )
    return tuple(findings)
