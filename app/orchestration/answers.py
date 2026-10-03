"""The final response architecture (ADR 0054 P22-5 ... P22-8; sections 227-230). Pure.

Each builder reads one command's own answer - the plain JSON object that command prints
with --json - and returns section 227's parts for it:

    Answer, Basis, Reasoning, Calculation, Assumptions, Sources, Status, Missing information

and, where they apply, section 228's *"I cannot determine this from the currently
authorized information"* (missing, why it is required, available, unavailable, next steps),
section 229's conflict lines, and section 230's *"Unknown."*. Nothing is invented: every
part is read from the command's answer, and a part the answer does not hold is left empty.
"""

import re
from dataclasses import dataclass

CANNOT = "I cannot determine this from the currently authorized information."
UNKNOWN = "Unknown."
INSUFFICIENT = "Insufficient information."
NEXT_STEPS = (
    "Try asking in other words.",
    "Add a document that covers it (python -m app extract FILE.pdf).",
    'Look it up on one website you choose: python -m app research "QUESTION" --site URL.',
)
MAX_SOURCES = 5

#: Which query groups answer which requested output (the interpreter's `requested_output`).
_GROUPS = {
    "definition": ("Definitions",),
    "explanation": ("Definitions", "Properties", "Relationships", "Dependencies"),
    "equations": ("Equations", "Variables"),
    "variables": ("Variables", "Equations"),
    "properties": ("Properties",),
    "applications": ("Applications",),
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


def _keyword_items(keyword: dict) -> list[dict]:
    """Keyword mode's own shape (ADR 0035/0036 P9-3, P9-22): stored text matched the term
    through the derived index, then every hit was re-read from knowledge.db and
    scope-checked - rendered the same honest way concept mode renders a stored statement.
    A keyword hit is never presented as a stored link to a concept (it is not one)."""
    records = []
    for item in keyword.get("knowledge", ()):
        knowledge = item.get("knowledge") or {}
        kind = str(knowledge.get("knowledge_type") or "Statement").replace("_", " ").title()
        line = f"{kind}: {' '.join(str(knowledge.get('statement', '')).split())}"
        why = _why_uncertain(knowledge, item.get("evidence", ()))
        records.append({"kind": kind, "line": line, "basis": knowledge.get("id", ""),
                        "sources": [_source(row) for row in item.get("evidence", ())],
                        "uncertain": f"{line} - {why}" if why else None})
    for item in keyword.get("concepts", ()):
        concept = item.get("concept") or {}
        records.append({"kind": "Concept", "line": f"Concept: {concept.get('canonical_name', '')}",
                        "basis": concept.get("id", ""),
                        "sources": [_source(row) for row in item.get("occurrences", ())], "uncertain": None})
    return records


#: The order statements that merely mention a term are shown in: what states something first.
_MENTION_ORDER = {"Definition": 0, "Rule": 1, "Property": 2, "Equation": 3, "Concept": 4, "Variable": 5, "Unit": 6,
                  "Procedure": 7, "Example": 8}
MAX_MENTIONS = 8


MAX_PASSAGES = 3


def _passages(text: str, term: str, limit: int = MAX_PASSAGES) -> list[str]:
    """The sentences or lines of a page that contain `term`, trimmed, in reading order."""
    wanted = " ".join(term.casefold().split())
    found: list[str] = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", text):
        flat = " ".join(chunk.split())
        if wanted and wanted in flat.casefold() and flat not in found:
            found.append(flat if len(flat) <= 240 else flat[:237] + "...")
        if len(found) >= limit:
            break
    return found


def from_mentions(unknown: "Part", keyword_answer: dict, name: str, output: str | None = None) -> "Part":
    """No stored definition of `name`, but the documents mention it: say exactly that.

    What follows is the stored text that contains the words - labelled as mentions, never
    as RUDRA's definition of the concept, which is what an exact lookup would have returned.
    Where no stored statement mentions it but a page's own text does, that sentence is quoted
    as page text, which RUDRA did not extract as knowledge.
    """
    keyword = keyword_answer.get("keyword") or {}
    records = _keyword_items(keyword)
    records.sort(key=lambda r: _MENTION_ORDER.get(r["kind"], 9))
    shown, more = records[:MAX_MENTIONS], max(0, len(records) - MAX_MENTIONS)
    passages: list[tuple[str, str]] = []  # (line, source)
    if not shown:
        for hit in keyword.get("pages", ()):
            segment = hit.get("segment") or {}
            document = (hit.get("document") or {}).get("document") or {}
            where = f"{document.get('original_filename') or segment.get('document_id', '?')}, page {segment.get('page_number', '?')}"
            for sentence in _passages(str(segment.get("text", "")), str(keyword.get("term") or name)):
                passages.append((f"Page text ({where}): {sentence}",
                                 f"{segment.get('document_id', '?')} p.{segment.get('page_number', '?')}: \"{sentence}\""))
            if len(passages) >= MAX_PASSAGES:
                break
        passages = passages[:MAX_PASSAGES]
    missing = {"equations": "No equation is linked to", "variables": "No variable is linked to",
               "properties": "No property is stored for", "applications": "No application is stored for",
               "prerequisites": "No prerequisite is stored for",
               "locations": "No stored statement defines or describes"}.get(output or "", "No definition of")
    if not shown:
        header = f"Nothing about '{name}' was stored as knowledge, but your documents mention it:"
    elif missing == "No definition of":
        header = f"No definition of '{name}' is stored, but your documents mention it:"
    else:
        header = f"{missing} '{name}' in your documents, but they mention it:"
    lines = [header, *(r["line"] for r in shown), *(line for line, _source in passages)]
    if more:
        lines.append(f"...and {more} more statement(s) that mention it.")
    sources = [src for r in shown for src in r["sources"]] + [source for _line, source in passages]
    why = (f"These are statements that contain the words. They are not a stored definition of '{name}': no "
           "document states one in a form RUDRA recognises." if shown else
           "This is the page's own text. RUDRA did not extract it as knowledge, so it cannot reason with it "
           "or calculate from it.")
    # Asked for what a thing is, the mentions are the knowledge there is. Asked for a specific link
    # (its equations, variables, properties ...) that no stored edge gives, the answer is still
    # Unknown - the mentions are shown beside it, not instead of it.
    answered = output in (None, "definition", "explanation", "summary", "definitions_all", "everything")
    return Part(
        number=unknown.number, request=unknown.request, intent=unknown.intent, path="KNOWLEDGE",
        command=unknown.command, status="ANSWERED" if answered else "UNKNOWN", answer="\n".join(lines),
        basis=_dedup(r["basis"] for r in shown if r["basis"]), sources=_dedup(sources)[:MAX_SOURCES],
        reasoning=(why,), uncertain=_dedup(r["uncertain"] for r in shown if r["uncertain"]),
        next_steps=() if answered else NEXT_STEPS, detail=keyword_answer)


def from_query(number: int, request: str, intent: str, command, answer: dict, output: str | None) -> Part:
    """A `query` answer: the requested group(s) of the resolved concept, or - in keyword
    mode, which resolves no concept - the stored text that matched (sections 227, 229, 230)."""
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
    keyword = answer.get("keyword") or {}
    if keyword:
        for record in _keyword_items(keyword):
            lines.append(record["line"])
            basis.append(record["basis"])
            sources.extend(record["sources"])
            if record["uncertain"]:
                uncertain.append(record["uncertain"])
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


_CHECKED = {
    "VERIFIED": "checked: an independent evaluation reproduced every step",
    "INCONCLUSIVE": "not independently confirmed: the value is the one you gave",
    "FAILED": "the independent check FAILED - do not rely on this result",
}
SOLVE_NEXT_STEPS = (
    "Give the missing value or values, for example by adding them to the question.",
    "Use the symbol your documents use for a quantity, if you named it differently.",
    "Add a document that states the relation you need.",
)


def from_solution(number: int, request: str, intent: str, command, answer: dict) -> Part:
    """A `solve` answer: the quantity RUDRA calculated by choosing the stored equations itself,
    with the route it took and its checks - or what the documents do not establish."""
    common = dict(number=number, request=request, intent=intent, path="KNOWLEDGE", command=tuple(command), detail=answer)
    names = {symbol: name for symbol, name in answer.get("quantities") or ()}

    def label(symbol: str) -> str:
        return f"{symbol} ({names[symbol]})" if names.get(symbol) else str(symbol)

    target = answer.get("target") or {}
    goal = target.get("symbol") or target.get("asked") or "the quantity"
    known = [g for g in answer.get("givens") or () if g.get("symbol")]
    available = tuple(f"{label(g['symbol'])} = {g['value']}" for g in known)
    steps = answer.get("steps") or []
    status = answer.get("status")
    if status == "CALCULATED" and answer.get("result"):
        result = answer["result"]
        unit = f" {result['unit']}" if result.get("unit") else ""
        value = f"{goal} {result.get('relation', '=')} {result.get('displayed', '?')}{unit}"
        reasoning = [f"Asked for {label(goal)}" + (f" from {', '.join(available)}." if available else ".")]
        if steps:
            reasoning.append(f"Chose {len(steps)} equation(s) from your documents, applied in this order.")
        reasoning += [note for note in answer.get("notes") or ()]
        calculation = []
        for step in steps:
            turned = " (the stored equation, turned around)" if step.get("rearranged") else ""
            calculation.append(f"Step {step['number']}: {step['formula']}{turned}  →  {step['substitution']}"
                               f" = {step['result']['displayed']}{' ' + step['result']['unit'] if step['result'].get('unit') else ''}")
        if not steps:
            calculation.append(f"{goal} was among the values you gave.")
        calculation.append(_CHECKED.get(answer.get("verification"), "not independently checked").capitalize())
        sources, basis = [], []
        for step in steps:
            basis.append(step["knowledge_id"])
            sources.append(f"{step['knowledge_id']}: the stored equation \"{' '.join(str(step['stored_text']).split())}\"")
            sources.extend(_source(row) for row in step.get("evidence", ())[:2])
        basis += [b["knowledge_id"] for b in [target, *known] if b.get("knowledge_id")]
        uncertain = tuple(answer.get("uncertain") or ())
        unused = answer.get("unused") or ()
        assumptions = (f"Not needed for this result: {', '.join(unused)}.",) if unused else ()
        return Part(status="ANSWERED", answer=value, reasoning=tuple(reasoning), calculation=tuple(calculation),
                    assumptions=assumptions, sources=_dedup(sources)[:MAX_SOURCES * 2], basis=_dedup(b for b in basis if b),
                    available=available, uncertain=uncertain, **common)
    if status == "CONFLICTING":
        lines = ["Conflict detected: stored equations give different values for " + label(goal) + "."]
        for letter, route in zip("ABCDEFGH", answer.get("routes") or ()):
            value = route.get("value") or {}
            lines.append(f"Route {letter}: {' ; '.join(route.get('equations', ()))} gives "
                         f"{label(goal)} {value.get('relation', '=')} {value.get('displayed', '?')} {value.get('unit', '')}".rstrip())
        lines.append("Resolution: Not automatically selected.")
        return Part(status="CANNOT_DETERMINE", answer=f"{CANNOT} {answer.get('message', '')}".strip(),
                    conflicts=tuple(lines), available=available, next_steps=SOLVE_NEXT_STEPS[2:],
                    reasoning=tuple(answer.get("notes") or ()), **common)
    missing_lines, unavailable, why = [], [], []
    for gap in answer.get("missing") or ():
        needs = ", ".join(label(n) for n in gap.get("needs") or ()) or "nothing more"
        missing_lines.append(f"{gap['formula']} would give it, and needs {needs}")
        unavailable.extend(gap.get("needs") or ())
        why.append(f"{gap['formula']} ({gap['knowledge_id']}) needs {needs}")
    unknown = answer.get("unknown") or ()
    for asked in unknown:
        missing_lines.append(f"what '{asked}' stands for - none of your documents' equations or explanations says")
    known_names = [f"{symbol} = {name}" for symbol, name in answer.get("known_quantities") or ()]
    reasoning = list(answer.get("notes") or ())
    if known_names:
        reasoning.append("Quantities your documents name: " + "; ".join(known_names[:12]) + ".")
    reasoning += [hint for hint in answer.get("suggestions") or ()]
    for route in answer.get("routes") or ():
        if route.get("problem"):
            reasoning.append(f"{' ; '.join(route.get('equations', ()))}: {route['problem']}")
    return Part(status="CANNOT_DETERMINE", answer=f"{CANNOT} {answer.get('message', '')}".strip(),
                reasoning=tuple(reasoning), missing=tuple(missing_lines), why=_dedup(why), available=available,
                unavailable=_dedup(unavailable), next_steps=SOLVE_NEXT_STEPS, **common)


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
