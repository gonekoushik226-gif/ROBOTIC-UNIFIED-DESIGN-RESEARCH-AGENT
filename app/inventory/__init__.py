"""What RUDRA currently knows, as a person reads it.

`build` answers "what is in my knowledge base?" without a word of the database in it:

* **per document** - what was stored from it, what was linked to knowledge other evidence
  already supported, and what was found but *not* stored, each kind of problem explained in
  plain language with examples;
* **per item** - every concept, definition, equation, variable, unit, property, rule, example
  and relationship, where it came from (document, page, the quoted text), and how sure RUDRA
  is of it;
* **for calculation** - which stored equations can be calculated with, and why the others cannot.

Nothing is invented or smoothed over: an item the extraction doubted says so and why, and a
problem the extraction recorded is listed rather than counted away. Reads only.
"""

from app.inventory.build import (
    ISSUE_WORDS,
    KINDS,
    DocumentSummary,
    Inventory,
    IssueGroup,
    Item,
    Readiness,
    Totals,
    build,
    to_json,
)

__all__ = ["DocumentSummary", "ISSUE_WORDS", "Inventory", "IssueGroup", "Item", "KINDS", "Readiness", "Totals",
           "build", "to_json"]
