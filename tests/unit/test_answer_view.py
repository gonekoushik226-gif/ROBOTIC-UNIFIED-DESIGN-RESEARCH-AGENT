"""The answer view: the answer alone first, its sources only when asked for.

A real document is imported into a temporary project through the command line's own
`extract`; questions go through the window's Ask page. The live database is never
opened.
"""

from __future__ import annotations

import gc
import hashlib
import json
import sqlite3
import time
import tkinter as tk
from contextlib import closing

import pytest

from app.ui.gui import answerview, commands, theme
from app.ui.gui.window import RudraWindow
from tests.unit.pdf_fixtures import make_pdf

PAGE = (
    "Resistance\n"
    "Resistance is defined as the opposition offered by a material to the flow of current.\n"
    "Current is defined as the rate of flow of charge.\n"
    "Resistance depends on current.\n"
)
HIDDEN_UNTIL_ASKED = ("DOC-", "K-0", "CPT-", "p.1", "S O U R C E S", "SHA-256", "query --name", "S T A T U S",
                      "circuits.pdf")


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("answers")
    pdf = root / "circuits.pdf"
    pdf.write_bytes(make_pdf([PAGE]))
    result = commands.run(["extract", str(pdf)], root / "project")
    assert result.ok, result.stderr
    return root / "project"


@pytest.fixture
def window(project, capsys):
    try:
        with capsys.disabled():
            root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - a machine without a display
        pytest.skip(f"Tk cannot open a window here: {exc}")
    root.withdraw()
    built = RudraWindow(root, project, full=True, autostart=False)
    yield built
    root.destroy()
    del built, root
    gc.collect()
    gc.freeze()


def _wait(window: RudraWindow, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while window.busy and time.monotonic() < deadline:
        window.root.update()
        time.sleep(0.01)
    assert not window.busy, "the command did not finish"
    window.root.update()


def _ask(window: RudraWindow, question: str) -> None:
    window.full.pages["ask"].question.set(question)
    assert window.full.pages["ask"].run()
    _wait(window)


def _provenance_state(project) -> tuple:
    database = project / "data" / "database" / "knowledge.db"
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        occurrences = connection.execute(
            "SELECT id, knowledge_id, source_id, document_id, page_number, original_text FROM source_occurrence ORDER BY id"
        ).fetchall()
        documents = connection.execute("SELECT id, file_path, file_hash FROM document ORDER BY id").fetchall()
    trace = commands.run(["provenance", "K-00000001", "--json"], project)
    return occurrences, documents, json.loads(trace.stdout)


# ---------------------------------------------------------------- pure presentation


def test_the_answer_is_split_from_its_sources():
    stdout = json.dumps({"request": "What is resistance?", "parts": [{
        "number": 1, "status": "ANSWERED", "command": ["query", "--name", "resistance"],
        "answer": "Definition: Resistance is the opposition to current.",
        "basis": ["CPT-00000001", "K-00000001"], "sources": ['DOC-00000001 p.1: "Resistance is ..."'],
        "missing": ["Voltage (CPT-00000009)"], "next_steps": ["Use another authorized source (python -m app "
                                                              "extract FILE.pdf)."],
    }]})
    document = answerview.parse_answer(stdout)
    (part,) = document.parts
    assert part.lines == ("Definition: Resistance is the opposition to current.",)
    assert dict(part.extras)["Missing information"] == ("Voltage",)  # no database identifier in the answer
    assert dict(part.extras)["Next steps"] == ("Import a document that covers it (Import page).",)
    assert part.details.knowledge_ids() == ("K-00000001",)
    plain = answerview.plain_text(document)
    for hidden in ("DOC-", "CPT-", "K-0", "p.1", "query"):
        assert hidden not in plain
    details = dict(answerview.detail_lines(part.details, commands.display_command))
    assert details["Sources"] == 'DOC-00000001 p.1: "Resistance is ..."' and details["Based on"] == "CPT-00000001"


def test_output_that_is_not_an_answer_is_left_alone():
    assert answerview.parse_answer("Request     : ...") is None
    assert answerview.parse_answer(json.dumps({"status": "AVAILABLE"})) is None


# ---------------------------------------------------------------- in the window


def test_sources_are_hidden_until_view_sources_is_used(window, project):
    before = _provenance_state(project)
    _ask(window, "What is resistance?")
    view = window.full.output
    shown = view.text_content()
    assert "Definition: Resistance is defined as the opposition offered by a material" in shown
    for hidden in HIDDEN_UNTIL_ASKED:
        assert hidden not in shown, hidden
    assert "python -m app ask" not in shown  # not the raw command either

    # View Sources is offered for the answer ...
    (button,) = view.source_buttons.values()
    assert button.cget("text") == "View Sources"
    # ... and opening it shows the provenance RUDRA already records.
    button.invoke()
    _wait(window)
    opened = view.text_content()
    assert "DOC-00000001 p.1" in opened and "K-00000001" in opened and "CPT-00000001" in opened
    assert theme.spaced("Answered by") in opened and "query --name resistance" in opened
    assert theme.spaced("Sources") in opened
    assert "Provenance of K-00000001" in opened and "Verification: VERIFIED" in opened  # file, hash and quote checked
    assert "circuits.pdf" in opened  # the document's own name, from the provenance trace
    assert view.source_buttons[1].cget("text") == "Hide Sources"
    assert "Definition: Resistance is defined" in opened  # the answer is still there

    # Hiding them again returns to the answer alone.
    view.source_buttons[1].invoke()
    window.root.update()
    for hidden in HIDDEN_UNTIL_ASKED:
        assert hidden not in view.text_content(), hidden

    # Presentation only: every provenance record and link is exactly as before.
    assert _provenance_state(project) == before


def test_the_compact_assistant_shows_the_answer_the_same_way(window):
    window.show(full=False)
    window.compact.entry.insert(0, "What is resistance?")
    assert window.compact.send()
    _wait(window)
    shown = window.compact.output.text_content()
    assert "Definition: Resistance is defined" in shown
    assert "DOC-" not in shown and "K-0" not in shown
    assert window.compact.output.source_buttons


def test_an_unknown_answer_says_so_without_identifiers(window):
    _ask(window, "What is inductance?")
    shown = window.full.output.text_content()
    assert "Unknown." in shown
    for hidden in ("CPT-", "K-0", "DOC-"):
        assert hidden not in shown


def test_the_database_file_is_not_written_by_viewing_sources(window, project):
    database = project / "data" / "database" / "knowledge.db"
    _ask(window, "What is resistance?")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    next(iter(window.full.output.source_buttons.values())).invoke()
    _wait(window)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest
