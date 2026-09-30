"""Which concept pairs are recorded `POSSIBLE_EQUIVALENT` (ADR 0033, P8-16).

A pair of distinct ACTIVE concepts qualifies on either of two kinds of evidence:

* **a shared name** - they share an ACTIVE normalised alias and are concepts of
  different runs: each is evidenced by at least one extraction run, and no run
  evidences both. This covers the duplicates re-extraction creates (ADR 0029) and
  the same term in different documents. Concepts are per run (interpretation I-A),
  so two concepts of one run never qualify by name.
* **a source statement** - an ACTIVE EXPLICIT `EQUIVALENT_TO` edge joins them
  (section 44's different-name case).

One record per unordered pair. When both kinds apply, the shared name is recorded
(the first bullet of P8-16); the pair is never recorded twice. Nothing is ever
confirmed, denied or merged: the status is always `POSSIBLE_EQUIVALENT`.
"""

from dataclasses import dataclass

from app.deduplication.rules import canonical_pair
from app.models.enums import ConceptEquivalenceBasis
from app.storage import queries


@dataclass(frozen=True, slots=True)
class ConceptPair:
    """One qualifying pair, in canonical storage order, with its evidence."""

    concept_a_id: str
    concept_b_id: str
    basis: ConceptEquivalenceBasis
    shared_name: str | None = None
    relationship_id: str | None = None


def equivalent_pairs(connection, concept_ids=None) -> list[ConceptPair]:
    """Every qualifying pair with at least one member in `concept_ids`.

    `concept_ids=None` means every ACTIVE concept (what `merge` compares); stage 15
    passes the concepts of its run. Pairs come back in canonical order, sorted.
    """
    names: dict[str, set[str]] = {}
    for concept_id, alias in queries.active_concept_aliases(connection):
        names.setdefault(concept_id, set()).add(alias)
    runs: dict[str, set[str]] = {}
    documents: dict[str, set[str]] = {}
    for concept_id, run_id, document_id in queries.concept_scopes(connection):
        documents.setdefault(concept_id, set()).add(document_id)
        if run_id is not None:
            runs.setdefault(concept_id, set()).add(run_id)

    wanted = set(names) if concept_ids is None else set(concept_ids) & set(names)
    owners: dict[str, set[str]] = {}
    for concept_id, aliases in names.items():
        for alias in aliases:
            owners.setdefault(alias, set()).add(concept_id)

    found: dict[tuple[str, str], ConceptPair] = {}
    for concept_id in sorted(wanted):
        own_runs = runs.get(concept_id, set())
        if not own_runs:
            continue
        for alias in sorted(names[concept_id]):
            for other in sorted(owners[alias]):
                other_runs = runs.get(other, set())
                if other == concept_id or not other_runs or own_runs & other_runs:
                    continue
                pair = canonical_pair(concept_id, other)
                if pair in found:
                    continue
                same = documents.get(concept_id) == documents.get(other) and len(
                    documents.get(concept_id, ())
                ) == 1
                found[pair] = ConceptPair(
                    *pair,
                    basis=(
                        ConceptEquivalenceBasis.SHARED_NAME_SAME_DOCUMENT
                        if same
                        else ConceptEquivalenceBasis.SHARED_NAME_OTHER_DOCUMENT
                    ),
                    shared_name=alias,
                )

    for edge in queries.stated_equivalences(connection):
        ends = (edge.from_concept_id, edge.to_concept_id)
        if concept_ids is not None and not (set(ends) & set(concept_ids)):
            continue
        pair = canonical_pair(*ends)
        if pair not in found:
            found[pair] = ConceptPair(
                *pair, basis=ConceptEquivalenceBasis.STATED_EQUIVALENT_TO, relationship_id=edge.id
            )
    return [found[pair] for pair in sorted(found)]
