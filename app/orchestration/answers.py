"""The final response architecture (ADR 0054 P22-5 ... P22-8; sections 227-230). Pure.

Each builder reads one command's own answer - the plain JSON object that command prints
with --json - and returns section 227's parts for it:

    Answer, Basis, Reasoning, Calculation, Assumptions, Sources, Status, Missing information

and, where they apply, section 228's *"I cannot determine this from the currently
authorized information"* (missing, why it is required, available, unavailable, next steps),
section 229's conflict lines, and section 230's *"Unknown."*. Nothing is invented: every
part is read from the command's answer, and a part the answer does not hold is left empty.
"""

from dataclasses import dataclass

CANNOT = "I cannot determine this from the currently authorized information."
UNKNOWN = "Unknown."
INSUFFICIENT = "Insufficient information."
NEXT_STEPS = (
    "Provide the missing information.",
    "Use another authorized source (python -m app extract FILE.pdf).",
    'Authorize an Internet search: python -m app research "QUESTION" --site URL.',
    "Cancel.",
)
MAX_SOURCES = 5

#: Which query groups answer which requested output (the interpreter's `requested_output`).
_GROUPS = {
    "definition": ("Definitions",),
    "explanation": ("Definitions", "Properties", "Relationships", "Dependencies"),
    "equations": ("Equations", "Variables"),
    "variables": ("Variables", "Equations"),
    "properties": ("Properties",),
    "definitions_all": ("Definitions",),
    "summary": ("Definitions", "Properties", "Equations", "Variables", "Applications", "Examples", "Prerequisites",
                "Dependencies", "Relationships"),
    "locations": ("Definitions", "Properties", "Equations", "Variables", "Examples", "Applications"),
    "prerequisites": ("Prerequisites",),
    "everything": ("Definitions", "Properties", "Prerequisites", "Dependencies", "Applications", "Relationships",
                   "Equations", "Variables", "Examples"),
}


@dataclass(frozen=True, slots=True)
class Part:
    """One part of a request and its answer (section 227)."""

    number: int
    #: What the part asked, in words.
    request: str
    intent: str
    #: KNOWLEDGE, ACTION, or NOT_RUN.
    path: str
    #: The command that answered a knowledge part (as the user could run it), or ().
    command: tuple[str, ...]
    #: ANSWERED, UNKNOWN, INSUFFICIENT, CANNOT_DETERMINE, NEEDS_INFORMATION, NOT_RUN, or an
    #: action outcome (DONE, FAILED, REFUSED, NOT_EXECUTED).
    status: str
    answer: str
    basis: tuple[str, ...] = ()
    reasoning: tuple[str, ...] = ()
    calculation: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    why: tuple[str, ...] = ()
    available: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    #: An action part's steps, each with its verification, or a documented workflow.
    actions: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    #: The command's or the pipeline's whole answer, for traceability (JSON form only).
    detail: object = None
    #: Answer lines resting on uncertain recognition, each with the reason (OCR, a general
    #: sentence shape, an equation from a PDF text layer).
    uncertain: tuple[str, ...] = ()


def _source(row: dict) -> str:
    page = "" if row.get("page_number") is None else f" p.{row['page_number']}"
    return f"{row.get('document_id', '?')}{page}: \"{' '.join(str(row.get('evidence_text', '')).split())}\""


def _why_uncertain(knowledge: dict, evidence) -> str | None:
    """Why a stored statement is uncertain, from how its evidence was obtained; None if it is not."""
    if knowledge.get("certainty") != "UNCERTAIN":
        return None
    methods = " ".join(str(row.get("extraction_method", "")) for row in evidence or ())
    if "+ocr" in methods:
        return "recognized by OCR from a scanned page or image; compare it with the source"
    if "+weak" in methods:
        return "recognised from the sentence's general shape, not an explicit definition"
    if "+layout?" in methods:
        return ("rebuilt from where the PDF page places its glyphs, but part of the layout is ambiguous; "
                "compare it with the source page")
    if "EquationCandidate" in methods:
        return "read from a PDF's text layer, which can lose fractions and integrals laid out over several lines"
    return "stored as uncertain"


def _dedup(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def from_query(number: int, request: str, intent: str, command, answer: dict, output: str | None) -> Part:
    """A `query` answer: the requested group(s) of the resolved concept (sections 227, 229, 230)."""
    status = answer.get("status")
    common = dict(number=number, request=request, intent=intent, path="KNOWLEDGE", command=tuple(command), detail=answer)
    if status == "NOT_FOUND":
        return Part(status="UNKNOWN", answer=f"{UNKNOWN} {answer.get('message', '')}".strip(),
                    next_steps=NEXT_STEPS, **common)
    if status == "INSUFFICIENT_AUTHORIZED_INFORMATION":
        return Part(status="INSUFFICIENT", answer=f"{INSUFFICIENT} {answer.get('message', '')}".strip(),
                    next_steps=NEXT_STEPS, **common)
    concept = answer.get("concept") or {}
    wanted = _GROUPS.get(output or "definition", _GROUPS["definition"])
    lines, basis, sources, uncertain = [], [], [], []
    for resolved in concept.get("concepts", ()):
        identity = resolved.get("concept", {})
        basis.append(identity.get("id", ""))
    for group in concept.get("groups", ()):
        if group.get("name") not in wanted:
            continue
        for item in group.get("items", ()):
            knowledge = item.get("knowledge")
            if knowledge and output == "locations":
                # "Where is X stated?": the location is the answer asked for.
                label = group['name'][:-1] if group['name'].endswith('s') else group['name']
                for row in item.get("evidence", ())[:3]:
                    page = row.get("page_number")
                    lines.append(f"{label} stated in {row.get('document_id', '?')}"
                                 + (f", page {page}" if page is not None else "")
                                 + f": \"{' '.join(str(row.get('evidence_text', '')).split())}\"")
                basis.append(knowledge.get("id", ""))
                sources.extend(_source(row) for row in item.get("evidence", ()))
            elif knowledge:
                lines.append(f"{group['name'][:-1] if group['name'].endswith('s') else group['name']}: "
                             f"{' '.join(str(knowledge.get('statement', '')).split())}")
                why = _why_uncertain(knowledge, item.get("evidence", ()))
                if why:
                    uncertain.append(f"{lines[-1]} - {why}")
                basis.append(knowledge.get("id", ""))
                sources.extend(_source(row) for row in item.get("evidence", ()))
            elif item.get("links"):
                edge = item["links"][0].get("edge", {})
                relationship = edge.get("relationship", {})
                names = ", ".join(n.get("canonical_name", "") for n in item.get("neighbours", ()))
                lines.append(f"{relationship.get('relation_type', 'RELATED')}: {names}")
                basis.append(relationship.get("id", ""))
                sources.extend(_source(row) for row in edge.get("evidence", ()))
    conflicts = []
    for item in concept.get("conflicts", ()):
        record = item.get("conflict", {})
        conflicts.append(f"Conflict detected: {record.get('id', '?')} (cause {record.get('cause', 'UNDETERMINED')}"
                         + (f"; context: {record['context']}" if record.get("context") else "") + ")")
        for label, claim in (("Source A", item.get("claim_a", {})), ("Source B", item.get("claim_b", {}))):
            knowledge = claim.get("knowledge") or {}
            where = "; ".join(_source(row) for row in claim.get("evidence", ())[:2]) or "no evidence in scope"
            conflicts.append(f"{label}: {claim.get('knowledge_id', '?')} \"{knowledge.get('statement', '')}\" - {where}")
        conflicts.append("Resolution: Not automatically selected.")
    if not lines:
        return Part(status="UNKNOWN", answer=f"{UNKNOWN} Nothing of the kind asked for is stored for "
                    f"{request!r} in the authorized scope.", basis=_dedup(basis), conflicts=tuple(conflicts),
                    next_steps=NEXT_STEPS, **common)
    return Part(status="ANSWERED", answer="\n".join(lines), basis=_dedup(b for b in basis if b),
                sources=_dedup(sources)[:MAX_SOURCES], conflicts=tuple(conflicts), uncertain=_dedup(uncertain),
                **common)


def from_reasoning(number: int, request: str, intent: str, command, answer: dict) -> Part:
    """A `reason` answer: what the target requires, and what is missing (sections 227, 228)."""
    common = dict(number=number, request=request, intent=intent, path="KNOWLEDGE", command=tuple(command), detail=answer)
    methods = answer.get("methods") or []
    reasoning, missing, why, basis = [], [], [], []
    for method in methods:
        for step in method.get("steps", ()):
            reasoning.append(str(step.get("text") or step.get("explanation") or step))
        for gap in method.get("missing", ()):
            concept = gap.get("concept", {})
            name = f"{concept.get('canonical_name', '?')} ({concept.get('id', '?')})"
            missing.append(name)
            by = ", ".join(r.get("concept_id", "?") + " via " + r.get("relationship_id", "?")
                           for r in gap.get("required_by", ()))
            why.append(f"{name} is required by {by}")
            basis.extend(r.get("relationship_id", "") for r in gap.get("required_by", ()))
    status = answer.get("status", "")
    if status == "CANNOT_DETERMINE" or missing:
        return Part(status="CANNOT_DETERMINE", answer=f"{CANNOT} {answer.get('message', '')}".strip(),
                    reasoning=tuple(reasoning), missing=_dedup(missing), why=_dedup(why),
                    unavailable=_dedup(missing), basis=_dedup(b for b in basis if b), next_steps=NEXT_STEPS, **common)
    return Part(status="ANSWERED", answer=answer.get("message", ""), reasoning=tuple(reasoning),
                basis=_dedup(b for b in basis if b), **common)


def from_calculation(number: int, request: str, intent: str, command, answer: dict) -> Part:
    """A `calculate` answer: the result with its steps and verification, or what is missing."""
    common = dict(number=number, request=request, intent=intent, path="KNOWLEDGE", command=tuple(command), detail=answer)
    steps = tuple(" ".join(str(s.get("text") or s.get("explanation") or s).split()) for s in answer.get("steps", ()))
    assumptions = tuple(str(a.get("text", a)) for a in answer.get("assumptions", ()))
    available = tuple(i.get("symbol", "?") + " = " + i.get("text", "?") for i in answer.get("inputs", ()))
    sources = tuple(f"{f.get('text', '?')} ({f.get('origin', '?')}" + (f", {f['knowledge_id']}" if f.get("knowledge_id") else "") + ")"
                    for f in answer.get("formulas", ()))
    if answer.get("status") == "CALCULATED" and answer.get("result"):
        result = answer["result"]
        value = f"{answer.get('target', '?')} {result.get('relation', '=')} {result.get('displayed', '?')}"
        value += f" {result['unit']}" if result.get("unit") else ""
        return Part(status="ANSWERED", answer=value, calculation=steps + (
            f"verification: {answer.get('verification_status', 'not stated')}",), assumptions=assumptions,
                    sources=sources, available=available, **common)
    missing = tuple(m.get("symbol", "?") for m in answer.get("missing", ()))
    why = tuple(f"{m.get('symbol', '?')} is required by {', '.join(m.get('required_by', ()))}"
                for m in answer.get("missing", ()))
    return Part(status="CANNOT_DETERMINE", answer=f"{CANNOT} {answer.get('message', '')}".strip(), calculation=steps,
                assumptions=assumptions, sources=sources, missing=missing, why=why, available=available,
                unavailable=missing, next_steps=NEXT_STEPS, **common)


def from_provenance(number: int, request: str, intent: str, command, answer: dict) -> Part:
    common = dict(number=number, request=request, intent=intent, path="KNOWLEDGE", command=tuple(command), detail=answer)
    sources = tuple(_source(c.get("evidence", {})) for c in answer.get("citations", ()))
    status = "ANSWERED" if answer.get("status") == "AVAILABLE" else "UNKNOWN"
    return Part(status=status, answer=answer.get("message", ""), sources=sources[:MAX_SOURCES],
                basis=(answer.get("identifier", ""),), **common)


def from_diagram(number: int, request: str, intent: str, command, answer: dict) -> Part:
    common = dict(number=number, request=request, intent=intent, path="KNOWLEDGE", command=tuple(command), detail=answer)
    spec = answer.get("specification") or {}
    if answer.get("status") == "DRAWN":
        return Part(status="ANSWERED", answer=f"{answer.get('message', '')} Image: {answer.get('svg_path')}",
                    basis=tuple(spec.get("identifiers", ())), missing=tuple(spec.get("unknowns", ())), **common)
    status = "INSUFFICIENT" if answer.get("status") == "INSUFFICIENT_AUTHORIZED_INFORMATION" else "NOT_RUN"
    return Part(status=status, answer=answer.get("message", ""), next_steps=NEXT_STEPS if status == "INSUFFICIENT" else (),
                **common)


def from_pipeline(number: int, request: str, intent: str, report: dict) -> Part:
    """An action part: the pipeline's outcome and each step's verification (section 223)."""
    execution = report.get("execution") or {}
    lines = tuple(f"{s.get('action', '?')}: {s.get('status', '?')} - {s.get('observed', '')}"
                  for s in execution.get("steps", ()))
    workflows = tuple(" → ".join(step.get("text", "") for step in w.get("steps", ())) for w in report.get("workflows", ()))
    missing = tuple(m for w in report.get("workflows", ()) for m in w.get("missing", ()))
    return Part(number=number, request=request, intent=intent, path="ACTION", command=(), status=report.get("outcome", "?"),
                answer=report.get("message", ""), actions=lines + workflows, missing=missing, detail=report)


def not_run(number: int, request: str, intent: str, reason: str, status: str = "NOT_RUN",
            missing: tuple[str, ...] = (), next_steps: tuple[str, ...] = ()) -> Part:
    return Part(number=number, request=request, intent=intent, path="NOT_RUN", command=(), status=status,
                answer=reason, missing=missing, next_steps=next_steps)
