"""What stage 15 and `merge` compare against: the ACTIVE knowledge, in memory.

One read of the stored knowledge - statements, located occurrences, the concepts
each object is attached to and those concepts' stored names - after which every
comparison is a dictionary lookup rather than a query. Stage 15 keeps the index
current as the run writes, so an item is compared with everything stored before it,
including what the same run stored a page earlier (ADR 0033, P8-11).

**The exact-duplicate relation** (ADR 0033, P8-12), between two knowledge objects or
an object and an item about to be written:

* the same knowledge type and the same normalised statement; and
* the same document, page and character span - any type - or, at another location:
  a `DEFINITION` whose defining concepts share an ACTIVE normalised alias (concept
  identity), never an `EQUATION` (section 77), any other type always.

**Exact-duplicate groups** are the connected components of that relation among
stored objects with one type and one normalised statement; the member with the
smallest numeric identifier counter is the group's **canonical member** (P8-19).
Non-exact comparisons use canonical members only (P8-13), so no assessment is ever
recorded against an object `merge` would supersede.

**Concept identity** is used only as a necessary condition for linking or
comparing; it never merges concepts (sections 44 and 73).
"""

from dataclasses import dataclass

from app.deduplication.rules import by_counter, counter, normalized_statement
from app.models.enums import KnowledgeType, RelationType
from app.storage import queries

#: (document id, page number, char start, char end) - a stored location (ADR 0019).
Location = tuple[str, int, int, int]

#: The only route from a knowledge object to its concept, per type (D-21). Other
#: types have no concept (ADR 0033, "Concept identity").
CONCEPT_RELATION: dict[KnowledgeType, RelationType] = {
    KnowledgeType.DEFINITION: RelationType.DEFINED_BY,
    KnowledgeType.PROPERTY: RelationType.HAS_PROPERTY,
}


@dataclass(frozen=True, slots=True)
class Item:
    """A piece of knowledge about to be written, before any object exists for it."""

    knowledge_type: KnowledgeType
    #: The statement as it would be stored (whitespace collapsed, stage 13).
    statement: str
    location: Location
    #: The defining concept of a DEFINITION, or the resolved owner of a PROPERTY.
    concept_id: str | None = None


class KnowledgeIndex:
    """The ACTIVE knowledge objects, their locations, concepts and concept names."""

    __slots__ = ("_type", "_normalized", "_locations", "_concepts", "_aliases",
                 "_by_statement", "_by_name")

    def __init__(self) -> None:
        self._type: dict[str, KnowledgeType] = {}
        self._normalized: dict[str, str] = {}
        self._locations: dict[str, set[Location]] = {}
        self._concepts: dict[str, set[str]] = {}
        self._aliases: dict[str, frozenset[str]] = {}
        self._by_statement: dict[tuple[KnowledgeType, str], set[str]] = {}
        self._by_name: dict[tuple[KnowledgeType, str], set[str]] = {}

    @classmethod
    def load(cls, connection) -> "KnowledgeIndex":
        """Read every ACTIVE knowledge object once, through `app.storage`."""
        index = cls()
        aliases: dict[str, set[str]] = {}
        for concept_id, alias in queries.active_concept_aliases(connection):
            aliases.setdefault(concept_id, set()).add(alias)
        for concept_id, names in aliases.items():
            index._aliases[concept_id] = frozenset(names)
        for knowledge_id, knowledge_type, statement in queries.active_knowledge_statements(connection):
            index.add(knowledge_id, KnowledgeType(knowledge_type), statement)
        for knowledge_id, document_id, page, start, end in queries.active_knowledge_locations(connection):
            index.add_location(knowledge_id, (document_id, page, start, end))
        for knowledge_id, concept_id, relation in queries.active_concept_links(connection):
            index.attach_concept(knowledge_id, concept_id, RelationType(relation))
        return index

    # ------------------------------------------------------------- building

    def add(self, knowledge_id: str, knowledge_type: KnowledgeType, statement: str) -> None:
        """Register an ACTIVE knowledge object."""
        normalized = normalized_statement(statement)
        self._type[knowledge_id] = knowledge_type
        self._normalized[knowledge_id] = normalized
        self._locations.setdefault(knowledge_id, set())
        self._concepts.setdefault(knowledge_id, set())
        self._by_statement.setdefault((knowledge_type, normalized), set()).add(knowledge_id)

    def add_location(self, knowledge_id: str, location: Location) -> None:
        """Record another stored location of an object (a new or linked occurrence)."""
        if knowledge_id in self._type:
            self._locations[knowledge_id].add(location)

    def set_aliases(self, concept_id: str, aliases) -> None:
        """Record the stored normalised names of a concept (a concept of the run)."""
        self._aliases[concept_id] = frozenset(aliases)

    def knows_concept(self, concept_id: str) -> bool:
        return concept_id in self._aliases

    def attach_concept(
        self, knowledge_id: str, concept_id: str, relation: RelationType | None = None
    ) -> None:
        """Record that a concept is attached to an object by its type's relation."""
        knowledge_type = self._type.get(knowledge_id)
        if knowledge_type is None or CONCEPT_RELATION.get(knowledge_type) is None:
            return
        if relation is not None and CONCEPT_RELATION[knowledge_type] is not relation:
            return
        self._concepts[knowledge_id].add(concept_id)
        for alias in self._aliases.get(concept_id, ()):
            self._by_name.setdefault((knowledge_type, alias), set()).add(knowledge_id)

    def remove(self, knowledge_id: str) -> None:
        """Forget an object that is no longer ACTIVE (a superseded one)."""
        knowledge_type = self._type.pop(knowledge_id, None)
        if knowledge_type is None:
            return
        normalized = self._normalized.pop(knowledge_id)
        self._by_statement[(knowledge_type, normalized)].discard(knowledge_id)
        for concept_id in self._concepts.pop(knowledge_id, set()):
            for alias in self._aliases.get(concept_id, ()):
                self._by_name.get((knowledge_type, alias), set()).discard(knowledge_id)
        self._locations.pop(knowledge_id, None)

    # ------------------------------------------------------------- reading

    def knowledge_type(self, knowledge_id: str) -> KnowledgeType:
        return self._type[knowledge_id]

    def normalized(self, knowledge_id: str) -> str:
        return self._normalized[knowledge_id]

    def concept_aliases(self, concept_id: str | None) -> frozenset[str]:
        return frozenset() if concept_id is None else self._aliases.get(concept_id, frozenset())

    def object_aliases(self, knowledge_id: str) -> frozenset[str]:
        """Every stored name of every concept attached to the object."""
        names: set[str] = set()
        for concept_id in self._concepts.get(knowledge_id, ()):
            names |= self._aliases.get(concept_id, frozenset())
        return frozenset(names)

    def ids(self) -> list[str]:
        """Every ACTIVE object, by counter."""
        return by_counter(self._type)

    # ------------------------------------------------------- the P8-12 relation

    def _exact(
        self,
        knowledge_type: KnowledgeType,
        normalized: str,
        locations: set[Location],
        aliases: frozenset[str],
        other: str,
    ) -> bool:
        if self._type.get(other) is not knowledge_type or self._normalized[other] != normalized:
            return False
        if locations & self._locations[other]:
            return True
        if knowledge_type is KnowledgeType.EQUATION:
            return False
        if knowledge_type is KnowledgeType.DEFINITION:
            return bool(aliases & self.object_aliases(other))
        return True

    def exact_pair(self, first: str, second: str) -> bool:
        """Whether two stored objects are exact duplicates of each other (P8-12)."""
        return self._exact(
            self._type[first], self._normalized[first], self._locations[first],
            self.object_aliases(first), second,
        )

    def exact_matches(self, item: Item) -> list[str]:
        """The stored objects an item is an exact duplicate of, by counter."""
        normalized = normalized_statement(item.statement)
        bucket = self._by_statement.get((item.knowledge_type, normalized), set())
        aliases = self.concept_aliases(item.concept_id)
        return by_counter(
            k for k in bucket
            if self._exact(item.knowledge_type, normalized, {item.location}, aliases, k)
        )

    # ---------------------------------------------- groups and canonical members

    def groups(self, members) -> list[list[str]]:
        """Connected components of the exact relation among `members`, each by counter,
        ordered by their canonical (smallest-counter) member."""
        ordered = by_counter(members)
        parent = {k: k for k in ordered}

        def root(k: str) -> str:
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        for i, first in enumerate(ordered):
            for second in ordered[i + 1:]:
                if root(first) != root(second) and self.exact_pair(first, second):
                    a, b = root(first), root(second)
                    parent[max(a, b, key=counter)] = min(a, b, key=counter)
        components: dict[str, list[str]] = {}
        for k in ordered:
            components.setdefault(root(k), []).append(k)
        return sorted(components.values(), key=lambda group: counter(group[0]))

    def duplicate_groups(self) -> list[list[str]]:
        """Every exact-duplicate group with more than one member (P8-19)."""
        found: list[list[str]] = []
        for bucket in self._by_statement.values():
            if len(bucket) > 1:
                found.extend(group for group in self.groups(bucket) if len(group) > 1)
        return sorted(found, key=lambda group: counter(group[0]))

    def is_canonical(self, knowledge_id: str) -> bool:
        """Whether an object is the smallest-counter member of its group."""
        bucket = self._by_statement[(self._type[knowledge_id], self._normalized[knowledge_id])]
        for group in self.groups(bucket):
            if knowledge_id in group:
                return group[0] == knowledge_id
        return True  # pragma: no cover - every object is in its own bucket

    def canonical_members(self, members) -> list[str]:
        """The canonical member of each group among `members`, by counter."""
        return [group[0] for group in self.groups(members)]

    # ------------------------------------------------------ comparison targets

    def same_statement(self, item: Item) -> list[str]:
        """Canonical objects of the item's type with its normalised statement."""
        normalized = normalized_statement(item.statement)
        return self.canonical_members(self._by_statement.get((item.knowledge_type, normalized), ()))

    def statement_peers(self, knowledge_id: str) -> list[str]:
        """The canonical members of the *other* groups with this object's type and
        normalised statement - for an EQUATION, the identical equations stored at
        another location."""
        bucket = self._by_statement[(self._type[knowledge_id], self._normalized[knowledge_id])]
        return [group[0] for group in self.groups(bucket) if knowledge_id not in group]

    def same_concept(self, knowledge_type: KnowledgeType, normalized: str, aliases) -> list[str]:
        """Canonical objects of this type attached to a concept sharing a stored name,
        whose statement is not identical - the P8-13 candidates, by counter."""
        found: set[str] = set()
        for alias in aliases:
            found |= self._by_name.get((knowledge_type, alias), set())
        return by_counter(
            k for k in found if self._normalized[k] != normalized and self.is_canonical(k)
        )
