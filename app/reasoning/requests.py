"""The structured reasoning request (ADR 0040 P10-27; ADR 0038 P10-5, OI-1 = Option B).

Phase 10 parses no natural language (section 4; I-4): reasoning takes a typed request.
Two modes:

    TARGET   backward reasoning from one target (ADR 0039 P10-14)
    FORWARD  everything derivable from what the request makes available

What a request makes available **initially** (OI-1 = Option B): only nodes it supplies
as `USER_INPUT` or for which it explicitly admits one stored knowledge object; an
assumption (U7(b)) is stated separately and labelled. Stored knowledge is never
available merely because it exists in `knowledge.db`.

A node is named by a concept identifier (`CPT-...`) or an exact concept name (D-30;
ADR 0036 P9-11). A value supplied with an input is carried verbatim and never parsed,
evaluated, converted or combined (ADR 0038 P10-6).

`check` refuses what can be judged without the database - a missing or superfluous
field, a blank name, an identifier that is malformed or of the wrong kind. What needs
the database (a node that does not resolve, an item that does not exist) is judged by
`app.reasoning.inputs`. Every refusal is an `InvalidInputError`: an invalid request,
exit code 2 at the command line (ADR 0040 P10-28).
"""

from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import InvalidInputError
from app.models.identifiers import EntityKind, is_valid_id
from app.models.naming import normalize_alias


class ReasoningMode(StrEnum):
    TARGET = "TARGET"
    FORWARD = "FORWARD"


class ReasoningScope(StrEnum):
    """Which sources' evidence reasoning may use: ADR 0035 P9-5, applied unchanged.

    Evidence whose source is `NOT_AUTHORIZED`, or of category `UNAUTHORIZED_SOURCE`,
    is never used in any scope (sections 49-50).
    """

    #: Section 51's "USER_PROVIDED / AUTHORIZED LOCAL DOCUMENTS": category
    #: `USER_PROVIDED_SOURCE` or `LOCAL_SOURCE`, and `AUTHORIZED`.
    MY_BOOKS = "MY_BOOKS"
    #: Every `AUTHORIZED` source.
    AUTHORIZED = "AUTHORIZED"


_STAGE = "reasoning.request"


def refuse(summary: str, reason: str) -> InvalidInputError:
    """An invalid reasoning request (exit code 2); nothing was read or changed."""
    return InvalidInputError.of(
        summary,
        reason,
        stage=_STAGE,
        data_changed=False,
        retry_safe=True,
        next_options=(
            'ReasoningRequest.of_target("X", inputs=(UserInput("E"),))',
            'ReasoningRequest.of_target("X", admissions=(Admission("D", "K-00000001"),))',
            'ReasoningRequest.forward(inputs=(UserInput("E"),))',
        ),
    )


def check_node(node: object, what: str) -> None:
    """A node is a concept identifier or a non-blank exact concept name.

    Text in the form of any RUDRA identifier is read as an identifier, so it must be
    a concept's: `K-00000001` names a knowledge object, never a node.
    """
    if not isinstance(node, str):
        raise refuse(f"The {what} node is not text.", f"Got {node!r}.")
    if is_valid_id(node):
        if not is_valid_id(node, kind=EntityKind.CONCEPT):
            raise refuse(
                f"The {what} node {node!r} is an identifier of the wrong kind.",
                f"A node is a concept identifier ({EntityKind.CONCEPT.prefix}-...) or an "
                "exact concept name.",
            )
        return
    try:
        normalize_alias(node)
    except (TypeError, ValueError) as exc:
        raise refuse(
            f"The {what} node is empty.",
            "A node name needs at least one character that is not whitespace.",
        ) from exc


def _optional_text(value: object, what: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise refuse(f"The {what} is empty.", f"Give none, or non-blank text; got {value!r}.")


@dataclass(frozen=True, slots=True)
class UserInput:
    """A node the request supplies as given (`USER_INPUT`), with an optional value.

    The value is carried verbatim and never interpreted (ADR 0038 P10-6). A
    `USER_INPUT` never becomes sourced knowledge: nothing is written (P10-9).
    """

    node: str
    value: str | None = None

    def check(self) -> None:
        check_node(self.node, "input")
        _optional_text(self.value, "input value")


@dataclass(frozen=True, slots=True)
class Admission:
    """One stored knowledge object the request admits as a node's basis (OI-1 = B).

    A pair and nothing else (ADR 0040 P10-27): the node, and the identifier of one
    knowledge object (`K-...`). No stored link between the two is required, checked
    or inferred, and an admission adds or removes no requirement.
    """

    node: str
    knowledge_id: str

    def check(self) -> None:
        check_node(self.node, "admission")
        identifier = self.knowledge_id
        if not is_valid_id(identifier):
            raise refuse(
                f"The admitted item {identifier!r} is not an identifier.",
                f"An admission names one knowledge object, "
                f"{EntityKind.KNOWLEDGE_OBJECT.prefix}-00000001 in form.",
            )
        if not is_valid_id(identifier, kind=EntityKind.KNOWLEDGE_OBJECT):
            raise refuse(
                f"The admitted item {identifier!r} is an identifier of the wrong kind.",
                "Only knowledge objects can be admitted: concepts, "
                "relationships, documents and other identifiers cannot.",
            )


@dataclass(frozen=True, slots=True)
class Assumption:
    """A node reasoning proceeds as if it were available (U7(b); ADR 0039 P10-18).

    Labelled `ASSUMPTION`, with its effect in the result; never stored.
    """

    node: str
    statement: str | None = None

    def check(self) -> None:
        check_node(self.node, "assumption")
        _optional_text(self.statement, "assumption statement")


@dataclass(frozen=True, slots=True)
class ReasoningRequest:
    """One structured reasoning request. Build it with `of_target` or `forward`."""

    mode: ReasoningMode
    target: str | None = None
    inputs: tuple[UserInput, ...] = ()
    admissions: tuple[Admission, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    scope: ReasoningScope = ReasoningScope.MY_BOOKS

    @classmethod
    def of_target(cls, target: str, **options: object) -> "ReasoningRequest":
        return cls(mode=ReasoningMode.TARGET, target=target, **options)  # type: ignore[arg-type]

    @classmethod
    def forward(cls, **options: object) -> "ReasoningRequest":
        return cls(mode=ReasoningMode.FORWARD, **options)  # type: ignore[arg-type]

    def check(self) -> "ReasoningRequest":
        """Refuse a request that cannot be answered as asked. Returns it unchanged."""
        if not isinstance(self.mode, ReasoningMode):
            raise refuse("The reasoning mode is not one of TARGET or FORWARD.", f"Got {self.mode!r}.")
        if not isinstance(self.scope, ReasoningScope):
            raise refuse("The source scope is not one of MY_BOOKS or AUTHORIZED.", f"Got {self.scope!r}.")
        for name, kind in (
            ("inputs", UserInput), ("admissions", Admission), ("assumptions", Assumption)
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not all(isinstance(v, kind) for v in values):
                raise refuse(f"The {name} are not a tuple of {kind.__name__} values.", f"Got {values!r}.")
            for value in values:
                value.check()
        if self.mode is ReasoningMode.TARGET:
            if self.target is None:
                raise refuse("A TARGET request names its target.", "No target was given.")
            check_node(self.target, "target")
        else:
            if self.target is not None:
                raise refuse(
                    "A FORWARD request takes no target.",
                    f"It was given the target {self.target!r}; use TARGET mode to reason towards one.",
                )
            if not (self.inputs or self.admissions or self.assumptions):
                raise refuse(
                    "A FORWARD request has nothing to reason from.",
                    "Supply at least one input, admission or assumption.",
                )
        return self
