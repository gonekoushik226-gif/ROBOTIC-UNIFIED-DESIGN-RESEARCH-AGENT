"""Stage 13 - normalization (Part 2 section 60).

Section 60's order puts normalization after validation and before provenance
attachment. What normalization is allowed to do here is narrow, and the narrowness
is the point:

* **Whitespace only, for statements and expressions.** A PDF breaks one sentence
  across several lines; collapsing that whitespace changes nothing the source said.
* **No Unicode compatibility folding (NFKC) on content.** NFKC would turn `x²` into
  `x2`, `Ω` (U+2126) into `Ω` (U+03A9), and `½` into `1⁄2` - rewriting equations
  and units into something the source did not print. Concept *names* are normalised
  for lookup by `app.models.naming.normalize_alias` (decision D-30) exactly as
  Phase 3 does, and nowhere else.
* **No repair.** A flattened fraction stays flattened. Part 1 section 5.
* **No `normalized_hash`.** Decision D-36 (ADR 0020): populating it would make the
  live `ux_knowledge_object_normalized` index deduplicate by `IntegrityError`, which
  Part 3 sections 72-73 forbid. Deduplication is Phase 8.
"""

import re

_WS = re.compile(r"\s+")

#: Leading articles are not part of a term: "a branch" names the concept Branch.
_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.I)

#: Discourse markers that open a sentence but are not part of what it names:
#: "For example, a linear element must ..." is about a linear element.
_DISCOURSE = re.compile(
    r"^(?:for example|for instance|hence|thus|therefore|so|also|now|here|"
    r"similarly|however|in general|in this case)\s*,?\s+",
    re.I,
)

#: Anything before the first letter or digit. On the acceptance document the
#: "therefore" sign arrives as a backslash; it names nothing, so it is not part of
#: a term. The evidence text keeps it verbatim - only the *name* is cleaned.
_LEADING_JUNK = re.compile(r"^[^\w]+")


def collapse(text: str) -> str:
    """Collapse runs of whitespace, including the line breaks a PDF inserts."""
    return _WS.sub(" ", text).strip()


def term(text: str) -> str:
    """A concept name as it should be stored.

    Whitespace collapsed; leading symbols, discourse markers and one article
    removed; first letter capitalised. Otherwise case is preserved - "Ohm's law"
    stays as printed. Case-insensitive lookup is `normalize_alias`'s job (D-30),
    not this function's. This touches **names only**, never evidence text.
    """
    cleaned = _LEADING_JUNK.sub("", collapse(text))
    cleaned = _DISCOURSE.sub("", cleaned)
    cleaned = _ARTICLE.sub("", cleaned).strip(" .,:;-–—")
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def label(prefix: str, text: str, limit: int = 60) -> str:
    """A knowledge object's `canonical_name`.

    Invariant I-KO-NAME (ADR 0012): this labels the record and carries no
    referential authority - nothing is ever looked up by it.
    """
    body = collapse(text)
    if len(body) > limit:
        body = body[: limit - 1].rstrip() + "…"
    return f"{prefix}: {body}" if body else prefix
