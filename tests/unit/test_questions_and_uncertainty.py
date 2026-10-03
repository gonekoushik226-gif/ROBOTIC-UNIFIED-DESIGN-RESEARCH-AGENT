"""Natural questions about the user's knowledge, and uncertainty carried to the answer.

The interpreter (grammar version 3) turns the ways people ask - explain, how does it
work, properties, compare definitions, equations, variables, where is it stated,
summarize, "according to my documents" - into RUDRA's own structured queries; a question
that names nothing ("summarize this section") is INCOMPLETE, never guessed. OCR pages
fall back only where native text is missing, are kept as OCR, and an unavailable OCR
engine never stops an import. Uncertain statements reach the answer with their reason.
"""

from __future__ import annotations

import json

import pytest

from app.documents import ocr, readers
from app.documents.ports import ParsedDocument, ParsedPage
from app.nlu import InterpretationStatus as S
from app.nlu import interpret
from app.orchestration.answers import _why_uncertain
from app.ui.gui import answerview


def _intent(text):
    result = interpret(text)
    (intent,) = result.intents
    return result, intent


@pytest.mark.parametrize("question, rule, output, target", [
    ("Explain the operating principle of a MOSFET", "query.explain_principle", "explanation", "MOSFET"),
    ("Describe the working principle of the transformer", "query.explain_principle", "explanation", "transformer"),
    ("How does a MOSFET work?", "query.how_works", "explanation", "MOSFET"),
    ("What are the properties of a capacitor?", "query.properties", "properties", "capacitor"),
    ("Which characteristics does an ideal op-amp have?", "query.properties", "properties", "ideal op-amp"),
    ("Compare the definitions of resistance", "query.compare_definitions", "definitions_all", "resistance"),
    ("Which equations are given for resonance?", "query.equations_given", "equations", "resonance"),
    ("Formulas for power", "query.equations_short", "equations", "power"),
    ("What variables are associated with Ohm's law?", "query.variables", "variables", "Ohm's law"),
    ("Where is Kirchhoff's current law stated?", "query.where_stated", "locations", "Kirchhoff's current law"),
    ("Summarize capacitance", "query.summarize", "summary", "capacitance"),
    ("Give me an overview of Thevenin's theorem", "query.summarize", "summary", "Thevenin's theorem"),
    ("What is an inductor?", "query.what_is", "definition", "inductor"),
])
def test_natural_questions_become_structured_local_queries(question, rule, output, target):
    result, intent = _intent(question)
    assert result.status is S.INTERPRETED
    assert (intent.rule, intent.requested_output, intent.target) == (rule, output, target)
    assert intent.command[:3] == ("query", "--name", target)


@pytest.mark.parametrize("question", [
    "What are the properties of MOSFETs according to my documents?",
    "Explain Thevenin's theorem based on my notes",
    "Summarize resonance using my documents",
])
def test_using_my_documents_limits_the_answer_to_the_users_books(question):
    _result, intent = _intent(question)
    assert intent.source_scope == "MY_BOOKS" and intent.command[-2:] == ("--scope", "my-books")


@pytest.mark.parametrize("question", ["Summarize this section", "Explain this", "What are the properties of this?",
                                      "Compare the definitions", "Where is that stated?"])
def test_a_question_that_names_nothing_is_incomplete_not_guessed(question):
    result, intent = _intent(question)
    assert result.status is S.INCOMPLETE and intent.target is None and intent.command is None
    assert intent.missing


def test_an_unsupported_request_is_reported_as_such():
    result = interpret("Sing me a song about the weather tomorrow")
    assert result.status is not S.INTERPRETED


# ------------------------------------------------------------------ OCR fallback, without the engine


class _Native:
    """A PDF parser whose page 2 has no text layer."""

    name = "pypdf"

    def open(self, path):
        return ParsedDocument(page_count=2)

    def pages(self, path):
        yield ParsedPage(1, "A resistor is a component that opposes the flow of current. " * 4)
        yield ParsedPage(2, "")


def _recognized(number: int) -> ocr.OcrPage:
    words = (ocr.OcrWord("A", (10, 10, 5, 8)), ocr.OcrWord("coil", (20, 10, 20, 8)))
    return ocr.OcrPage(number, (ocr.OcrLine("A coil", words),), 600, 800, "en-GB")


def test_native_text_is_kept_and_only_empty_pages_are_recognized(monkeypatch):
    asked = []
    monkeypatch.setattr(ocr, "status", lambda **k: ocr.OcrStatus(True, "", ("en-GB",)))
    monkeypatch.setattr(ocr, "recognize_pdf_pages", lambda path, pages: asked.append(tuple(pages)) or
                        tuple(_recognized(n) for n in pages))
    reader = readers.OcrPdfReader(_Native())
    first, second = list(reader.pages("book.pdf"))
    assert asked == [(2,)]
    assert first.origin is None and first.extraction_method == "pypdf"
    assert second.origin == "OCR" and second.extraction_method == "windows-ocr" and second.text == "A coil"
    assert second.confidence is None  # Windows reports none, and none is invented
    assert json.loads(json.dumps(second.layout))["lines"][0]["words"][1]["box"] == [20, 10, 20, 8]
    assert any("read by OCR" in note for note in reader.notes)


def test_an_import_goes_on_when_ocr_is_unavailable(monkeypatch):
    monkeypatch.setattr(ocr, "status", lambda **k: ocr.OcrStatus(False, "no OCR language is installed"))
    monkeypatch.setattr(ocr, "recognize_pdf_pages", lambda *a: pytest.fail("OCR must not be called"))
    reader = readers.OcrPdfReader(_Native())
    pages = list(reader.pages("book.pdf"))
    assert [p.text for p in pages][1] == "" and pages[1].origin is None
    assert any("OCR is not available" in note and "no OCR language" in note for note in reader.notes)


def test_a_failing_ocr_run_is_reported_and_the_import_goes_on(monkeypatch):
    monkeypatch.setattr(ocr, "status", lambda **k: ocr.OcrStatus(True, "", ("en-GB",)))

    def broken(path, pages):
        raise ocr.OcrUnavailable("the engine stopped")

    monkeypatch.setattr(ocr, "recognize_pdf_pages", broken)
    reader = readers.OcrPdfReader(_Native())
    assert [p.text for p in reader.pages("book.pdf")][1] == ""
    assert any("OCR failed" in note for note in reader.notes)


def test_ocr_can_be_switched_off():
    reader = readers.OcrPdfReader(_Native(), use_ocr=False)
    assert [p.origin for p in reader.pages("book.pdf")] == [None, None] and reader.notes == []


# ------------------------------------------------------------------ uncertainty reaches the answer


@pytest.mark.parametrize("method, words", [
    ("deterministic/DefinitionCandidate@v6+ocr", "recognized by OCR"),
    ("deterministic/DefinitionCandidate@v6+weak", "general shape"),
    ("deterministic/EquationCandidate@v6+layout?", "PDF page"),
    ("deterministic/EquationCandidate@v6", "text layer"),
])
def test_every_kind_of_uncertainty_is_explained(method, words):
    reason = _why_uncertain({"certainty": "UNCERTAIN"}, [{"extraction_method": method}])
    assert words in reason


def test_a_certain_statement_carries_no_uncertainty_note():
    assert _why_uncertain({"certainty": "REPORTED_BY_SOURCE"}, [{"extraction_method": "x+ocr"}]) is None


def test_the_answer_view_shows_uncertain_lines_with_the_answer_not_in_the_sources():
    stdout = json.dumps({"interpretation": {"text": "What is a solenoid?"}, "parts": [{
        "number": 1, "status": "ANSWERED", "answer": "Definition: A solenoid is a coil.",
        "uncertain": ["A solenoid is a coil - recognized by OCR from a scanned page or image; compare it with the source"],
        "sources": ["scan.png, image 1"], "basis": ["K-00000001"]}]})
    document = answerview.parse_answer(stdout)
    (part,) = document.parts
    assert part.lines == ("Definition: A solenoid is a coil.",)
    assert part.uncertain and "recognized by OCR" in part.uncertain[0]
    plain = answerview.plain_text(document)
    assert "Uncertain:" in plain and "scan.png" not in plain  # sources stay hidden until asked for


def test_a_short_native_page_keeps_its_own_text_when_ocr_finds_nothing_more(monkeypatch):
    class _Title(_Native):
        def pages(self, path):
            yield ParsedPage(1, "Chapter 1")

    monkeypatch.setattr(ocr, "status", lambda **k: ocr.OcrStatus(True, "", ("en-GB",)))
    monkeypatch.setattr(ocr, "recognize_pdf_pages", lambda path, pages: tuple(
        ocr.OcrPage(n, (ocr.OcrLine("Chapter 1"),), 600, 800, "en-GB") for n in pages))
    reader = readers.OcrPdfReader(_Title())
    (page,) = list(reader.pages("book.pdf"))
    assert page.text == "Chapter 1" and page.origin is None and page.extraction_method == "pypdf"
    assert any("kept their own text" in note for note in reader.notes)


def test_the_ocr_switch_turns_ocr_off_for_the_process(monkeypatch):
    monkeypatch.setenv(ocr.SWITCH, "1")
    assert not ocr.status().available and ocr.SWITCH in ocr.status().reason
