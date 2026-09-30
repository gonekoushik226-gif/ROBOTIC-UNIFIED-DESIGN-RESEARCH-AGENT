"""Stage 9 - content classification: which text may yield claims at all.

`ARCHITECTURE.md` section 7.1 stage 9 is "Content classification: blocks -> kinds".
ADR 0017 places stages 9-14 in Phase 5.

**Why this stage matters more than its size suggests.** The supplied acceptance
document is 55% question bank - practice problems and previous years' examination
questions - and multiple-choice options are, by design, mostly *false*. Page 129
carries the option "(iv) Resonant frequency depends on resistance.", which is false
for the series RLC circuit it is about. A detector that read that line as a claim
would store a falsehood attributed to the source as `REPORTED_BY_SOURCE` - the
exact failure Part 1 section 5 exists to prevent. The problem is not yield; it is
truthfulness.

So text is classified before any detector sees it, and non-expository text is
**masked with spaces of the same length**. Masking rather than deleting keeps every
offset valid, so a candidate's span is still a true position in the stored segment
text (decision D-40, ADR 0019).

What is masked from claim extraction:

* **Question sections**, across page boundaries: opened by a line that is exactly
  a question-section heading (`Exercises`, `Practice Problems N`,
  `Previous Years' Questions`, `Answer Keys`, `Direction for questions ...`) and
  closed by the next page carrying a chapter opener (`CHAPTER HIGHLIGHTS`) - the
  whole page, because the marker's position within a page is not reliable. These
  headings are
  conventional in examination-oriented textbooks; a document without them is
  treated as expository throughout, with only the line-level guards below.
* **Option lines anywhere**: `(A)`-`(D)` and `(i)`-`(x)` - distractors are false.
* **Questions and blanks**: lines ending in `?`, containing `____`, or carrying an
  examination year tag such as `[2015]`.
* **Worked-example stems**: from an `Example N` line up to its `Solution` line.
  The stem states a problem's hypothetical givens ("A capacitor of 100 mF stores
  10 mJ"), not general knowledge.

What is **not** masked, and why: worked *solutions*. They apply general laws and
would lose real material if cut. The residual risk - a solution-specific sentence
matching a claim pattern - is contained by the detectors' narrowness and, for
relationships, by the rule that both endpoints must be concepts the document
defines. It is recorded as a limitation, not assumed away.

Heuristic, not specification: the markers above were measured on the supplied
document. Nothing here is guessed from a title or a filename.
"""

import re
from dataclasses import dataclass

_QUESTION_SECTION = re.compile(
    r"^\s*(?:Exercises|Practice\s+Problems(?:\s*\d+)?|Previous\s+Years?[’']?\s*Questions"
    r"|Answer\s+Keys?|Direction\s+for\s+questions\b.*)\s*$",
    re.IGNORECASE,
)
_CHAPTER_OPENER = re.compile(r"^\s*CHAPTER\s+HIGHLIGHTS\s*$", re.IGNORECASE)

_OPTION_LINE = re.compile(r"^\s*\((?:[A-Da-d]|i{1,3}|iv|v|vi{0,3}|ix|x)\)")
_YEAR_TAG = re.compile(r"\[(?:19|20)\d\d\]")
_BLANK = re.compile(r"_{3,}")
_EXAMPLE_START = re.compile(r"^\s*Example\b", re.IGNORECASE)
_SOLUTION_START = re.compile(r"^\s*(?:Solution|Sol\.?)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PageView:
    """One page, classified. Both texts have exactly the original's length."""

    page_number: int
    original: str
    #: Only expository text survives; everything else is spaces. For claims.
    expository: str
    #: Question sections masked, examples kept. For the EXAMPLE detector.
    instructional: str
    question_chars: int
    masked_chars: int

    @property
    def fully_question(self) -> bool:
        return self.question_chars > 0 and not self.expository.strip()


def _mask(line: str) -> str:
    """Spaces in place of everything but line breaks, so offsets never shift."""
    return "".join(c if c in "\r\n" else " " for c in line)


def classify_pages(pages: tuple[tuple[int, str], ...]) -> tuple[PageView, ...]:
    """Classify a document's pages in reading order.

    State carries across pages because a question section that starts on page 20
    continues until the next chapter opener, which may be several pages later.
    """
    views: list[PageView] = []
    in_questions = False
    for page_number, text in pages:
        expository: list[str] = []
        instructional: list[str] = []
        in_stem = False
        question_chars = 0
        # A chapter opener closes the previous chapter's question section for the
        # WHOLE page, not from the line where the marker appears. Measured on the
        # acceptance document: the text layer emits the "CHAPTER HIGHLIGHTS" sidebar
        # at the END of the page, after the new chapter's opening text - so a
        # line-position rule masked the Superposition theorem and its four steps.
        # Reading order within a page is not layout order.
        if any(_CHAPTER_OPENER.match(line.strip()) for line in text.splitlines()):
            in_questions = False
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if _QUESTION_SECTION.match(stripped):
                in_questions = True

            if in_questions:
                question_chars += len(stripped)
                expository.append(_mask(line))
                instructional.append(_mask(line))
                continue

            instructional.append(line)

            if _EXAMPLE_START.match(stripped):
                in_stem = True
            elif _SOLUTION_START.match(stripped):
                in_stem = False

            if (
                in_stem
                or _OPTION_LINE.match(stripped)
                or _YEAR_TAG.search(stripped)
                or _BLANK.search(stripped)
                or stripped.endswith("?")
            ):
                expository.append(_mask(line))
            else:
                expository.append(line)

        exp_text = "".join(expository)
        views.append(
            PageView(
                page_number=page_number,
                original=text,
                expository=exp_text,
                instructional="".join(instructional),
                question_chars=question_chars,
                masked_chars=sum(1 for a, b in zip(text, exp_text) if a != b),
            )
        )
    return tuple(views)
