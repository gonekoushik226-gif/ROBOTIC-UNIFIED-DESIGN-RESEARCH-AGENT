"""How the window presents an answer: the answer first, its sources on request.

`ask --json` returns every part of an answer with its provenance: the identifiers it
rests on, the documents and pages, the command that answered it, conflicting claims.
The window shows the useful answer by itself - the statements, a calculation's steps,
what is missing - and keeps everything about *where it came from* behind a
**View Sources** button on each part. Opening it shows that information in full, plus
the `provenance` command's own trace of each knowledge item it rests on (document,
file, SHA-256 check, page and quote).

This is presentation only. The answer the command line returns, the database and the
provenance links are exactly what they were; nothing here removes or rewrites them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

#: Database identifiers: K-00000001, CPT-00000001, DOC-00000001, REL-..., and so on.
IDENTIFIER = re.compile(r"\b[A-Z]{1,5}-\d{8}\b")
_BRACKETED_ID = re.compile(r"\s*\((?:[A-Z]{1,5}-\d{8})(?:,\s*[A-Z]{1,5}-\d{8})*\)")
#: At most this many knowledge items are traced when the sources are opened.
MAX_TRACED = 3

_NEXT_STEPS = {
    "extract FILE.pdf": "Import a document that covers it (Import page).",
    "research": "Authorize an Internet search for one website (Command page: research \"QUESTION\" --site URL).",
}


@dataclass(frozen=True)
class SourceDetails:
    """Everything about where one part's answer came from, shown only on request."""

    command: tuple[str, ...] = ()
    status: str = ""
    basis: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    why: tuple[str, ...] = ()
    available: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.basis or self.sources or self.conflicts or self.why or self.available or self.unavailable)

    def knowledge_ids(self) -> tuple[str, ...]:
        """The knowledge items to trace with `provenance`, in the order the answer used them."""
        found = [identifier for identifier in self.basis if identifier.startswith("K-")]
        return tuple(dict.fromkeys(found))[:MAX_TRACED]


@dataclass(frozen=True)
class AnswerPart:
    number: int
    status: str
    #: The answer's own lines (definitions, equations, a result, or why it cannot answer).
    lines: tuple[str, ...]
    #: Useful context that is not provenance: steps, assumptions, what is missing, next steps.
    extras: tuple[tuple[str, tuple[str, ...]], ...]
    has_conflict: bool
    details: SourceDetails
    #: Answer lines resting on uncertain recognition, with the reason (shown with the answer:
    #: uncertainty is part of what the answer says, not provenance detail).
    uncertain: tuple[str, ...] = ()
    intent: str = ""

    def statements(self) -> tuple[str, ...]:
        """The retrieved statements themselves ("Definition: X is ..." -> "X is ...")."""
        return tuple(re.sub(r"^[A-Z][A-Za-z ]{1,30}?: ", "", line) for line in self.lines)


@dataclass(frozen=True)
class AnswerDocument:
    request: str
    parts: tuple[AnswerPart, ...] = field(default_factory=tuple)


def clean(text: str) -> str:
    """A line for the plain answer: no bracketed database identifiers, no doubled spaces."""
    return " ".join(_BRACKETED_ID.sub("", str(text)).split())


def _tuple(value) -> tuple[str, ...]:
    return tuple(str(item) for item in value or ())


def _next_step(step: str) -> str:
    for key, friendly in _NEXT_STEPS.items():
        if key in step:
            return friendly
    return step


def _part(raw: dict) -> AnswerPart:
    details = SourceDetails(
        command=_tuple(raw.get("command")),
        status=str(raw.get("status", "")),
        basis=_tuple(raw.get("basis")),
        sources=_tuple(raw.get("sources")),
        conflicts=_tuple(raw.get("conflicts")),
        why=_tuple(raw.get("why")),
        available=_tuple(raw.get("available")),
        unavailable=_tuple(raw.get("unavailable")),
    )
    lines = tuple(clean(line) for line in str(raw.get("answer") or "").split("\n") if line.strip())
    extras = []
    for title, key in (("Steps", "calculation"), ("Reasoning", "reasoning"), ("Actions", "actions"),
                       ("Assumptions", "assumptions"), ("Missing information", "missing")):
        values = tuple(clean(value) for value in raw.get(key) or () if clean(value))
        if values:
            extras.append((title, values))
    steps = tuple(dict.fromkeys(_next_step(str(step)) for step in raw.get("next_steps") or ()))
    if steps:
        extras.append(("Next steps", steps))
    uncertain = tuple(clean(value) for value in raw.get("uncertain") or () if clean(value))
    return AnswerPart(int(raw.get("number", 0)), details.status, lines, tuple(extras), bool(details.conflicts),
                      details, uncertain, str(raw.get("intent", "")))


def parse_answer(stdout: str) -> AnswerDocument | None:
    """The answer `ask --json` printed, or None when the output is not such an answer."""
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("parts"), list):
        return None
    request = data.get("request")
    if isinstance(request, dict):
        request = request.get("text", "")
    if not request:
        request = (data.get("interpretation") or {}).get("text", "")
    return AnswerDocument(str(request or ""), tuple(_part(part) for part in data["parts"] if isinstance(part, dict)))


def detail_lines(details: SourceDetails, display_command) -> list[tuple[str, str]]:
    """The source view's lines as (label, text), in the command line's own vocabulary."""
    rows: list[tuple[str, str]] = []
    for label, values in (("Sources", details.sources), ("Based on", details.basis),
                          ("Conflict", details.conflicts), ("Why required", details.why),
                          ("Available", details.available), ("Unavailable", details.unavailable)):
        for index, value in enumerate(values):
            rows.append((label if index == 0 else "", value))
    if details.command:
        rows.append(("Answered by", display_command(details.command)))
    if details.status:
        rows.append(("Status", details.status))
    return rows


_STORED = re.compile(r"Document\s*:\s*(?P<id>[A-Z]{1,5}-\d{8})\s+(?P<name>[0-9A-Fa-f]{64}\.[A-Za-z]{2,5});")
_PAGE = re.compile(r"(?P<id>[A-Z]{1,5}-\d{8}) p\.(?P<page>\d+)")


def source_files(trace: str) -> list[tuple[str, str, int | None]]:
    """(document id, stored file name, first cited page) for each document a provenance trace names."""
    found = []
    for match in _STORED.finditer(trace):
        page = next((int(m.group("page")) for m in _PAGE.finditer(trace) if m.group("id") == match.group("id")),
                    None)
        found.append((match.group("id"), match.group("name"), page))
    return found


def plain_text(document: AnswerDocument) -> str:
    """The default view as text (the answer only): for copying and for checks."""
    out: list[str] = []
    for part in document.parts:
        out.extend(part.lines)
        if part.has_conflict:
            out.append("Sources disagree on this. View Sources shows each claim.")
        out.extend(f"Uncertain: {line}" for line in part.uncertain)
        for title, values in part.extras:
            out.append(f"{title}:")
            out.extend(f"  {value}" for value in values)
    return "\n".join(out)
