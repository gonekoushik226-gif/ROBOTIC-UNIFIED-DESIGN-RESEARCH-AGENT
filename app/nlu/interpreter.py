"""The deterministic request interpreter (ADR 0045; Part 5 sections 206-207).

`interpret(text)` turns one request into structured intents - **data only**. It performs
no reasoning, calculation, retrieval or action, opens no database or file, and runs
nothing, not even the command it generates (section 207; P13-2). No model or provider is
involved (P13-1).

**The grammar** (`GRAMMAR_NAME`, version `GRAMMAR_VERSION`) is a closed list of rules,
each a case-insensitive pattern over one cleaned clause and a handler that extracts the
entities and parameters. Every rule that matches is considered; only the most specific
priority level counts; if its matches name **different intent types**, the clause is
`AMBIGUOUS` and every candidate is listed - none is chosen (section 97). A clause no rule
matches is `UNRECOGNIZED`, with examples; nothing is guessed.

**Clauses:** a request joined by *and* / *then* before a command verb is split into
clauses when every clause is recognised (P13-9), so *"Open Calculator and calculate
123 × 456"* stays two requests: application control, then calculation (section 237).

**Extraction is textual and exact** (P13-6): what the words say, trimmed of articles and
politeness, never completed from anywhere else. A calculation without formulas is
`INCOMPLETE`: no formula or value is ever supplied by guesswork (P13-7).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from app.core.errors import InvalidInputError
from app.models.enums import RiskLevel
from app.models.identifiers import is_valid_id
from app.nlu.lexicon import EXAMPLES, KNOWLEDGE_REASONING, RISK, resolve_application
from app.nlu.results import (
    ActionRequest,
    Entity,
    EntityType,
    Interpretation,
    InterpretationStatus,
    StructuredIntent,
)

GRAMMAR_NAME = "RUDRA request grammar"
GRAMMAR_VERSION = "2"
MAX_TEXT = 2000

_NUMBER = r"[-−]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
_SYMBOL = re.compile(r"[^\W\d_]\w*")
_VALUE = re.compile(rf"(?P<number>{_NUMBER})\s*(?P<unit>[^\s,;]+)?")
_ARITHMETIC = re.compile(r"[0-9\s.eE+\-−*/×÷·^()]+")
_OPERATOR = re.compile(r"[+\-−*/×÷·^]")
_REFERENCES = ("it", "this", "that", "these", "those", "them")
_REFERENCE_NOUNS = ("project", "file", "document", "folder", "application", "app", "program",
                    "answer", "result", "window")
_VERBS = (r"open|launch|start|run|close|quit|exit|calculate|compute|evaluate|determine|type|press|"
          r"take|capture|create|make|copy|move|rename|delete|remove|save|search|find|what|where|"
          r"draw|generate|paste|look")
_SPLIT = re.compile(rf"\s*(?:,\s*)?\b(?:and then|and|then)\s+(?=(?:{_VERBS})\b)", re.IGNORECASE)
_POLITE = re.compile(r"^(?:please|kindly|can you|could you|would you|rudra)[,\s]+", re.IGNORECASE)
_SCOPES = (
    (re.compile(r"\s+(?:in|from|using) (?:all )?authori[sz]ed sources$", re.IGNORECASE), "AUTHORIZED"),
    (re.compile(r"\s+(?:in|from|using|according to|based on|with) my (?:books|documents|notes|files|sources)$",
                re.IGNORECASE), "MY_BOOKS"),
)


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="nlu.request", data_changed=False, retry_safe=True,
        next_options=tuple(f'python -m app interpret "{example}"' for example in EXAMPLES[:3]),
    )


def clean(text: str) -> str:
    """One clause as the rules read it: whitespace collapsed, politeness and final
    punctuation removed. The words themselves are never changed."""
    text = re.sub(r"\s+", " ", text).strip()
    while True:
        stripped = _POLITE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return re.sub(r"(?:\s+please)?[.!?]+$", "", text).strip()


def _name(text: str) -> str:
    """A concept or quantity name: leading articles removed, nothing else changed."""
    return re.sub(r"^(?:the|a|an)\s+", "", text.strip(), flags=re.IGNORECASE).strip()


def _scope(text: str) -> tuple[str, str | None]:
    for pattern, scope in _SCOPES:
        if pattern.search(text):
            return pattern.sub("", text).strip(), scope
    return text, None


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        return text[1:-1]
    return text.strip("“”")


def _is_arithmetic(text: str) -> bool:
    """Numbers and operators only, with at least one digit and one operator."""
    return bool(_ARITHMETIC.fullmatch(text) and _OPERATOR.search(text) and re.search(r"[0-9]", text))


def _is_reference(text: str) -> bool:
    words = text.casefold().split()
    if words and words[0] in ("the", "that", "this", "my"):
        words = words[1:]
    return (text.casefold() in _REFERENCES) or (len(words) == 1 and words[0] in _REFERENCE_NOUNS)


def _looks_like_path(text: str) -> bool:
    return bool(re.search(r"[\\/]", text) or re.fullmatch(r"\S+\.[A-Za-z0-9]{1,5}", text))


# ------------------------------------------------------------------ building intents


def _intent(intent_type: str, classes: tuple[str, ...], rule: str, **fields) -> StructuredIntent:
    """An intent with its declared risk (section 106): read-only requests are LOW."""
    risk, confirm = RISK.get(intent_type, (RiskLevel.LOW, False))
    fields.setdefault("status", InterpretationStatus.INTERPRETED)
    return StructuredIntent(intent_id="", intent_type=intent_type, task_classes=classes, rule=rule,
                            risk_level=risk, requires_confirmation=confirm, **fields)


def _query(rule: str, target_text: str, output: str | None, cls: str = "KNOWLEDGE_QUERY") -> StructuredIntent:
    text, scope = _scope(target_text)
    if is_valid_id(text.strip()):
        return _intent("QUERY_ITEM", (cls,), rule, target=text.strip(),
                       entities=(Entity(EntityType.IDENTIFIER, text.strip(), text.strip()),),
                       command=("query", text.strip()), requested_output=output)
    name = _name(text)
    command = ("query", "--name", name) + (("--scope", scope.lower().replace("_", "-")) if scope else ())
    notes = ()
    if output == "equations":
        notes = ("Equations are shown only when a stored relationship links them to the concept; "
                 "extraction links none (Phase 9, D1).",)
    return _intent("QUERY_CONCEPT", (cls,), rule, target=name, source_scope=scope, requested_output=output,
                   entities=(Entity(EntityType.CONCEPT, text, name),), command=command, notes=notes,
                   constraints=() if scope is None else (f"source scope {scope}",))


def _calculation(rule: str, target_text: str, given: str | None) -> StructuredIntent:
    """A calculation request: complete when the text states a symbol, formulas and values."""
    target_text = target_text.strip()
    formulas: list[str] = []
    inputs: list[str] = []
    unreadable: list[str] = []
    if given:
        for piece in (p.strip() for p in re.split(r"\s*(?:,|;|\band\b)\s*", given) if p.strip()):
            left, sep, right = piece.partition("=")
            left, right = left.strip(), right.strip()
            if not sep or not _SYMBOL.fullmatch(left) or not right:
                unreadable.append(piece)
            elif _VALUE.fullmatch(right):
                inputs.append(f"{left}={right}")
            else:
                formulas.append(f"{left} = {right}")
    elif "=" in target_text:
        left, _, right = target_text.partition("=")
        if _SYMBOL.fullmatch(left.strip()) and right.strip():
            formulas.append(f"{left.strip()} = {right.strip()}")
            target_text = left.strip()
    if not given and not formulas and _is_arithmetic(target_text):
        formulas.append(f"value = {target_text}")
        target = "value"
        entities = (Entity(EntityType.EXPRESSION, target_text, target_text),)
        return _intent("CALCULATE", ("CALCULATION",), rule, target=target, entities=entities,
                       parameters=(("formula", formulas[0]),), requested_output="value",
                       command=("calculate", target, "--formula", formulas[0]),
                       notes=("The symbol `value` is a default name for the expression's result "
                              "(section 97 allows a harmless presentation default).",))
    symbol = target_text if _SYMBOL.fullmatch(target_text) else None
    name = _name(target_text)
    entities = [Entity(EntityType.SYMBOL if symbol else EntityType.QUANTITY, target_text, symbol or name)]
    entities += [Entity(EntityType.FORMULA, f, f) for f in formulas]
    entities += [Entity(EntityType.VALUE, i, i) for i in inputs]
    parameters = tuple(("formula", f) for f in formulas) + tuple(("input", i) for i in inputs)
    missing = []
    if unreadable:
        missing.append("each given item as SYMBOL = value or SYMBOL = formula; could not read: "
                       + "; ".join(unreadable))
    if symbol is None:
        missing.append(f"the symbol that stands for '{name}' in the formulas")
    if not formulas:
        missing.append("the formulas (RUDRA does not choose a formula for you)")
    if symbol is not None and not formulas and not inputs:
        missing.append("the input values")
    elif symbol is None and not inputs:
        missing.append("the input values")
    classes = ("CALCULATION", KNOWLEDGE_REASONING)
    if missing:
        routes = () if symbol else (("reason", name),)
        return _intent("CALCULATE", classes, rule, status=InterpretationStatus.INCOMPLETE, target=name,
                       entities=tuple(entities), parameters=parameters, missing=tuple(missing),
                       requested_output="value", routes=routes,
                       notes=("The language layer does not reason or calculate (section 207). The route "
                              "below asks what the quantity requires from stored knowledge; it is a route, "
                              "not an answer.",) if routes else ())
    command = ["calculate", symbol]
    for formula in formulas:
        command += ["--formula", formula]
    for value in inputs:
        command += ["--input", value]
    return _intent("CALCULATE", classes, rule, target=symbol, entities=tuple(entities), parameters=parameters,
                   requested_output="value", command=tuple(command))


def _application(rule: str, intent_type: str, said: str) -> StructuredIntent | None:
    said = _name(re.sub(r"\s+(?:application|app|program)$", "", said.strip(), flags=re.IGNORECASE))
    if not said or _is_reference(said) or _looks_like_path(said) or is_valid_id(said) \
            or re.match(r"^(?:a |the |new )?(?:file|folder|project|document|diagram|screenshot)\b", said, re.I):
        return None
    name, candidates = resolve_application(said)
    if candidates:
        return _intent(intent_type, ("APPLICATION_CONTROL",), rule, status=InterpretationStatus.AMBIGUOUS,
                       target=said, entities=(Entity(EntityType.APPLICATION, said, said, known=False),),
                       candidates=candidates, missing=("which application: " + ", ".join(candidates),))
    known = name is not None
    application = name or said
    notes = () if known else (f"'{said}' is not in RUDRA's application name table; the application "
                              "registry (Phase 14) resolves or refuses it.",)
    return _intent(intent_type, ("APPLICATION_CONTROL",), rule, target=application,
                   entities=(Entity(EntityType.APPLICATION, said, application, known=known),),
                   parameters=(("application", application),), notes=notes,
                   action=ActionRequest(intent_type, (("application", application),)))


def _file_action(rule: str, intent_type: str, **paths: str) -> StructuredIntent | None:
    values = {k: _unquote(v) for k, v in paths.items() if v is not None}
    if any(_is_reference(v) for v in values.values()):
        return None
    entities = tuple(Entity(EntityType.FILE_PATH, v, v) for v in values.values())
    parameters = tuple(values.items())
    return _intent(intent_type, ("FILE_OPERATION",), rule, target=next(iter(values.values()), None),
                   entities=entities, parameters=parameters, action=ActionRequest(intent_type, parameters))


def _reference(rule: str, verb: str, reference: str) -> StructuredIntent:
    kind = {"open": "APPLICATION_CONTROL", "launch": "APPLICATION_CONTROL", "start": "APPLICATION_CONTROL",
            "run": "APPLICATION_CONTROL", "close": "APPLICATION_CONTROL"}.get(verb.casefold(), "FILE_OPERATION")
    return _intent("UNRESOLVED_REFERENCE", (kind,), rule, status=InterpretationStatus.AMBIGUOUS,
                   target=reference, entities=(Entity(EntityType.REFERENCE, reference, reference),),
                   parameters=(("verb", verb.casefold()),),
                   missing=(f"which {reference.split()[-1]} to {verb.casefold()}: name it; RUDRA keeps no memory of "
                            "earlier requests and never picks one for you (section 97)",))


def _source(rule: str, reference: str) -> StructuredIntent:
    reference = _unquote(reference.strip())
    if is_valid_id(reference):
        return _intent("SOURCE_QUERY", ("SOURCE_QUERY",), rule, target=reference,
                       entities=(Entity(EntityType.IDENTIFIER, reference, reference),),
                       command=("provenance", reference), requested_output="provenance")
    if _is_reference(reference):
        missing = ("the item's identifier (provenance ID), or the answer's JSON file (provenance --answer "
                   "FILE); 'this' is not remembered (ADR 0044 P12-2)",)
        return _intent("SOURCE_QUERY", ("SOURCE_QUERY",), rule, status=InterpretationStatus.INCOMPLETE,
                       target=reference, entities=(Entity(EntityType.REFERENCE, reference, reference),),
                       missing=missing, requested_output="provenance")
    name = _name(reference)
    return _intent("SOURCE_QUERY", ("SOURCE_QUERY",), rule, status=InterpretationStatus.INCOMPLETE,
                   target=name, entities=(Entity(EntityType.CONCEPT, reference, name),),
                   missing=(f"the identifier of the stored item about '{name}'; find it with the route below",),
                   routes=(("query", "--name", name),), requested_output="provenance")


# ------------------------------------------------------------------------ the rules


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    priority: int
    pattern: re.Pattern
    handler: Callable[[re.Match], StructuredIntent | None]


def _r(name: str, priority: int, pattern: str, handler) -> Rule:
    return Rule(name, priority, re.compile(pattern, re.IGNORECASE), handler)


RULES: tuple[Rule, ...] = (
    # --- specific (3)
    _r("source.where_did_you_get", 3, r"^where did you get (?P<ref>.+?)(?: from)?$",
       lambda m: _source("source.where_did_you_get", m["ref"])),
    _r("source.come_from", 3, r"^where (?:does|do|did) (?P<ref>.+?) come from$",
       lambda m: _source("source.come_from", m["ref"])),
    _r("source.source_of", 3, r"^(?:what is|what's|what are|show(?: me)?) the (?:source|sources|provenance) (?:of|for) (?P<ref>.+)$",
       lambda m: _source("source.source_of", m["ref"])),
    _r("query.look_up", 3, r"^(?:look up|lookup|show(?: me)?) (?P<id>[A-Za-z]{1,4}-[0-9]+)$",
       lambda m: _query("query.look_up", m["id"], None)),
    _r("query.everything", 3, r"^(?:find|show(?: me)?|list|get|give me) (?:everything|all(?: the)? knowledge|all) (?:about|on|for) (?P<x>.+)$",
       lambda m: _query("query.everything", m["x"], "everything")),
    _r("query.equations", 3, r"^(?:which|what) equations (?:are )?(?:associated with|for|of|about|related to|relate to|involve) (?P<x>.+)$",
       lambda m: _query("query.equations", m["x"], "equations")),
    _r("query.prerequisites", 3, r"^what are the prerequisites (?:of|for) (?P<x>.+)$",
       lambda m: _query("query.prerequisites", m["x"], "prerequisites")),
    _r("reason.requires", 3, r"^what (?:does|do) (?P<x>.+?) (?:require|depend on|need)$",
       lambda m: _reason("reason.requires", m["x"])),
    _r("reason.needed_for", 3, r"^what (?:is|are) (?:needed|required) (?:for|to (?:find|determine|calculate)) (?P<x>.+)$",
       lambda m: _reason("reason.needed_for", m["x"])),
    _r("query.keyword", 3, r"^search (?:(?:in )?my books |my documents |the library )?for (?P<term>.+)$",
       lambda m: _keyword(m["term"])),
    _r("web.search", 3, r"^(?:search|look) (?:on )?(?:the )?(?:web|internet|online) for (?P<q>.+)$",
       lambda m: _web(m["q"])),
    _r("image.request", 3, r"^(?:draw|generate|create|make|produce) (?:a |an |the )?(?:diagram|image|picture|drawing|schematic|block diagram)(?: of| for| showing)? (?P<x>.+)$",
       lambda m: _image(m["x"])),
    _r("procedure.create_project", 3, r"^(?:create|make|start) (?:a )?(?:new )?project(?: (?:called|named) (?P<name>.+?))?(?: in (?P<app>[^\"']+))?$",
       lambda m: _project(m["name"], m["app"])),
    # Part 5 section 251's wording (ADR 0055): "Use the MATLAB manual to create a project called amplifier."
    _r("procedure.create_project_from_manual", 3,
       r"^use (?:the )?(?P<app>[^\"']+?) manual to (?:create|make|start) (?:a )?(?:new )?project(?: (?:called|named) (?P<name>.+?))?$",
       lambda m: _project(m["name"], m["app"])),
    _r("file.create", 3, r"^(?:create|make) (?:a |an |the )?(?:new )?(?:empty )?(?:text )?file(?: (?:called|named))? (?P<name>.+)$",
       lambda m: _file_action("file.create", "CREATE_FILE", path=m["name"])),
    _r("file.create_folder", 3, r"^(?:create|make) (?:a |an |the )?(?:new )?(?:folder|directory)(?: (?:called|named))? (?P<name>.+)$",
       lambda m: _file_action("file.create_folder", "CREATE_FOLDER", path=m["name"])),
    _r("file.open", 3, r"^open (?:the )?(?:file|document) (?P<path>.+)$",
       lambda m: _file_action("file.open", "OPEN_FILE", path=m["path"])),
    _r("file.open_path", 3, r"^open (?P<path>\S*[\\/]\S*|\S+\.[A-Za-z0-9]{1,5})$",
       lambda m: _file_action("file.open_path", "OPEN_FILE", path=m["path"])),
    _r("reference.unresolved", 3,
       r"^(?P<verb>open|close|delete|remove|show|save|use|run|start|launch|copy|move|rename) (?P<ref>it|this|that|them|(?:the|this|that|my) (?:project|file|document|folder|application|app|program|window))$",
       lambda m: _reference("reference.unresolved", m["verb"], m["ref"])),
    _r("calculate.what_is_given", 3, r"^what (?:is|are) (?P<target>.+?),? (?:given|if|when|where|with) (?P<given>.*=.*)$",
       lambda m: _calculation("calculate.what_is_given", m["target"], m["given"])),
    # --- verb commands (2)
    _r("calculate.verb", 2, r"^(?:calculate|compute|evaluate|determine|work out|find) (?:the value of )?(?P<rest>.+)$",
       lambda m: _calculate_verb(m["rest"])),
    _r("application.open", 2, r"^(?:open|launch|start|run) (?P<app>.+)$",
       lambda m: _application("application.open", "OPEN_APPLICATION", m["app"])),
    _r("application.close", 2, r"^(?:close|quit|exit|stop) (?P<app>.+)$",
       lambda m: _application("application.close", "CLOSE_APPLICATION", m["app"])),
    _r("file.copy", 2, r"^copy (?:the )?(?:file |folder )?(?P<a>.+?) (?:to|into) (?P<b>.+)$",
       lambda m: _file_action("file.copy", "COPY_FILE", source=m["a"], destination=m["b"])),
    _r("file.move", 2, r"^move (?:the )?(?:file |folder )?(?P<a>.+?) (?:to|into) (?P<b>.+)$",
       lambda m: _file_action("file.move", "MOVE_FILE", source=m["a"], destination=m["b"])),
    _r("file.rename", 2, r"^rename (?:the )?(?:file |folder )?(?P<a>.+?) (?:to|as) (?P<b>.+)$",
       lambda m: _file_action("file.rename", "RENAME_FILE", source=m["a"], new_name=m["b"])),
    _r("file.delete", 2, r"^(?:delete|remove|erase) (?:the )?(?:file |folder )?(?P<a>.+)$",
       lambda m: _file_action("file.delete", "DELETE_FILE", path=m["a"])),
    _r("file.save", 2, r"^save(?: the (?:file|document))?(?: as (?P<name>.+))?$",
       lambda m: _save(m["name"])),
    _r("keyboard.type", 2, r"^type (?P<text>.+)$", lambda m: _type(m["text"])),
    _r("keyboard.press", 2, r"^press (?P<key>.+)$", lambda m: _press(m["key"])),
    _r("keyboard.paste", 2, r"^paste(?: (?:it|the clipboard))?$", lambda m: _simple("keyboard.paste", "PASTE")),
    _r("screen.screenshot", 2, r"^(?:take|capture|grab) (?:a )?screen ?shot(?: of (?P<what>.+))?$",
       lambda m: _screenshot(m["what"])),
    # --- questions about a concept, in the ways people ask them (grammar version 2)
    _r("query.explain_principle", 3,
       r"^(?:explain|describe)(?: to me)?(?: the)? (?:operating principle|working principle|principle of operation|"
       r"principle|working|operation|functioning|concept|basics|theory|mechanism|meaning)(?: of| behind)? (?P<x>.+)$",
       lambda m: _concept_question("query.explain_principle", m["x"], "explanation", "EXPLANATION")),
    _r("query.how_works", 3, r"^how (?:does|do|is|are) (?P<x>.+?) (?:work|operate|function|used)$",
       lambda m: _concept_question("query.how_works", m["x"], "explanation", "EXPLANATION")),
    _r("query.properties", 3,
       r"^(?:what|which) (?:are )?(?:the )?(?:properties|characteristics|features) (?:of|does|do) (?P<x>.+?)"
       r"(?: have)?(?: (?:are|is|do|does)(?: \w+)*?)?(?: (?:stated|given|listed|found|mentioned|there))?$",
       lambda m: _concept_question("query.properties", m["x"], "properties")),
    _r("query.compare_definitions", 3,
       r"^compare (?:the )?(?:(?:two|different|all|various|both) )?definitions(?: (?:of|for) (?P<x>.+?))?"
       r"(?: (?:found|given|stated) in (?:my|the) (?:books|documents|notes))?$",
       lambda m: _concept_question("query.compare_definitions", m["x"] or "", "definitions_all")),
    _r("query.equations_given", 3,
       r"^(?:which|what) (?:equations|formulas|formulae|relations) (?:are )?(?:given|provided|stated|listed|"
       r"available|there|found) (?:for|about|on|in|of|with) (?P<x>.+)$",
       lambda m: _concept_question("query.equations_given", m["x"], "equations")),
    _r("query.equations_short", 3, r"^(?:the )?(?:equations|formulas|formulae) (?:for|of|about) (?P<x>.+)$",
       lambda m: _concept_question("query.equations_short", m["x"], "equations")),
    _r("query.variables", 3,
       r"^(?:what|which) (?:variables|quantities|symbols) (?:are )?(?:associated with|used in|in|of|appear in|"
       r"involved in|related to|belong to) (?P<x>.+)$",
       lambda m: _concept_question("query.variables", m["x"], "variables")),
    _r("query.where_stated", 3,
       r"^where (?:is|are|was|were) (?P<x>.+?) (?:stated|defined|mentioned|described|given|explained|found|discussed)"
       r"(?:\s+(?:in|from) (?:my|the) (?:documents|books|notes|files|sources))?$",
       lambda m: _concept_question("query.where_stated", m["x"], "locations")),
    _r("query.summarize", 3,
       r"^(?:summari[sz]e|give (?:me )?(?:a )?summary of|give (?:me )?an overview of|(?:an )?overview of) "
       r"(?:what (?:my|the) (?:documents|books) say about )?(?P<x>.+)$",
       lambda m: _concept_question("query.summarize", m["x"], "summary")),
    # --- generic (1)
    _r("query.what_is", 1, r"^(?:what is|what's|what are|define|explain|describe|tell me about) (?P<x>.+)$",
       lambda m: _what_is(m)),
)


def _reason(rule: str, text: str) -> StructuredIntent:
    text, scope = _scope(text)
    name = _name(text)
    command = ("reason", name) + (("--scope", scope.lower().replace("_", "-")) if scope else ())
    return _intent("REASON", (KNOWLEDGE_REASONING,), rule, target=name, source_scope=scope,
                   entities=(Entity(EntityType.CONCEPT, text, name),), command=command,
                   requested_output="dependencies")


def _keyword(term: str) -> StructuredIntent:
    term = _unquote(term)
    return _intent("KEYWORD_SEARCH", ("DOCUMENT_QUERY",), "query.keyword", target=term,
                   entities=(Entity(EntityType.SEARCH_TERM, term, term),), command=("query", "--keyword", term),
                   notes=("Keyword search reads the derived index; build it first with `python -m app index`.",))


def _web(query: str) -> StructuredIntent:
    return _intent("WEB_SEARCH", ("WEB_SEARCH",), "web.search", target=query,
                   entities=(Entity(EntityType.SEARCH_TERM, query, query),), parameters=(("query", query),),
                   action=ActionRequest("SEARCH_WEB", (("query", query),)),
                   notes=("Internet access needs the user's authorisation and a source scope "
                          "(sections 124-125); it is Phase 18.",))


def _image(subject: str) -> StructuredIntent:
    name = _name(subject)
    return _intent("IMAGE_REQUEST", ("IMAGE_REQUEST",), "image.request", target=name,
                   entities=(Entity(EntityType.CONCEPT, subject, name),), parameters=(("subject", name),),
                   notes=("A diagram is drawn from structured stored knowledge, never invented (Phase 19).",))


def _project(name: str | None, application: str | None) -> StructuredIntent:
    parameters = []
    entities = []
    if application:
        app_name, _ = resolve_application(application)
        parameters.append(("application", app_name or application.strip()))
        entities.append(Entity(EntityType.APPLICATION, application, app_name or application.strip(),
                               known=app_name is not None))
    if not name:
        return _intent("CREATE_PROJECT", ("PROCEDURE_EXECUTION",), "procedure.create_project",
                       status=InterpretationStatus.INCOMPLETE, entities=tuple(entities),
                       parameters=tuple(parameters), missing=("the project's name",))
    name = _unquote(name)
    entities.insert(0, Entity(EntityType.PROJECT, name, name))
    parameters.insert(0, ("name", name))
    return _intent("CREATE_PROJECT", ("PROCEDURE_EXECUTION",), "procedure.create_project", target=name,
                   entities=tuple(entities), parameters=tuple(parameters),
                   action=ActionRequest("CREATE_PROJECT", tuple(parameters)),
                   notes=("Carried out only by a documented procedure (Phases 16-17); a guessed workflow "
                          "is never presented as documented (section 111).",))


def _calculate_verb(rest: str) -> StructuredIntent | None:
    if re.match(r"^(?:everything|all)\b", rest, re.IGNORECASE):
        return None
    split = re.match(r"^(?P<target>.+?)\s*,?\s+(?:given|where|with|if|using|when)\s+(?P<given>.*=.*)$", rest, re.IGNORECASE)
    if split:
        return _calculation("calculate.verb", split["target"], split["given"])
    return _calculation("calculate.verb", rest, None)


def _concept_question(rule: str, target: str, output: str, cls: str = "KNOWLEDGE_QUERY") -> StructuredIntent:
    """A question about one concept. "this", "that section" and the like name nothing RUDRA can
    look up (it keeps no conversation), so they are INCOMPLETE rather than guessed."""
    target = re.sub(r"\s+(?:are|is|do|does)(?:\s+(?:in|from)\s+(?:my|the)\s+\w+)?$", "", target.strip(),
                    flags=re.IGNORECASE)  # "properties of MOSFETs are in my documents"
    text, _scope_found = _scope(target)
    if not text or _is_reference(text) or re.fullmatch(
            r"(?:this|that|these|those) \w+|the (?:section|chapter|topic|page|paragraph|passage|part|document|book|"
            r"text|above|following|previous|same|figure|table|example|subject|one)", text, re.I):
        return _intent("QUERY_CONCEPT", (cls,), rule, target=None, requested_output=output,
                       status=InterpretationStatus.INCOMPLETE,
                       missing=("the concept or topic to look up, by name (for example: " +
                                {"definitions_all": "Compare the definitions of resistance",
                                 "summary": "Summarize capacitance"}.get(output, "Explain resistance") + ")",))
    return _query(rule, target, output, cls)


def _what_is(match: re.Match) -> StructuredIntent:
    text = match["x"].strip()
    if _is_arithmetic(text):
        return _calculation("query.what_is", text, None)
    output = "explanation" if match[0].lower().startswith(("explain", "describe")) else "definition"
    cls = "EXPLANATION" if output == "explanation" else "KNOWLEDGE_QUERY"
    if _is_reference(_scope(text)[0]):  # "Explain this": nothing to look up
        return _concept_question("query.what_is", text, output, cls)
    return _query("query.what_is", text, output, cls)


def _save(name: str | None) -> StructuredIntent:
    parameters = () if not name else (("name", _unquote(name)),)
    return _intent("SAVE_FILE", ("FILE_OPERATION",), "file.save", target=None if not name else _unquote(name),
                   parameters=parameters, action=ActionRequest("SAVE_FILE", parameters))


def _type(text: str) -> StructuredIntent:
    text = _unquote(text)
    return _intent("TYPE_TEXT", ("APPLICATION_CONTROL",), "keyboard.type", target=text,
                   entities=(Entity(EntityType.TEXT, text, text),), parameters=(("text", text),),
                   action=ActionRequest("TYPE_TEXT", (("text", text),)))


def _press(key: str) -> StructuredIntent:
    key = _unquote(key)
    kind = "HOTKEY" if "+" in key and key.strip() != "+" else "PRESS_KEY"
    keys = tuple(k.strip() for k in key.split("+")) if kind == "HOTKEY" else (key.strip(),)
    parameters = (("keys", "+".join(keys)),)
    return _intent(kind, ("APPLICATION_CONTROL",), "keyboard.press", target="+".join(keys),
                   entities=tuple(Entity(EntityType.KEY, k, k) for k in keys), parameters=parameters,
                   action=ActionRequest(kind, parameters))


def _screenshot(what: str | None) -> StructuredIntent:
    parameters = () if not what else (("of", what.strip()),)
    return _intent("TAKE_SCREENSHOT", ("SYSTEM_QUERY",), "screen.screenshot", target=None if not what else what.strip(),
                   parameters=parameters, action=ActionRequest("TAKE_SCREENSHOT", parameters))


def _simple(rule: str, intent_type: str) -> StructuredIntent:
    return _intent(intent_type, ("APPLICATION_CONTROL",), rule, action=ActionRequest(intent_type, ()))


# ------------------------------------------------------------------ interpretation


def _clause(text: str) -> StructuredIntent | None:
    """The one intent a clause means, an AMBIGUOUS one listing the candidates, or None."""
    matches: list[tuple[int, StructuredIntent]] = []
    for rule in RULES:
        match = rule.pattern.match(text)
        if match is None:
            continue
        intent = rule.handler(match)
        if intent is not None:
            matches.append((rule.priority, intent))
    if not matches:
        return None
    top = max(priority for priority, _ in matches)
    found = [intent for priority, intent in matches if priority == top]
    types = list(dict.fromkeys(intent.intent_type for intent in found))
    if len(types) == 1:
        return found[0]
    return StructuredIntent(
        intent_id="", intent_type="AMBIGUOUS_REQUEST", task_classes=tuple(dict.fromkeys(
            c for intent in found for c in intent.task_classes)),
        status=InterpretationStatus.AMBIGUOUS, rule="+".join(i.rule for i in found), target=text,
        candidates=tuple(types), missing=("which of these is meant: " + ", ".join(types),),
    )


def _numbered(intents: list[StructuredIntent]) -> tuple[StructuredIntent, ...]:
    return tuple(replace(intent, intent_id=f"intent-{n}") for n, intent in enumerate(intents, start=1))


def interpret(text: str) -> Interpretation:
    """Interpret one request (module docstring). Raises `InvalidInputError` for empty or
    over-long text; otherwise always answers. Deterministic; writes and runs nothing."""
    if not isinstance(text, str) or not text.strip():
        raise refuse("There is no request to interpret.", "Give the request as text, e.g. \"Open Chrome.\"")
    if len(text) > MAX_TEXT:
        raise refuse(f"The request is {len(text)} characters long.", f"The limit is {MAX_TEXT} characters.")
    whole = clean(text)
    parts = [clean(p) for p in _SPLIT.split(whole) if clean(p)]
    intents: list[StructuredIntent] | None = None
    if len(parts) > 1:
        found = [_clause(part) for part in parts]
        if all(found):
            intents = found  # type: ignore[assignment]
    if intents is None:
        single = _clause(whole)
        intents = [] if single is None else [single]
    numbered = _numbered(intents)
    return Interpretation(
        text=text,
        status=_status(numbered),
        message=_message(numbered),
        intents=numbered,
        grammar=GRAMMAR_NAME,
        grammar_version=GRAMMAR_VERSION,
        examples=() if numbered else EXAMPLES,
        notes=("The interpretation is data: nothing was run, read or written (ADR 0045 P13-2).",),
    )


def _status(intents: tuple[StructuredIntent, ...]) -> InterpretationStatus:
    if not intents:
        return InterpretationStatus.UNRECOGNIZED
    statuses = {intent.status for intent in intents}
    for status in (InterpretationStatus.AMBIGUOUS, InterpretationStatus.INCOMPLETE):
        if status in statuses:
            return status
    return InterpretationStatus.INTERPRETED


def _message(intents: tuple[StructuredIntent, ...]) -> str:
    if not intents:
        return ("Not recognised: RUDRA's request grammar has no rule for this text, and nothing was "
                "guessed. See the examples.")
    described = "; ".join(
        f"{i.intent_type}" + (f" ({i.target})" if i.target else "") + f" - {i.status.value}"
        + (f": needs {'; '.join(i.missing)}" if i.missing else "")
        for i in intents
    )
    return f"{len(intents)} request(s): {described}."
