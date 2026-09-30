"""Rule R1: a definition that names another concept of the same run (ADR 0028).

    If the stored DEFINITION of concept X, extracted in run R, contains a whole-word
    mention of the name of concept Y of run R, record one INFERRED RELATED_TO edge
    between X and Y, with a basis.

**R1 is a RUDRA design decision adopted to satisfy Part 5 sections 192-193 while
minimising unsupported inference.** The specification requires an inferred
relationship to be distinguishable from a stated one; it names no inference rule and
does not mandate this one.

For an R1 edge, `RELATED_TO` means exactly the sentence above and nothing more: no
equivalence, opposition, dependency, prerequisite, hierarchy, application or
cross-reference. It is not a source statement - the source defined X; RUDRA
observed that the definition names Y.

This module is the rule and nothing else: pure functions over values already read
from storage. It opens no connection, reads no page text and writes nothing, so it
can be tested on its own (Part 1 section 24) and its output depends only on its
input (ADR 0028, "Deterministic").

The matching procedure, step by step (ADR 0028, with the implementation decisions
of 2026-09-23 in `docs/phases/PHASE_6.md` section 4.2):

1. The definition's stored evidence text is normalised by decision D-30's rule
   only - NFKC, `casefold`, whitespace collapse (`normalize_alias`).
2. The name set - the **stored** `normalized_alias` of the run's concepts' ACTIVE
   aliases, which by ADR 0010 S-1 include every canonical name (I6-E) - is scanned
   **longest name first**, ties broken by the normalised name; the concepts sharing
   one normalised name are held together, in identifier order.
3. A match counts only as a **whole word**: no letter or digit immediately before
   or after it (I6-K: a hyphen is therefore a boundary). Plural, inflected or
   abbreviated forms do not match.
4. Matches **may not overlap**: a shorter name inside a longer matched name
   ("resistance" inside "equivalent resistance") is consumed by the longer one.
   Every match consumes its text, including the two kinds skipped next (I6-C).
5. A match of one of X's own names is skipped.
6. A match of a name held by more than one concept of the run is ambiguous and is
   skipped rather than guessed.
7. Every other match of a concept Y gives the unordered pair {X, Y}, and records
   the **raw source text** it matched (I6-B): the match's position in the normalised
   text is mapped back to the definition's stored text, and the mapping is proved,
   never assumed. A match whose source text cannot be proved is skipped and
   counted, not guessed.
"""

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.models.naming import normalize_alias

#: The rule's name, recorded on every basis row (ADR 0027).
RULE = "R1"

#: The rule's version, recorded on every basis row. Bump it whenever the matching
#: procedure changes, so an edge a later version would not make can still be found.
#: A later version never retracts an earlier version's edges (ADR 0028).
RULE_VERSION = "1"


@dataclass(frozen=True, slots=True)
class NameEntry:
    """One normalised name and every concept of the run that answers to it."""

    name: str
    #: Identifier order. More than one means the name is ambiguous in this run.
    concept_ids: tuple[str, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.concept_ids) > 1


@dataclass(frozen=True, slots=True)
class DefinitionText:
    """The stored evidence of one definition of concept X, as R1 reads it."""

    concept_id: str
    knowledge_id: str
    occurrence_id: str
    #: The source occurrence's `original_text`, never the page it came from.
    text: str


@dataclass(frozen=True, slots=True)
class Mention:
    """X's definition names concept Y. The direction lives here, not on the edge."""

    mentioning_concept_id: str
    mentioned_concept_id: str
    basis_occurrence_id: str
    #: The name exactly as the source printed it: `text[raw_start:raw_end]` of the
    #: definition's stored `original_text` (I6-B).
    matched_text: str
    #: The normalised name that matched.
    name: str
    #: Where the match lies in the normalised text.
    start: int
    end: int
    #: Where the same match lies in the definition's stored text.
    raw_start: int
    raw_end: int

    @property
    def pair(self) -> tuple[str, str]:
        return canonical_pair(self.mentioning_concept_id, self.mentioned_concept_id)


@dataclass(frozen=True, slots=True)
class DefinitionScan:
    """Everything R1 found in one definition, including what it skipped."""

    definition: DefinitionText
    mentions: tuple[Mention, ...] = ()
    #: Matches of X's own names - consumed, never paired.
    own_name_matches: int = 0
    #: Matches of a name held by several concepts - consumed, never paired.
    ambiguous_matches: tuple[str, ...] = ()
    #: Matches whose source text could not be proved - consumed, never paired.
    unmappable_matches: tuple[str, ...] = ()


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    """The one storage order of an unordered pair (decision P6-4b, ADR 0028).

    Plain string comparison of the two identifiers, used **only** to pick one of two
    storage forms. It gives the order no meaning and infers nothing about age or
    time (ADR 0006's rules).
    """
    return (a, b) if a < b else (b, a)


def build_name_set(names_by_concept: Mapping[str, Iterable[str]]) -> tuple[NameEntry, ...]:
    """The run's name set, in scan order: longest first, then by normalised name.

    `names_by_concept` maps each concept identifier to its **already normalised**
    names - the stored `normalized_alias` values (I6-E). They are used as stored;
    nothing here normalises them again.
    """
    owners: dict[str, set[str]] = {}
    for concept_id, names in names_by_concept.items():
        for name in names:
            if name:
                owners.setdefault(name, set()).add(concept_id)
    entries = (
        NameEntry(name=name, concept_ids=tuple(sorted(ids))) for name, ids in owners.items()
    )
    return tuple(sorted(entries, key=lambda e: (-len(e.name), e.name)))


def scan_definition(
    definition: DefinitionText, name_set: tuple[NameEntry, ...]
) -> DefinitionScan:
    """Apply R1's matching procedure to one definition. Pure and deterministic."""
    text = _normalise(definition.text)
    if not text:
        return DefinitionScan(definition=definition)
    positions = normalise_with_positions(definition.text)

    consumed: list[tuple[int, int]] = []
    mentions: list[Mention] = []
    own = 0
    ambiguous: list[str] = []
    unmappable: list[str] = []
    for entry in name_set:
        for start in _whole_word_positions(text, entry.name):
            end = start + len(entry.name)
            if any(start < taken_end and taken_start < end for taken_start, taken_end in consumed):
                continue
            consumed.append((start, end))
            if entry.ambiguous:
                ambiguous.append(entry.name)
                continue
            if entry.concept_ids == (definition.concept_id,):
                own += 1
                continue
            raw = _raw_span(definition.text, positions, start, end, entry.name)
            if raw is None:
                unmappable.append(entry.name)
                continue
            mentions.append(
                Mention(
                    mentioning_concept_id=definition.concept_id,
                    mentioned_concept_id=entry.concept_ids[0],
                    basis_occurrence_id=definition.occurrence_id,
                    matched_text=definition.text[raw[0]:raw[1]],
                    name=entry.name,
                    start=start,
                    end=end,
                    raw_start=raw[0],
                    raw_end=raw[1],
                )
            )
    mentions.sort(key=lambda m: m.start)
    return DefinitionScan(
        definition=definition,
        mentions=tuple(mentions),
        own_name_matches=own,
        ambiguous_matches=tuple(ambiguous),
        unmappable_matches=tuple(unmappable),
    )


def normalise_with_positions(raw: str) -> tuple[str, tuple[tuple[int, int], ...]] | None:
    """Decision D-30's normalisation of `raw`, with each output character's raw span.

    The text is normalised in clusters - a character together with every following
    character that NFKC would combine with it (combining marks, and for example
    Hangul jamo) - so composition never crosses a cluster boundary, and every
    normalised character is traced to the cluster it came from. Whitespace is then
    collapsed and stripped exactly as `normalize_alias` does.

    The result is **proved, not assumed**: it is returned only when its text equals
    `normalize_alias(raw)`. Otherwise the answer is None, and no position is claimed.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    clusters: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(raw) + 1):
        if index == len(raw) or not _joins(raw[start:index], raw[index]):
            clusters.append((start, index))
            start = index

    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    for cluster in clusters:
        for char in unicodedata.normalize("NFKC", raw[cluster[0]:cluster[1]]).casefold():
            chars.append(char)
            spans.append(cluster)

    text: list[str] = []
    text_spans: list[tuple[int, int]] = []
    index = 0
    while index < len(chars):
        if not chars[index].isspace():
            text.append(chars[index])
            text_spans.append(spans[index])
            index += 1
            continue
        run_end = index
        while run_end < len(chars) and chars[run_end].isspace():
            run_end += 1
        # An interior run becomes one space; a leading or trailing one is stripped.
        if text and run_end < len(chars):
            text.append(" ")
            text_spans.append((spans[index][0], spans[run_end - 1][1]))
        index = run_end

    normalised = "".join(text)
    if normalised != _normalise(raw):
        return None
    return normalised, tuple(text_spans)


def _raw_span(
    raw: str,
    positions: tuple[str, tuple[tuple[int, int], ...]] | None,
    start: int,
    end: int,
    name: str,
) -> tuple[int, int] | None:
    """The stored-text span of a normalised match, or None when it cannot be proved.

    The span runs from the first matched character's cluster to the last one's. It
    is accepted only when that stretch of the source normalises to exactly the name
    that matched - so a cluster that normalises to more than the match (for example
    a vulgar fraction) is never passed off as the matched text.
    """
    if positions is None:
        return None
    spans = positions[1]
    raw_start, raw_end = spans[start][0], spans[end - 1][1]
    if _normalise(raw[raw_start:raw_end]) != name:
        return None
    return raw_start, raw_end


def _joins(cluster: str, following: str) -> bool:
    """Whether `following` must be normalised together with `cluster`."""
    if unicodedata.combining(following):
        return True
    together = unicodedata.normalize("NFKC", cluster + following)
    apart = unicodedata.normalize("NFKC", cluster) + unicodedata.normalize("NFKC", following)
    return together != apart


def _normalise(value: str) -> str:
    """Decision D-30's normalisation; a value that normalises to nothing is ''."""
    if not isinstance(value, str) or not value.strip():
        return ""
    return normalize_alias(value)


def _whole_word_positions(text: str, name: str) -> list[int]:
    """Start offsets of `name` in `text` with no letter or digit on either side."""
    found: list[int] = []
    start = text.find(name)
    while start != -1:
        end = start + len(name)
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if not (before and before.isalnum()) and not (after and after.isalnum()):
            found.append(start)
        start = text.find(name, start + 1)
    return found
