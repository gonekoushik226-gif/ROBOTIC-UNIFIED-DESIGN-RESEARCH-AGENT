"""Phase 18: controlled Internet (ADR 0050).

What every test holds Phase 18 to: local knowledge first; without the user's named site the
answer is "Insufficient authorized information." and nothing is fetched; retrieval stays
inside the authorized scope and the limits; every external claim keeps its URL, retrieval
time, content hash, span and authorization; external information is labelled and never
passes as the user's (`my-books`); a conflict with a local definition is recorded, never
resolved; a failure stores nothing.

**Nothing here leaves the machine:** every page comes from a loopback server on 127.0.0.1
inside the test process (P18-13).
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from app.core.errors import InvalidInputError
from app.internet import ResearchStatus, RetrievalFailed, Research, retrieve, site, within
from app.internet.research import sentences_naming, subject_of
from app.internet.sites import read_html
from app.models.entities import Document, MemoryItem, Source
from app.provenance import ProvenanceScope, ProvenanceService
from app.query import SourceScope
from app.storage import Repository, connect, migrate
from app.ui.cli.main import main
from tests.unit.loopback import Loopback, closed_port, site_routes
from tests.unit.pdf_fixtures import make_pdf

#: A local page whose definition rule C1 finds contradicted by the loopback page.
LOCAL_PAGE = ("Memristor\n"
              "A memristor is a passive element with 3 terminals.\n")


@pytest.fixture(scope="module")
def web():
    with Loopback(site_routes()) as server:
        yield server


def _database(tmp_path: Path) -> tuple[Repository, Path]:
    path = tmp_path / "project" / "data" / "database" / "knowledge.db"
    path.parent.mkdir(parents=True)
    connection = connect(path)
    migrate(connection, database_path=path)
    connection.commit()
    return Repository(connection), path


def _research(repository, path, tmp_path) -> Research:
    return Research(repository, database_path=path, documents_dir=tmp_path / "project" / "data" / "documents")


def _counts(repository) -> tuple[int, ...]:
    return tuple(repository.count(t) for t in (Document, Source, MemoryItem))


# ------------------------------------------------------------ the authorized site (P18-3)


def test_a_site_authorizes_its_directory_only():
    authorized = site("http://Example.org:8080/docs/memristor.html#top")
    assert authorized.scope == "http://example.org:8080/docs/" and authorized.url.endswith("memristor.html")
    assert within(authorized, "http://example.org:8080/docs/other.html")
    for outside in ("http://example.org:8080/private/x.html", "https://example.org:8080/docs/x.html",
                    "http://example.org/docs/x.html", "http://evil.example/docs/x.html", "ftp://example.org/docs/"):
        assert not within(authorized, outside), outside


@pytest.mark.parametrize("url", ["ftp://example.org/a.txt", "file:///C:/Windows/win.ini", "example.org/page",
                                 "http://user:secret@example.org/page", "http:///nohost"])
def test_only_plain_web_addresses_are_accepted(url):
    with pytest.raises(InvalidInputError):
        site(url)


def test_the_reader_keeps_visible_text_and_the_title():
    text, title = read_html("<html><head><title> A  page </title><script>x = 1;</script></head>"
                            "<body><p>First &amp; second.</p><style>p{}</style><div>Third</div></body></html>")
    assert title == "A page" and text == "First & second.\n\nThird"


def test_sentences_naming_the_subject_with_their_spans():
    text = "Memristor\nA memristor is passive. Capacitors store charge. Memristors remember.\n"
    found = sentences_naming(text, "memristor")
    assert [s for _, _, s in found] == ["Memristor", "A memristor is passive.", "Memristors remember."]
    assert all(text[a:b] == s for a, b, s in found)


def test_the_subject_of_a_question():
    assert subject_of("What is a memristor?") == "memristor"
    with pytest.raises(InvalidInputError):
        subject_of("Open Calculator.")


# ------------------------------------------------------------ retrieval (P18-4)


def test_the_page_is_retrieved_with_nothing_of_the_users_sent(web):
    retrieved = retrieve(site(web.url("/docs/memristor.html")))
    assert retrieved.title == "Memristor notes" and "not text" not in retrieved.text
    assert retrieved.content_type == "text/html" and len(retrieved.sha256) == 64
    path, headers = web.requests[-1]
    assert path == "/docs/memristor.html" and headers["User-Agent"].startswith("RUDRA/")
    assert "Cookie" not in headers and "Authorization" not in headers


def test_a_redirect_inside_the_scope_is_followed(web):
    assert retrieve(site(web.url("/docs/moved.html"))).final_url == web.url("/docs/memristor.html")


@pytest.mark.parametrize(("path", "reason"), [("/docs/away.html", "outside the authorized scope"),
                                              ("/docs/huge.html", "larger than 2 MiB"),
                                              ("/docs/binary.bin", "application/octet-stream"),
                                              ("/docs/missing.html", "HTTP 404")])
def test_what_the_limits_refuse(web, path, reason):
    with pytest.raises(RetrievalFailed) as failure:
        retrieve(site(web.url(path)))
    assert reason in failure.value.reason


def test_a_refused_connection_is_a_failure():
    with pytest.raises(RetrievalFailed) as failure:
        retrieve(site(f"http://127.0.0.1:{closed_port()}/docs/page.html"))
    assert "could not be reached" in failure.value.reason


# ------------------------------------------------------------ research (P18-2 ... P18-10)


def test_local_first_and_insufficient_without_a_site(web, tmp_path):
    repository, path = _database(tmp_path)
    with closing(repository.connection):
        before, requests = _counts(repository), len(web.requests)
        answer = _research(repository, path, tmp_path).ask("What is a memristor?")
        assert answer.status is ResearchStatus.INSUFFICIENT
        assert answer.message.startswith("Insufficient authorized information.\n")
        assert "--site URL" in answer.message and answer.record is None
        assert _counts(repository) == before and len(web.requests) == requests  # nothing fetched


def test_no_database_is_insufficient_too(tmp_path):
    answer = Research(None, database_path=tmp_path / "none.db", documents_dir=tmp_path).ask("What is a memristor?")
    assert answer.status is ResearchStatus.INSUFFICIENT


def test_the_authorized_site_answers_labelled_and_with_provenance(web, tmp_path):
    repository, path = _database(tmp_path)
    with closing(repository.connection):
        url = web.url("/docs/memristor.html")
        answer = _research(repository, path, tmp_path).ask("What is a memristor?", site_url=url)
        repository.connection.commit()
        assert answer.status is ResearchStatus.EXTERNAL
        assert dict(answer.labels) == {"SOURCE": "AUTHORIZED_EXTERNAL_SOURCE", "URL": url,
                                       "Retrieved": answer.record.retrieved_at, "Status": "EXTERNAL_INFORMATION"}
        assert [c.statement for c in answer.external] == [
            "Memristor", "A memristor is a passive element with 2 terminals.",
            "Chua proposed the memristor in 1971 as the fourth basic circuit element."]
        document = repository.get(Document, answer.record.document_id)
        source = repository.get(Source, answer.record.source_id)
        assert document.source_type == "WEB" and document.file_hash == answer.record.sha256
        assert Path(document.file_path).read_bytes().startswith(b"<html>")
        assert (source.source_category.value, source.availability.value, source.url) == (
            "AUTHORIZED_EXTERNAL_SOURCE", "EXTERNAL_ONLY", url)
        grant = repository.get(MemoryItem, answer.record.grant_id)
        assert grant.key == f"internet-authorization:{url}" and '"scope": "SPECIFIC_WEBSITE"' in grant.value
        service = ProvenanceService(repository)
        claim = answer.external[1].knowledge_id
        authorized = service.of_item(claim, ProvenanceScope.AUTHORIZED)
        assert authorized.status.value == "AVAILABLE" and authorized.verification.value == "VERIFIED"
        assert authorized.citations[0].evidence.extraction_timestamp == answer.record.retrieved_at
        mine = service.of_item(claim, ProvenanceScope.MY_BOOKS)
        assert mine.status.value != "AVAILABLE" and mine.citations == ()  # never the user's own


def test_the_same_page_again_is_not_stored_twice(web, tmp_path):
    repository, path = _database(tmp_path)
    with closing(repository.connection):
        research = _research(repository, path, tmp_path)
        url = web.url("/docs/memristor.html")
        first = research.ask("What is a memristor?", site_url=url)
        documents = repository.count(Document)
        again = research.ask("What is a memristor?", site_url=url)
        assert not again.record.new and again.record.document_id == first.record.document_id
        assert repository.count(Document) == documents
        assert [c.knowledge_id for c in again.external] == [c.knowledge_id for c in first.external]


@pytest.mark.parametrize("page", ["/docs/away.html", "/docs/huge.html", "/docs/binary.bin", "/docs/unrelated.html"])
def test_a_failure_or_an_unrelated_page_stores_nothing(web, tmp_path, page):
    repository, path = _database(tmp_path)
    with closing(repository.connection):
        before = _counts(repository)
        research = _research(repository, path, tmp_path)
        if page == "/docs/unrelated.html":
            answer = research.ask("What is a memristor?", site_url=web.url(page))
            assert answer.status is ResearchStatus.NOT_ON_SITE and "Nothing was stored" in answer.message
        else:
            with pytest.raises(RetrievalFailed):
                research.ask("What is a memristor?", site_url=web.url(page))
        assert _counts(repository) == before
        assert not (tmp_path / "project" / "data" / "documents").exists()


def test_a_conflicting_external_definition_is_recorded_and_both_are_kept(web, tmp_path):
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(make_pdf([LOCAL_PAGE]))
    root = tmp_path / "project"
    assert main(["extract", str(pdf), "--project-root", str(root)]) == 0
    path = root / "data" / "database" / "knowledge.db"
    with closing(connect(path)) as connection:
        repository = Repository(connection)
        research = _research(repository, path, tmp_path)
        local = research.ask("What is a memristor?")
        assert local.status is ResearchStatus.LOCAL and local.local.definitions
        answer = research.ask("What is a memristor?", site_url=web.url("/docs/memristor.html"))
        (conflict,) = answer.conflicts
        assert conflict.local_statement == "A memristor is a passive element with 3 terminals."
        assert conflict.external_statement == "A memristor is a passive element with 2 terminals."
        # The local answer is unchanged: nothing was replaced.
        again = research.ask("What is a memristor?")
        assert again.local.definitions == local.local.definitions


def test_stored_external_claims_are_shown_only_in_the_authorized_scope(web, tmp_path):
    repository, path = _database(tmp_path)
    with closing(repository.connection):
        research = _research(repository, path, tmp_path)
        research.ask("What is a memristor?", site_url=web.url("/docs/memristor.html"))
        assert research.ask("What is a memristor?").cached == ()
        cached = research.ask("What is a memristor?", scope=SourceScope.AUTHORIZED).cached
        assert len(cached) == 3 and all(entry[2].endswith("Z") for entry in cached)
