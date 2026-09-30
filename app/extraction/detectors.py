"""The eleven Part 5 section 190 category detectors (Phase 5, stages 10-11).

Section 190 names eleven things to extract and closes with two constraints:

    Initially prioritize accuracy over maximum extraction volume.
    Do not claim to extract something that the implementation does not actually
    extract.

ADR 0017 reads the first as governing *volume*, not *coverage*, so all eleven are
implemented - and reads the second as binding on what is *claimed*. Every detector
below is therefore deliberately narrow, and what each one does **not** recognise is
written in its docstring rather than left to be discovered.

Detectors receive text that stage 9 (`classify`) has already masked: question
sections, multiple-choice options and worked-example stems are spaces. That is why
nothing here needs to guess whether a sentence is a distractor.

Two of the eleven are expected to yield almost nothing on the supplied acceptance
document, and that is the correct outcome rather than a defect:

* **Procedures** - the document contains four lines beginning "Step N" in 137 pages.
* **Prerequisites** - it contains no prerequisite language at all.

A detector that invented material where the source has none would be exactly the
unsupported assertion Part 1 section 5 forbids. Zero is an answer.

Everything here is deterministic: the standard library's `re` only - no model, no
provider, no new dependency (decision D-39, ADR 0021).
"""

import re

from app.extraction.candidates import (
    ConceptCandidate,
    DefinitionCandidate,
    EquationCandidate,
    ExampleCandidate,
    PageCandidates,
    PrerequisiteCandidate,
    ProcedureCandidate,
    PropertyCandidate,
    RelationshipCandidate,
    RuleCandidate,
    Span,
    UnitCandidate,
    VariableCandidate,
)
from app.extraction.normalize import term as normalize_term
from app.models.enums import RelationType

# --------------------------------------------------------------------- prose

#: A line shorter than this does not *start* a prose run. The acceptance document
#: is 48.9% lines of three characters or fewer - flattened circuit diagrams and
#: matrix brackets - so this is what keeps detectors off figure noise. Measured.
_MIN_PROSE_LENGTH = 25

#: A prose line is mostly letters. Below this it is a figure label or a formula.
_MIN_PROSE_ALPHA = 0.55

#: A short line may *continue* an open run - "produces an" / "electrical current:"
#: is one sentence broken by layout - if it is at least this long and mostly
#: letters. Without this, the last words of many sentences were being cut off.
_MIN_CONTINUATION = 4

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ENDS_SENTENCE = re.compile(r"[.!?:]\s*$")


def _alpha_ratio(text: str) -> float:
    return sum(1 for c in text if c.isalpha()) / len(text) if text else 0.0


def prose_regions(text: str) -> tuple[tuple[int, int, str], ...]:
    """Contiguous runs of prose lines, with offsets into `text`.

    Returns `(start, end, text)` triples, so a candidate found here reports a
    real span rather than one recomputed by searching.
    """
    regions: list[tuple[int, int, str]] = []
    offset = 0
    run_start: int | None = None
    previous = ""
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        starts = (
            len(stripped) >= _MIN_PROSE_LENGTH
            and _alpha_ratio(stripped) >= _MIN_PROSE_ALPHA
            and " " in stripped
        )
        continues = (
            run_start is not None
            and len(stripped) >= _MIN_CONTINUATION
            and _alpha_ratio(stripped) >= _MIN_PROSE_ALPHA
            and not _ENDS_SENTENCE.search(previous)
        )
        if starts or continues:
            if run_start is None:
                run_start = offset
            previous = stripped
        elif run_start is not None:
            regions.append((run_start, offset, text[run_start:offset]))
            run_start = None
            previous = ""
        offset += len(line)
    if run_start is not None:
        regions.append((run_start, offset, text[run_start:offset]))
    return tuple(regions)


def sentences(text: str) -> tuple[tuple[int, int, str], ...]:
    """Sentences of a prose region, with offsets relative to `text`."""
    out: list[tuple[int, int, str]] = []
    cursor = 0
    for part in _SENTENCE_END.split(text):
        if not part.strip():
            cursor += len(part)
            continue
        start = text.find(part, cursor)
        if start < 0:
            continue
        out.append((start, start + len(part), part))
        cursor = start + len(part)
    return tuple(out)


def _flat(sentence: str) -> str:
    """Collapse the line breaks a PDF puts inside one sentence."""
    return re.sub(r"\s+", " ", sentence).strip()


def _each_sentence(text: str):
    """Yield `(start, end, flattened)` for every prose sentence, spans absolute."""
    for r_start, _r_end, region in prose_regions(text):
        for s_start, s_end, raw in sentences(region):
            yield r_start + s_start, r_start + s_end, _flat(raw)


#: Words that are never a concept on their own.
_STOP_TERMS = frozenset(
    {
        "example", "solution", "note", "remark", "figure", "table", "answer",
        "given", "sol", "hence", "therefore", "where", "here", "thus", "so",
        "proof", "case", "step", "let", "now", "then", "for", "from", "this",
        "that", "these", "those", "it", "we", "they", "there", "which", "what",
        "the", "a", "an", "is", "are", "and", "or", "of", "in", "on", "to",
        "such", "same", "above", "below", "following", "circuit shown",
        "network shown", "figure shown",
    }
)

#: Where a term named by "is called X" ends.
_TERM_END = re.compile(
    r"\s*(?:,|;|\(|\band\b|\bwhich\b|\bthat\b|\bor\b|\bif\b|\bfor\b|\bwhen\b|\bwith\b)"
)


#: A noun phrase does not open with a preposition: "said to be in series only
#: when ..." names no concept called "In series only".
_PREPOSITIONS = frozenset(
    {"in", "on", "at", "of", "by", "to", "for", "from", "with", "into", "as", "under", "between"}
)


def _clean_term(term: str) -> str | None:
    """Normalise a candidate term, or reject it."""
    t = normalize_term(term)
    if not (2 <= len(t) <= 46):
        return None
    if t.lower() in _STOP_TERMS:
        return None
    if t.split()[0].lower() in _PREPOSITIONS:
        return None
    if not any(c.isalpha() for c in t):
        return None
    if len(t.split()) > 6:
        return None
    return t


def _leading_term(text: str) -> str | None:
    """The noun phrase at the start of `text`, cut where a term ends."""
    return _clean_term(_TERM_END.split(text, maxsplit=1)[0])


# ----------------------------------------------------- 1. concepts, 2. definitions

#: Verbs whose *subject* is the defined term: "X is defined as ...".
_DEFINES_SUBJECT = re.compile(
    r"\b(?:is defined as|are defined as|refers to|refer to|is the name given to|denotes|is defined to be|"
    r"are defined to be|stands for|is the term for)\b", re.I
)

#: "By X we mean ...", "By the term X, one means ...".
_BY_WE_MEAN = re.compile(
    r"^By\s+(?:the\s+term\s+)?(?:an?\s+|the\s+)?(?P<term>[A-Za-z][\w \-]{1,40}?),?\s+(?:we|one)\s+means?\b", re.I
)
#: "We define X as ...", "We call X ..." is handled by the complement verbs.
_WE_DEFINE = re.compile(
    r"^We\s+define\s+(?:an?\s+|the\s+)?(?P<term>[A-Za-z][\w \-]{1,40}?)\s+(?:as|to be)\b", re.I
)
#: A dash-separated glossary entry: "Anode - the electrode where oxidation occurs."
_DASH_TERM = re.compile(
    r"^\s*(?P<term>[A-Z][A-Za-z][A-Za-z \-/]{1,44}?)\s+[\u2014\u2013-]\s+(?P<body>\S.{15,})$"
)
#: "Resistance is the opposition ...": a general definitional shape. Recognised only
#: with a head noun that definitions use, and stored as UNCERTAIN (weak). "X is the
#: property / characteristic of ..." is a property statement, read by the property
#: detector (`_PROPERTY_OF_SUBJECT`), and is not also taken as a definition.
_BARE_IS = re.compile(
    r"^(?P<term>[A-Z][A-Za-z\-]*(?:\s+[a-z][a-z\-]*){0,3})\s+(?:is|are)\s+(?:the|an?)\s+"
    r"(?:[a-z\-]+\s+){0,2}?(?P<noun>measure|ability|capacity|quantity|rate|process|device|component|"
    r"opposition|flow|amount|ratio|unit|region|type|kind|form|phenomenon|method|principle|law|state|condition|"
    r"study|branch|science|set|collection|element|circuit|material|product|sum|difference|number|value|"
    r"tendency|relationship|force|mechanism|technique|system|instrument|function|variable|constant|"
    r"parameter|measurement|electrode|semiconductor|transistor|particle|substance|field|energy)\b"
)
#: Verbs whose *complement* is the defined term: "... is called X".
_DEFINES_COMPLEMENT = re.compile(
    r"\b(?:is called|are called|is known as|are known as|is termed|are termed|"
    r"is said to be|are said to be)\b",
    re.I,
)

#: A labelled glossary entry - "1. Electron: Electron is a mobile charge carrier."
_LABELLED_TERM = re.compile(
    r"^\s*(?:\d{1,2}\s*[.)]\s*)?(?P<term>[A-Z][A-Za-z][A-Za-z \-/]{1,44}?)\s*:\s+(?P<body>\S.{15,})$"
)

#: "A/An/The <Term> is a/an/the ..." - the classic definitional opening.
_IS_A = re.compile(
    r"^(?:A|An|The)\s+(?P<term>[A-Za-z][A-Za-z \-]{2,44}?)\s+is\s+(?:a|an|the)\s+\S", re.I
)

#: Does a glossary entry's body actually define its label? "It is the
#: capability of an element to store charge" does; "There are free electrons
#: available in all conductive materials" (under the label "Current") does not.
_DEFINITIONAL_OPENING = re.compile(
    r"^(?:It|This|They|These|(?:A|An|The)\s+[\w \-]{1,40}?)\s+(?:is|are|means|refers to)\b", re.I
)


def _glossary_body_defines(label: str, body: str) -> bool:
    head = label.casefold().rstrip("s")
    if body.casefold().startswith(head):
        return True
    if _DEFINITIONAL_OPENING.match(body):
        return True
    return bool(_DEFINES_SUBJECT.search(body) or _DEFINES_COMPLEMENT.search(body))


def detect_definitions(
    page_number: int, text: str
) -> tuple[tuple[ConceptCandidate, ...], tuple[DefinitionCandidate, ...]]:
    """Definitional sentences, and the concepts they introduce.

    **Recognised:** `X is defined as ...`, `X refers to ...`, `X denotes ...`,
    `X stands for ...`, `By X we mean ...`, `We define X as ...`, `... is called X`,
    `... is known as X`, `... is said to be X`, `A <Term> is a ...`, and the glossary
    shapes `Term: body` and `Term - body`. Weakly, and stored as UNCERTAIN:
    `X is the <definitional noun> ...` ("Resistance is the opposition to ..."). A glossary entry introduces the *concept* in
    every case, but yields a *definition* only when its body actually defines the
    label - otherwise the body is an explanation, and labelling it a definition
    would misrepresent what the source says.

    **Not recognised:** definitions spread over several sentences; definitions
    carried only by a figure, a table or an equation; terms that appear in running
    prose without being introduced.
    """
    concepts: list[ConceptCandidate] = []
    definitions: list[DefinitionCandidate] = []
    seen: set[str] = set()

    def concept(term: str, span: Span, flat: str) -> None:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            concepts.append(
                ConceptCandidate(
                    page_number=page_number, span=span, text=flat,
                    name=term, surface_form=term,
                )
            )

    for start, end, flat in _each_sentence(text):
        span = Span(start, end)

        labelled = _LABELLED_TERM.match(flat) or _DASH_TERM.match(flat)
        if labelled is not None:
            label = _clean_term(labelled.group("term"))
            if label is not None:
                body = labelled.group("body").strip()
                concept(label, span, flat)
                if _glossary_body_defines(label, body):
                    definitions.append(
                        DefinitionCandidate(
                            page_number=page_number, span=span, text=flat,
                            concept_name=label, statement=body,
                        )
                    )
                elif re.match(r"(?:the|an?)\s+\w", body, re.I) and not re.search(r"\b(?:is|are|was|were)\b",
                                                                              body.split(",")[0]):
                    # "Anode - the electrode at which ...": a definition by apposition. It
                    # reads as one, but no defining verb says so: stored as UNCERTAIN.
                    definitions.append(
                        DefinitionCandidate(
                            page_number=page_number, span=span, text=flat,
                            concept_name=label, statement=f"{label}: {body}", weak=True,
                        )
                    )
                continue

        term: str | None = None
        weak = False
        for pattern in (_BY_WE_MEAN, _WE_DEFINE):
            explicit = pattern.match(flat)
            if explicit is not None:
                term = _clean_term(explicit.group("term"))
                if term is not None:
                    break
        m = _DEFINES_COMPLEMENT.search(flat) if term is None else None
        if m is not None:
            term = _leading_term(flat[m.end():])
        if term is None:
            m = _DEFINES_SUBJECT.search(flat)
            if m is not None:
                term = _clean_term(flat[: m.start()])
        if term is None:
            m = _IS_A.match(flat)
            if m is not None:
                term = _clean_term(m.group("term"))
        if term is None:
            m = _BARE_IS.match(flat)
            if m is not None:
                term = _clean_term(m.group("term"))
                weak = term is not None
        if term is None:
            continue
        concept(term, span, flat)
        definitions.append(
            DefinitionCandidate(
                page_number=page_number, span=span, text=flat,
                concept_name=term, statement=flat, weak=weak,
            )
        )
    return tuple(concepts), tuple(definitions)


# ---------------------------------------------------------------- 3. equations

#: A line holding a relation: something symbolic, then `=`, `≅`, `≈` or `∝`.
_EQUATION_LINE = re.compile(
    r"^(?P<lhs>[^=\n]{0,40}?[A-Za-zα-ωΔ][^=\n]{0,20}?)\s*"
    r"(?P<op>=|≅|≈|∝)\s*(?P<rhs>\S.{0,120})$"
)

#: Characters that mean the extractor is looking at flattened layout, not an
#: equation: matrix brackets and box-drawing left over from a figure.
_LAYOUT_NOISE = re.compile(r"[⎡-⎭│─]")

#: Unit tokens, removed before asking "does the right-hand side relate symbols?".
_UNIT_TOKEN = re.compile(
    r"(?<![A-Za-z])(?:[kMmunpµG]?(?:Ω|Ω|V|A|W|F|H|Hz|J|C|s|VA|VAR|rad|ohms?))(?![A-Za-z])"
)


def is_numeric_instance(rhs: str) -> bool:
    """True when the right-hand side is a value, not a relation between symbols.

    `VL = 10 V` or `i(t) = 5 A constant` from a worked solution: strip units and
    numbers, and nothing symbolic is left. A chain is judged by its **last** link,
    because a worked computation ends in its result: `L = b − n + 1 = 55 − 11 + 1
    = 45` and `V1 = 19.85 V and V2 = 10.9 V` are arithmetic, not relations. The
    imaginary unit written before a number (`−j6.8`) is part of the number.
    """
    last = re.split(r"=|≅|≈", rhs)[-1]
    rest = re.sub(r"(?<![A-Za-z])j(?=\s*\d)", " ", last)
    rest = _UNIT_TOKEN.sub(" ", rest)
    rest = re.sub(
        r"\b(?:constant|approx|approximately|nearly|only|volts?|amperes?|amps?|ohms?|"
        r"watts?|farads?|henr(?:y|ies)|seconds?|hertz|joules?|coulombs?)\b",
        " ", rest, flags=re.I,
    )
    return not re.search(r"[A-Za-zα-ω]", rest)


#: A line opening with a condition ("If Z = R or ...", "Given i(0+) = 0 and") is a
#: statement of circumstances, not an equation.
_CONDITION_LINE = re.compile(r"^(?:\d{1,2}\.\s*)?(?:If|Given|When|Whenever|Suppose)\b", re.I)


def detect_equations(page_number: int, text: str) -> tuple[EquationCandidate, ...]:
    """Equation lines, exactly as extracted.

    **Recognised:** a line with `=`, `≅`, `≈` or `∝` and a symbolic left-hand side.
    Numeric substitutions (`VL = 10 V`) are marked `numeric` and are counted, not
    stored - a value computed for one worked problem is not a general relation.

    **Not recognised, and deliberately not repaired:** equations whose numerator
    and denominator, or whose integral sign, were laid out apart from the rest.
    A PDF flattens them into several lines - `P = dW/dt` arrives as `P = dW`.
    Stage 12 rejects the fragments it can identify as `INVALID_EQUATION_STRUCTURE`;
    nothing reconstructs them, because a reconstruction would be an invention
    (Part 1 section 5). Fragments that happen to look well formed cannot be told
    apart without layout data, which `pypdf` does not supply (ADR 0014).
    """
    out: list[EquationCandidate] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        m = _EQUATION_LINE.match(stripped) if stripped else None
        if (
            m is not None
            and not _LAYOUT_NOISE.search(stripped)
            and not _CONDITION_LINE.match(stripped)
        ):
            # Notation commands (\frac, \begin{aligned}) are structure, not words.
            words = re.findall(r"[A-Za-z]{4,}", re.sub(r"\\[A-Za-z]+(?:\{[A-Za-z]*\})?", " ", stripped))
            # A sentence that happens to contain '=' is prose, not an equation.
            if len(words) <= 3:
                start = offset + line.index(stripped)
                out.append(
                    EquationCandidate(
                        page_number=page_number,
                        span=Span(start, start + len(stripped)),
                        text=stripped,
                        expression=stripped,
                        numeric=is_numeric_instance(m.group("rhs")),
                    )
                )
        offset += len(line)
    return tuple(out)


# ---------------------------------------------------------------- 4. variables

#: "where V is the voltage", "where R = resistance".
_WHERE_LEGEND = re.compile(
    r"\bwhere\s+(?P<sym>[A-Za-zα-ω][\w₀-₉]{0,4})\s*"
    r"(?:=|\bis\b|\bdenotes\b)\s+(?:the\s+|a\s+|an\s+)?(?P<name>[a-zA-Z][a-zA-Z \-]{2,40})",
    re.I,
)
#: "... and is denoted by V". The *name* is not captured by this pattern: a
#: fixed-width capture started mid-word on the acceptance document ("rical
#: terminology is known as voltage"). `_denoted_name` finds it from the words
#: before the match instead.
_DENOTED = re.compile(
    r"\b(?:and\s+)?(?:is|are)\s+(?:denoted|represented)\s+by\s+"
    r"(?P<sym>[A-Za-zα-ω][\w₀-₉]{0,4})\b",
    re.I,
)
_NAMING_VERB = re.compile(r"\s+(?:is|are)\s+(?:known as|called|termed)\s+", re.I)


def _denoted_name(before: str) -> str | None:
    """The quantity a "denoted by" clause names, from the words before it.

    "Potential difference ... is known as voltage and is denoted by V" names
    voltage; "Power is the rate of change of energy and is denoted by P" names
    power. When neither shape applies the name is left None - an unnamed symbol
    is honest, a wrongly named one is not.
    """
    text = _flat(before).strip(" ,;")
    parts = _NAMING_VERB.split(text)
    if len(parts) > 1:
        return _variable_name(parts[-1])
    subject = re.split(r"\s+(?:is|are)\s+", text, maxsplit=1)[0]
    return _variable_name(subject)


def _variable_name(raw: str) -> str | None:
    name = _flat(raw).strip(" .,;")
    name = re.split(r"\s+(?:and|which|that|in|of|at|across|through)\b", name, maxsplit=1)[0]
    cleaned = _clean_term(name)
    return cleaned.lower() if cleaned else None


def detect_variables(page_number: int, text: str) -> tuple[VariableCandidate, ...]:
    """Symbols the document introduces with a meaning.

    **Recognised:** `where <symbol> is <name>` legends and `<name> is denoted by
    <symbol>`.

    **Not recognised:** symbols that appear only inside equations with no
    introducing sentence. Stage 12 reports those as `UNKNOWN_VARIABLE` rather than
    guessing what they mean.
    """
    out: list[VariableCandidate] = []
    for start, end, flat in _each_sentence(text):
        for m in _WHERE_LEGEND.finditer(flat):
            name = _variable_name(m.group("name"))
            if name:
                out.append(
                    VariableCandidate(
                        page_number=page_number, span=Span(start, end), text=flat,
                        symbol=m.group("sym"), name=name,
                    )
                )
        for m in _DENOTED.finditer(flat):
            name = _denoted_name(flat[: m.start()])
            out.append(
                VariableCandidate(
                    page_number=page_number, span=Span(start, end), text=flat,
                    symbol=m.group("sym"), name=name,
                )
            )
    return tuple(out)


# -------------------------------------------------------------------- 5. units

#: The units this domain actually uses. A closed list on purpose: a stated unit
#: outside it becomes an `UNKNOWN_UNIT` issue rather than an invented unit.
KNOWN_UNITS: dict[str, str] = {
    "v": "volt", "a": "ampere", "w": "watt", "f": "farad", "h": "henry",
    "hz": "hertz", "s": "second", "j": "joule", "c": "coulomb",
    "Ω": "ohm", "Ω": "ohm", "ohm": "ohm", "ohms": "ohm",
    "va": "volt-ampere", "var": "volt-ampere reactive",
    "volt": "volt", "volts": "volt", "ampere": "ampere", "amperes": "ampere",
    "amp": "ampere", "amps": "ampere", "watt": "watt", "watts": "watt",
    "farad": "farad", "farads": "farad", "henry": "henry", "henries": "henry",
    "hertz": "hertz", "joule": "joule", "joules": "joule",
    "coulomb": "coulomb", "coulombs": "coulomb", "second": "second",
    "seconds": "second", "siemens": "siemens", "mho": "siemens", "mhos": "siemens",
    "weber": "weber", "webers": "weber", "tesla": "tesla",
    "rad": "radian", "radian": "radian", "radians": "radian",
    "degree": "degree", "degrees": "degree", "decibel": "decibel", "db": "decibel",
    "neper": "neper", "nepers": "neper",
}

_MEASURED_IN = re.compile(
    r"\b(?:is |are )?(?:measured|expressed)\s+in\s+(?P<unit>[A-Za-zΩΩ]+)"
    r"\s*(?:\((?P<sym>[^)]{1,4})\))?",
    re.I,
)
_UNIT_OF = re.compile(
    r"\b(?:SI\s+)?units?\s+of\s+(?P<qty>[a-z][a-z ]{2,30}?)\s+(?:is|are)\s+"
    r"(?:the\s+)?(?P<unit>[A-Za-zΩΩ]+)\s*(?:\((?P<sym>[^)]{1,4})\))?",
    re.I,
)

#: Words that follow "expressed in" without being units at all. These are not
#: unknown units; they are not units, so they raise no issue.
_NOT_UNITS = frozenset(
    {"terms", "the", "a", "an", "form", "this", "which", "its", "either", "both",
     "such", "units", "per", "general", "polar", "rectangular", "phasor", "time"}
)


def detect_units(page_number: int, text: str) -> tuple[UnitCandidate, ...]:
    """Explicit statements of what unit something is measured in.

    **Recognised:** `measured in <unit> (<symbol>)`, `expressed in <unit>`, and
    `the unit of <quantity> is <unit>`.

    **Not recognised, deliberately:** every bare quantity such as `10 V` or `5 mA`.
    The acceptance document carries about two thousand of them; they are *values*
    that use a unit, not statements about one, and storing each as unit knowledge
    would be the volume-over-accuracy Part 5 section 190 warns against.

    **Not recognised, by design:** unit algebra, dimensional analysis and
    conversion - every structured unit requirement is Phase 11's (D-27, ADR 0012).
    """
    out: list[UnitCandidate] = []
    for pattern in (_UNIT_OF, _MEASURED_IN):
        for m in pattern.finditer(text):
            unit = m.group("unit")
            if unit.casefold() in _NOT_UNITS:
                continue
            qty = m.groupdict().get("qty")
            out.append(
                UnitCandidate(
                    page_number=page_number,
                    span=Span(m.start(), m.end()),
                    text=_flat(m.group(0)),
                    unit=unit,
                    quantity=_flat(qty) if qty else None,
                )
            )
    return tuple(out)


# --------------------------------------------------------------- 6. properties

_PROPERTY_OF_SUBJECT = re.compile(
    r"^(?P<subject>[A-Za-z][A-Za-z \-']{2,40}?)\s+(?:is|are)\s+(?:the|a|an)\s+"
    r"(?:property|properties|characteristic|characteristics)\b"
)
_PROPERTY_NAMED = re.compile(
    r"\b(?:the\s+)?(?:property|characteristic)s?\s+of\s+(?P<subject>[a-zA-Z][\w \-]{2,40}?)\s+(?:is|are)\b",
    re.I,
)


def detect_properties(page_number: int, text: str) -> tuple[PropertyCandidate, ...]:
    """Sentences stating a property of an identifiable subject.

    **Recognised:** `<Subject> is the property of ...` (including inside a glossary
    entry's body) and `the property of <subject> is ...`.

    **Not recognised:** a property stated without the words *property* or
    *characteristic*; figure captions such as "V-I characteristics".

    **Owner (Phase 8, decision P8-24).** Only the second form names the concept the
    property belongs to, so only it records `owner` - its captured subject. In the
    first form the subject is the property itself; an owner after "of" is not
    captured, because nothing defines where that phrase ends. The patterns,
    statements and labels are unchanged.
    """
    out: list[PropertyCandidate] = []
    for start, end, flat in _each_sentence(text):
        labelled = _LABELLED_TERM.match(flat)
        body = labelled.group("body") if labelled else flat
        of_subject = _PROPERTY_OF_SUBJECT.match(body)
        m = of_subject or _PROPERTY_NAMED.search(body)
        if m is None:
            continue
        subject = _clean_term(m.group("subject"))
        if subject is None:
            continue
        out.append(
            PropertyCandidate(
                page_number=page_number, span=Span(start, end), text=flat,
                subject=subject, statement=body,
                owner=None if of_subject is not None else subject,
            )
        )
    return tuple(out)


# -------------------------------------------------------------------- 7. rules

_MODAL = re.compile(r"\b(?:must|shall|should never|never|always|cannot)\b", re.I)
_NAMED_LAW = re.compile(
    r"\b(?P<name>(?!(?:This|That|These|The|Above|Following|Same)\b)"
    r"[A-Z][A-Za-z]+(?:\s?['’]\s?s)?(?:\s+[A-Z][A-Za-z]+)?\s+(?i:law|theorem|principle|rule))\b"
)
#: What makes a sentence that names a law an *invariant statement* rather than a
#: mention of one: it asserts an equality or a constant.
_INVARIANT = re.compile(
    r"\b(?:is|are)\s+(?:equal\s+to|zero|constant|the\s+same)\b|\bequals\b|\bsum\s+of\b.*\bis\b",
    re.I,
)
_CONDITION = re.compile(r"\bif\b(?P<pre>.{5,90}?)(?:,|\bthen\b)(?P<out>.{5,120})", re.I)


def detect_rules(page_number: int, text: str) -> tuple[RuleCandidate, ...]:
    """Deontic and invariant statements (Part 3 section 87, ADR 0017 category 7).

    **Recognised:** modal statements (*must*, *shall*, *never*, *always*,
    *cannot*), and sentences that name a law, theorem, principle or rule **and**
    state an invariant (an equality, a zero, a constant) - Kirchhoff's voltage law
    as stated, not a sentence that merely mentions it.

    **Not recognised:** a sentence that only mentions a theorem ("Steps to Apply
    Superposition Theorem", "This theorem is used to ...") - that is not a rule;
    laws stated without a modal or an invariant predicate; applying a rule or
    checking its applicability, which is Part 3 section 87's second half and
    Phase 10's work. `preconditions` and `output` are filled only when the
    sentence states them with *if ... then*.
    """
    out: list[RuleCandidate] = []
    for start, end, flat in _each_sentence(text):
        modal = _MODAL.search(flat)
        named = _NAMED_LAW.search(flat)
        if modal is None and not (named is not None and _INVARIANT.search(flat)):
            continue
        name = named.group("name") if named else normalize_term(flat[:60])
        cond = _CONDITION.search(flat)
        out.append(
            RuleCandidate(
                page_number=page_number, span=Span(start, end), text=flat,
                name=_flat(name), statement=flat,
                preconditions=_flat(cond.group("pre")) if cond else None,
                output=_flat(cond.group("out")) if cond else None,
            )
        )
    return tuple(out)


# ----------------------------------------------------------------- 8. examples

_EXAMPLE = re.compile(r"^\s*(?P<label>Example\s*[\d.]*)\s*[:.–—-]?\s*(?P<body>.*)$", re.I)


def detect_examples(page_number: int, text: str) -> tuple[ExampleCandidate, ...]:
    """Worked examples.

    **Recognised:** a line beginning `Example` with an optional number. The body is
    the problem statement that follows, taken **only from prose lines** - where an
    example is a figure, its statement is just its printed label, never figure
    noise dressed up as text.

    **Not recognised:** decomposing a worked solution into its steps.
    """
    out: list[ExampleCandidate] = []
    offset = 0
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        m = _EXAMPLE.match(stripped) if stripped else None
        if m is not None:
            body = _flat(m.group("body"))
            if not body:
                following: list[str] = []
                for nxt in lines[index + 1: index + 6]:
                    s = nxt.strip()
                    if len(s) >= _MIN_CONTINUATION and _alpha_ratio(s) >= _MIN_PROSE_ALPHA:
                        following.append(s)
                    elif following:
                        break
                body = _flat(" ".join(following))[:400]
            start = offset + line.index(stripped)
            label = _flat(m.group("label"))
            out.append(
                ExampleCandidate(
                    page_number=page_number,
                    span=Span(start, start + len(stripped)),
                    text=stripped, label=label, statement=body or label,
                )
            )
        offset += len(line)
    return tuple(out)


# --------------------------------------------------------------- 9. procedures

#: An explicit, ordered step. Only "Step N" counts: a bare "1." in a technical
#: book is far more often a numbered definition list or a multiple-choice option,
#: and the acceptance document proves it - 625 numbered lines, of which four are
#: steps. Matching those 625 would manufacture procedures that do not exist.
_STEP = re.compile(r"^\s*Step\s*(?P<n>\d{1,2})\s*[:.–—-]?\s*(?P<body>.*)$", re.I)


def detect_procedures(page_number: int, text: str) -> tuple[ProcedureCandidate, ...]:
    """Ordered step sequences the document states.

    **Recognised:** two or more `Step N` lines on one page.

    **Not recognised:** an unlabelled numbered list - this detector's whole design
    constraint, and the reason the acceptance document yields almost no procedures:
    it has four `Step N` lines in 137 pages. Zero is the correct answer where a
    document has no procedures; Part 5 section 190's closing line makes producing
    more than there are the error.

    A procedure extracted here is `DOCUMENTED_PROCEDURE` (Part 3 section 111) and
    carries no authority to execute anything (Part 3 section 109, ADR 0023).
    """
    steps: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        m = _STEP.match(stripped) if stripped else None
        if m is not None:
            start = offset + line.index(stripped)
            steps.append((start, start + len(stripped), _flat(m.group("body")) or stripped))
        offset += len(line)
    if len(steps) < 2:
        return ()
    first, last = steps[0], steps[-1]
    return (
        ProcedureCandidate(
            page_number=page_number,
            span=Span(first[0], last[1]),
            # The evidence is what the page printed across the whole step block,
            # verbatim. The joined step list is kept separately in `steps` and
            # becomes the knowledge object's normalised statement (Part 6
            # section 9) - it must never masquerade as the source's own text.
            text=_flat(text[first[0]: last[1]]),
            name=f"Procedure stated on page {page_number}",
            steps=tuple(s[2] for s in steps),
        ),
    )


# ------------------------------------------------------------ 10. prerequisites

_PREREQUISITE = re.compile(
    r"\bis\s+(?:a\s+)?pre-?requisites?\s+(?:for|of|to)\s+(?P<target>[A-Za-z][\w \-]{2,40})", re.I
)
_REQUIRES_KNOWLEDGE = re.compile(
    r"\brequires?\s+(?:a\s+)?(?:prior\s+)?(?:knowledge|understanding)\s+of\s+(?P<prereq>[A-Za-z][\w \-]{2,40})",
    re.I,
)


def detect_prerequisites(page_number: int, text: str) -> tuple[PrerequisiteCandidate, ...]:
    """Prerequisites the document explicitly states.

    **Recognised:** `X is a prerequisite for Y` and `Y requires a knowledge of X`.

    **Not recognised:** a prerequisite inferred from chapter ordering, from
    co-occurrence, or from a concept being mentioned earlier. That inference is
    Phase 6's classification work (Part 5 sections 192-193), and producing it here
    would put an `INFERRED` edge into a phase that creates only `EXPLICIT` ones.

    The supplied acceptance document contains **no** prerequisite language, so this
    detector correctly returns nothing for it.
    """
    out: list[PrerequisiteCandidate] = []
    for start, end, flat in _each_sentence(text):
        span = Span(start, end)
        m = _REQUIRES_KNOWLEDGE.search(flat)
        if m is not None:
            subject = _clean_term(flat[: m.start()])
            prereq = _leading_term(m.group("prereq"))
            if subject and prereq:
                out.append(
                    PrerequisiteCandidate(
                        page_number=page_number, span=span, text=flat,
                        concept_name=subject, prerequisite_name=prereq,
                    )
                )
            continue
        m = _PREREQUISITE.search(flat)
        if m is not None:
            prereq = _clean_term(flat[: m.start()])
            target = _leading_term(m.group("target"))
            if prereq and target:
                out.append(
                    PrerequisiteCandidate(
                        page_number=page_number, span=span, text=flat,
                        concept_name=target, prerequisite_name=prereq,
                    )
                )
    return tuple(out)


# ------------------------------------------------------------ 11. relationships

#: Stated relations, mapped onto Part 2 section 39's vocabulary. Each phrase maps
#: to exactly one relation type; nothing is guessed when no phrase matches.
_RELATION_PHRASES: tuple[tuple[re.Pattern[str], RelationType], ...] = (
    (re.compile(r"\b(?:consists of|consist of|is composed of|is made up of)\b", re.I), RelationType.COMPOSED_OF),
    (re.compile(r"\b(?:is a part of|is part of|forms part of)\b", re.I), RelationType.PART_OF),
    (re.compile(r"\b(?:is a type of|is a kind of|is an example of|is a form of|is a special case of)\b", re.I), RelationType.INSTANCE_OF),
    (re.compile(r"\b(?:depends on|depend on|is dependent on|is a function of)\b", re.I), RelationType.DEPENDS_ON),
    (re.compile(r"\b(?:is equivalent to|are equivalent to|is the same as)\b", re.I), RelationType.EQUIVALENT_TO),
    (re.compile(r"\b(?:is derived from|is obtained from)\b", re.I), RelationType.DERIVED_FROM),
    (re.compile(r"\b(?:can be replaced by|may be replaced by)\b", re.I), RelationType.ALTERNATIVE_TO),
    (re.compile(r"\b(?:is used to|is used in|is used for|are used to|are used in)\b", re.I), RelationType.USES),
    (re.compile(r"\b(?:applies to|is applicable to|is applicable only to)\b", re.I), RelationType.APPLIES_TO),
    (re.compile(r"\b(?:requires|must satisfy|must obey)\b", re.I), RelationType.REQUIRES),
    (re.compile(r"\b(?:produces|supplies|deliver|delivers|generates)\b", re.I), RelationType.PRODUCES),
    (re.compile(r"\b(?:constrains|is limited by)\b", re.I), RelationType.CONSTRAINS),
)


def detect_relationships(page_number: int, text: str) -> tuple[RelationshipCandidate, ...]:
    """Relations the document states.

    **Recognised:** twelve relational phrases, each mapped to exactly one Part 2
    section 39 relation type, with a subject before and an object after.

    **Not recognised:** any relation RUDRA would have to derive. Every candidate
    here that survives becomes an `EXPLICIT` edge carrying this sentence as its
    evidence (decision D-28, ADR 0011); Phase 5 creates no `INFERRED` edges at all
    (ADR 0017). Stage 12 additionally requires **both endpoints to be concepts the
    document itself defines** - a relation between two undefined noun phrases is
    recorded as `BROKEN_RELATIONSHIP`, not stored.
    """
    out: list[RelationshipCandidate] = []
    for start, end, flat in _each_sentence(text):
        # A stated prerequisite ("requires a knowledge of X") is category 10's,
        # not a generic REQUIRES edge; reading it twice would record a spurious
        # broken relationship beside a correct prerequisite.
        if _REQUIRES_KNOWLEDGE.search(flat) or _PREREQUISITE.search(flat):
            continue
        for pattern, relation in _RELATION_PHRASES:
            m = pattern.search(flat)
            if m is None:
                continue
            subject = _clean_term(flat[: m.start()])
            obj = _leading_term(flat[m.end():].split(".")[0])
            if subject is None or obj is None or subject.casefold() == obj.casefold():
                break
            out.append(
                RelationshipCandidate(
                    page_number=page_number, span=Span(start, end), text=flat,
                    subject=subject, object=obj, relation_type=relation,
                )
            )
            break
    return tuple(out)


# ------------------------------------------------------------------ all eleven


def detect_all(
    page_number: int, segment_id: str, expository: str, instructional: str
) -> PageCandidates:
    """Run all eleven Part 5 section 190 detectors over one classified page.

    `expository` feeds every claim-bearing detector. `instructional` - which still
    contains worked-example stems - feeds only the EXAMPLE detector, because an
    example's stem is exactly what an EXAMPLE record should hold.
    """
    concepts, definitions = detect_definitions(page_number, expository)
    return PageCandidates(
        page_number=page_number,
        segment_id=segment_id,
        concepts=concepts,
        definitions=definitions,
        equations=detect_equations(page_number, expository),
        variables=detect_variables(page_number, expository),
        units=detect_units(page_number, expository),
        properties=detect_properties(page_number, expository),
        rules=detect_rules(page_number, expository),
        examples=detect_examples(page_number, instructional),
        procedures=detect_procedures(page_number, expository),
        prerequisites=detect_prerequisites(page_number, expository),
        relationships=detect_relationships(page_number, expository),
    )
