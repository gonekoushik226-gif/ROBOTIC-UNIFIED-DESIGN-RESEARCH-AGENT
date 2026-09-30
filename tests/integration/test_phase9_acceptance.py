"""Phase 9 acceptance: Part 5 section 199 - "Find everything about MOSFETs in my books."

ADR 0035 P9-2 and P9-7, `docs/phases/PHASE_9.md` section 7. The sentence runs as the
structured concept query `query --name MOSFET` in the "my books" scope (P9-2, P9-5).

**The library** is generated one-page PDFs (original text) taken through the real
Phase 4 ingestion, Phase 5 extraction with stage 15, Phase 6 classification and the
Phase 8 `edition` and `merge` commands, **one process per step through the CLI** (the
Phase 7 and Phase 8 precedent), in a temporary project root. The live database is never
opened. Only four things are written directly, each named in section 7's design or
impossible through extraction:

* a curated `PARENT_OF` chain (Transistor -> Field-effect transistor -> MOSFET), stored
  through `ConceptService` with the book's own "is a type of" sentences as evidence:
  no Phase 5 detector writes `PARENT_OF`, and the closures must still be accepted;
* one definition stored the pre-Phase-8 way (extractor v3, no stage 15), so that the
  `merge` command has a duplicate to supersede (the Phase 8 precedent);
* book U's source set NOT_AUTHORIZED, and book G's source set AUTHORIZED_EXTERNAL_SOURCE
  ("set directly in the test database", section 7).

Books: A (MOSFET, its property, applications, prerequisite, an unlinked equation,
variable and example, a BJT distractor naming MOSFET, the curated hierarchy); B (the
same definition, linked by stage 15; "A MOS transistor is the same as a MOSFET");
C and D (definitions differing only in a threshold voltage - a rule C1 conflict -
declared editions of one work); U (unauthorised); G (JFET and "gain" distractors, out
of "my books"); E (the pre-Phase-8 duplicate); V ("MOSFETs", extracted after the index).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
from dataclasses import replace

import pytest

from app.knowledge import ConceptService, Evidence
from app.models import (
    Authorization,
    Document,
    DocumentProcessingStatus,
    DocumentSegment,
    ExtractionRun,
    ExtractionRunStatus,
    ExtractionTrigger,
    RelationshipOrigin,
    RelationType,
    Source,
    SourceAvailability,
    SourceCategory,
    SourceOccurrence,
    TextOrigin,
)
from app.models.base import utc_now
from app.storage import Repository, connect, migrate, queries
from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf

MOSFET = "A MOSFET is a voltage-controlled transistor."
MOSFET_TYPE = "A MOSFET is a type of field-effect transistor."
FET_TYPE = "A field-effect transistor is a type of transistor."
PROPERTY = "The characteristic of a MOSFET is its high input impedance."
USED_IN = "A MOSFET is used in an amplifier."
GATE_OXIDE_USE = "A gate oxide is used in a MOSFET."
PREREQUISITE = "Semiconductor is a prerequisite for MOSFET."
EQUATION = "I_D = k (V_GS - V_T)^2"
LEGEND = "The drain current follows the square law, where V_T is the threshold voltage of the device."
EXAMPLE = "A MOSFET with a threshold voltage of 2 V switches a lamp on and off."
BJT = "A BJT is a device that, unlike a MOSFET, is controlled by its base current."
MOS_SAME = "A MOS transistor is the same as a MOSFET."
MOS_DEFINED = "A MOS transistor is a metal-oxide-semiconductor transistor."
C_DEFINITION = "A MOSFET is a transistor with a threshold voltage of 0.7 V."
D_DEFINITION = "A MOSFET is a transistor with a threshold voltage of 0.3 V."
UNAUTHORISED = "A MOSFET is a device that stores charge in a vacuum."
JFET = "A JFET is a transistor controlled by a reverse-biased junction."
PLURAL = "MOSFETs are defined as transistors with an insulated gate."

BOOKS: dict[str, list[str]] = {
    "A": ["Book A\n" + "\n".join((
        MOSFET,
        "An amplifier is a circuit that increases the power of a signal.",
        "A gate oxide is a thin insulating layer beneath the gate electrode.",
        "A semiconductor is a material whose conductivity lies between a conductor and an insulator.",
        "A transistor is a semiconductor device that switches or amplifies signals.",
        "A field-effect transistor is a device controlled by an electric field.",
        BJT, PROPERTY, USED_IN, GATE_OXIDE_USE, PREREQUISITE, MOSFET_TYPE, FET_TYPE,
        EQUATION, LEGEND, "Example 1", EXAMPLE,
    ))],
    "B": ["Book B\n" + "\n".join((MOSFET, MOS_DEFINED, MOS_SAME))],
    "C": ["Book C\n" + C_DEFINITION],
    "D": ["Book D\n" + D_DEFINITION],
    "U": ["Book U\n" + UNAUTHORISED],
    "G": ["Book G\n" + "\n".join((
        JFET,
        "The gain is the ratio of output voltage to input voltage in an amplifier.",
        "The gain is the ratio of the controller output to the error signal.",
    ))],
    "V": ["Book V\n" + PLURAL],
}


# ------------------------------------------------------------------- the library


def cli(root: pathlib.Path, *args: str, in_process: bool = False) -> tuple[int, str, str]:
    """One `python -m app` step against the scratch root: (exit code, stdout, stderr)."""
    if in_process:
        from app.ui.cli.main import main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([*args, "--project-root", str(root)])
        return code, out.getvalue(), err.getvalue()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PROJECT_ROOT), timeout=240,
    )
    return done.returncode, done.stdout, done.stderr


def database(root: pathlib.Path) -> pathlib.Path:
    return root / "data" / "database" / "knowledge.db"


def extract(root: pathlib.Path, name: str, *, in_process: bool = False) -> None:
    pdf = root.parent / f"book-{name}.pdf"
    pdf.write_bytes(make_pdf(BOOKS[name]))
    code, out, err = cli(root, "extract", str(pdf), in_process=in_process)
    assert code == 0, err or out


def build_library(root: pathlib.Path, *, in_process: bool = False) -> dict:
    """Books A, B, C, D, U, G through the CLI, then the section 7 direct writes, classify,
    edition and merge. Book V is extracted later, by the index-lifecycle test."""
    root.mkdir(parents=True, exist_ok=True)
    for name in "ABCDUG":
        extract(root, name, in_process=in_process)
    ids = _identify(root)
    code, out, err = cli(root, "classify", "--run", ids["run_A"], in_process=in_process)
    assert code == 0, err or out
    code, out, err = cli(root, "edition", ids["doc_D"], "--work", ids["doc_C"], "--label",
                         "2nd edition", "--work-label", "1st edition", in_process=in_process)
    assert code == 0, err or out
    _direct_writes(root, ids)
    code, out, err = cli(root, "merge", in_process=in_process)
    assert code == 0, err or out
    return {**_identify(root), "root": str(root)}


def _identify(root: pathlib.Path) -> dict:
    """The identifiers the assertions need, read back from the scratch database."""
    connection = connect(database(root), read_only=True)
    try:
        ids: dict = {}
        for row in connection.execute("SELECT id, original_filename FROM document"):
            ids["doc_" + row["original_filename"].removeprefix("book-").removesuffix(".pdf")] = row["id"]
        for row in connection.execute("SELECT id, document_id FROM extraction_run ORDER BY run_number"):
            for key, value in list(ids.items()):
                if value == row["document_id"] and key.startswith("doc_"):
                    ids.setdefault("run_" + key[4:], row["id"])
        for key, statement in (("k_mosfet", MOSFET), ("k_type", MOSFET_TYPE), ("k_property", PROPERTY),
                               ("k_equation", EQUATION), ("k_legend", LEGEND), ("k_example", EXAMPLE),
                               ("k_bjt", BJT), ("k_mos", MOS_DEFINED), ("k_c", C_DEFINITION),
                               ("k_d", D_DEFINITION), ("k_unauthorised", UNAUTHORISED), ("k_jfet", JFET)):
            found = [r["id"] for r in connection.execute(
                "SELECT id FROM knowledge_object WHERE statement = ? ORDER BY id", (statement,))]
            ids[key] = found[0] if found else None
            ids[key + "_all"] = found
        ids["concepts"] = {
            r["id"]: r["canonical_name"]
            for r in connection.execute("SELECT id, canonical_name FROM concept")
        }
        books = {value: key[4:] for key, value in ids.items() if key.startswith("doc_")}
        #: "<book>:<name>" -> the concept that book's run created.
        ids["concept"] = {
            f"{books[r['document_id']]}:{r['canonical_name']}": r["id"]
            for r in connection.execute(
                "SELECT c.id, c.canonical_name, min(o.document_id) AS document_id FROM concept c "
                "JOIN concept_occurrence o ON o.concept_id = c.id GROUP BY c.id")
        }
        return ids
    finally:
        connection.close()


def _direct_writes(root: pathlib.Path, ids: dict) -> None:
    """The section 7 writes no command makes (see the module docstring)."""
    path = database(root)
    connection = connect(path)
    try:
        repository = Repository(connection)
        service = ConceptService(repository)
        by_name = {}
        for concept_id, name in ids["concepts"].items():
            occurrences = queries.occurrences_for_concept(connection, concept_id)
            if occurrences and occurrences[0].document_id == ids["doc_A"]:
                by_name[name] = concept_id

        def evidence_of(sentence: str) -> Evidence:
            (row,) = [r for r in connection.execute(
                "SELECT * FROM source_occurrence WHERE original_text = ? AND document_id = ?",
                (sentence, ids["doc_A"]))]
            return Evidence(
                source_id=row["source_id"], document_id=row["document_id"], text=sentence,
                extraction_method="curated hierarchy (Phase 9 acceptance fixture)",
                segment_id=row["segment_id"], page_number=row["page_number"],
                char_start=row["char_start"], char_end=row["char_end"],
                extraction_run_id=row["extraction_run_id"],
            )

        for parent, child, sentence in (
            ("Field-effect transistor", "MOSFET", MOSFET_TYPE),
            ("Transistor", "Field-effect transistor", FET_TYPE),
        ):
            service.attach_relationship(
                relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.EXPLICIT,
                from_concept_id=by_name[parent], to_concept_id=by_name[child],
                evidence=evidence_of(sentence),
            )
        _store_before_phase_8(repository, service)
        for document_id, change in (
            (ids["doc_U"], {"authorization": Authorization.NOT_AUTHORIZED}),
            (ids["doc_G"], {"source_category": SourceCategory.AUTHORIZED_EXTERNAL_SOURCE}),
        ):
            for source in queries.sources_for_document(connection, document_id):
                repository.update(replace(source, **change))
        connection.commit()
    finally:
        connection.close()


def _store_before_phase_8(repository: Repository, service: ConceptService) -> None:
    """Book E: MOSFET's definition stored as extractor v3 stored it - its own copy of the
    object, never compared (ADR 0020 D-47) - so `merge` supersedes it (P8-19)."""
    now = utc_now()
    document = repository.add(Document(
        id=repository.new_id(Document), created_at=now, updated_at=now, filename="book-E.pdf",
        original_filename="book-E.pdf", source_type="PDF", file_path="/documents/book-E.pdf",
        file_hash="hash-book-E", file_size=1, mime_type="application/pdf", ingested_at=now,
        processing_status=DocumentProcessingStatus.PROCESSED, processing_version=1, page_count=1))
    source = repository.add(Source(
        id=repository.new_id(Source), created_at=now, updated_at=now, name="Book E",
        source_category=SourceCategory.USER_PROVIDED_SOURCE, authorization=Authorization.AUTHORIZED,
        availability=SourceAvailability.AVAILABLE, document_id=document.id, file_hash="hash-book-E"))
    text = "Book E\n" + MOSFET
    segment = repository.add(DocumentSegment(
        id=repository.new_id(DocumentSegment), created_at=now, updated_at=now,
        document_id=document.id, page_number=1, ordinal=0, text=text,
        extraction_method="pypdf", text_origin=TextOrigin.NATIVE_TEXT))
    run = repository.add(ExtractionRun(
        id=repository.new_id(ExtractionRun), created_at=now, updated_at=now,
        document_id=document.id, run_number=1, trigger=ExtractionTrigger.FIRST_EXTRACTION,
        extractor_version="3", parser_name="pypdf", started_at=now,
        status=ExtractionRunStatus.COMPLETED, completed_at=now))
    start = text.index(MOSFET)

    def evidence(said: str) -> Evidence:
        return Evidence(
            source_id=source.id, document_id=document.id, text=said,
            extraction_method="deterministic/Candidate@v3", segment_id=segment.id, page_number=1,
            char_start=start, char_end=start + len(MOSFET), extraction_run_id=run.id)

    concept = service.create_concept("MOSFET")
    service.attach_concept_provenance(concept.id, evidence("MOSFET"))
    knowledge, _ = service.attach_definition(
        concept.id, MOSFET, label="Definition: MOSFET", evidence=evidence(MOSFET))
    ev = evidence(MOSFET)
    repository.add(SourceOccurrence(
        id=repository.new_id(SourceOccurrence), created_at=now, updated_at=now,
        knowledge_id=knowledge.id, source_id=ev.source_id, document_id=ev.document_id,
        original_text=ev.text, extraction_method=ev.extraction_method, extraction_timestamp=now,
        segment_id=ev.segment_id, page_number=ev.page_number, char_start=ev.char_start,
        char_end=ev.char_end, extraction_run_id=ev.extraction_run_id))


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state(root: pathlib.Path) -> str:
    """The knowledge database's bytes with its WAL: what any writer would change."""
    db = database(root)
    wal = db.with_name(db.name + "-wal")
    return _sha256(db) + (_sha256(wal) if wal.exists() else "")


# ------------------------------------------------------------------- fixtures

#: The queries the tests read, run once each, one process per query.
QUERIES = {
    "199": ("--name", "MOSFET", "--json"),
    "199_again": ("--name", "MOSFET", "--json"),
    "199_text": ("--name", "MOSFET"),
    "plural": ("--name", "MOSFETs", "--json"),
    "transistor": ("--name", "Transistor", "--json"),
    "jfet_my_books": ("--name", "JFET", "--json"),
    "jfet_authorized": ("--name", "JFET", "--scope", "authorized", "--json"),
    "properties_only": ("--name", "MOSFET", "--knowledge-type", "property", "--json"),
    "nothing_on_page_99": ("--name", "MOSFET", "--on-page", "99", "--json"),
    "keyword_before_index": ("--keyword", "MOSFET", "--json"),
    "two_modes": ("--name", "MOSFET", "--keyword", "MOSFET"),
    "bad_scope": ("--name", "MOSFET", "--scope", "everything"),
    "empty_keyword": ("--keyword", "   "),
}


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> dict:
    """The library built one process per step, and every query answered, one process each."""
    root = tmp_path_factory.mktemp("phase9_acceptance") / "project"
    ids = build_library(root)
    ids["root_path"] = root
    by_id = {
        "in_run_c": ("--name", "MOSFET", "--in-run", ids["run_C"], "--json"),
        "exact_conflicting": (ids["k_c"], "--json"),
        "exact_document_c": (ids["doc_C"], "--json"),
        "exact_unauthorised": (ids["k_unauthorised"], "--json"),
        "page_a": ("--document", ids["doc_A"], "--page", "1", "--json"),
    }
    before = _state(root)
    answers = {}
    for key, args in {**QUERIES, **by_id}.items():
        code, out, err = cli(root, "query", *args)
        answers[key] = {"code": code, "out": out, "err": err,
                        "json": json.loads(out) if "--json" in args and code in (0, 3) else None}
    ids["answers"] = answers
    ids["unchanged"] = _state(root) == before
    ids["index_created_by_queries"] = (root / "data" / "indexes" / "index.db").exists()
    return ids


def _answer(library, key) -> dict:
    return library["answers"][key]["json"]


def _group(section, name):
    return next(g for g in section["groups"] if g["name"] == name)


def _ids(group) -> list[str]:
    return [
        item["knowledge"]["id"] if "knowledge" in item else item["links"][0]["edge"]["relationship"]["id"]
        for item in group["items"]
    ]


def _edge(group, relation_type):
    return [i for i in group["items"] if "knowledge" not in i
            and i["links"][0]["edge"]["relationship"]["relation_type"] == relation_type]


# ------------------------------------------------------------- section 199


def test_199_every_mosfet_concept_in_my_books_is_resolved_and_none_chosen(library):
    answer = _answer(library, "199")
    assert library["answers"]["199"]["code"] == 0 and answer["status"] == "FOUND"
    concept = library["concept"]
    resolved = [rc["concept"]["id"] for rc in answer["concept"]["concepts"]]
    assert resolved == [concept[f"{b}:MOSFET"] for b in "ABCDE"]  # one per book; U withheld
    assert concept["U:MOSFET"] not in resolved
    assert all(rc["matched_alias"]["normalized_alias"] == "mosfet" for rc in answer["concept"]["concepts"])
    assert "none was chosen" in answer["message"] and "withheld" in answer["message"]
    assert answer["notes"][0].startswith("Only concepts named exactly so were resolved")


def test_199_definitions_properties_applications_prerequisites(library):
    section, ids, concept = _answer(library, "199")["concept"], library, library["concept"]
    definitions = _group(section, "Definitions")
    superseded = [k for k in ids["k_mosfet_all"] if k != ids["k_mosfet"]][0]
    assert _ids(definitions) == [ids["k_mosfet"], ids["k_type"], ids["k_c"], ids["k_d"], superseded]
    shared = definitions["items"][0]  # one object, reached from books A and B, listed once
    assert [link["concept_id"] for link in shared["links"]] == [concept["A:MOSFET"], concept["B:MOSFET"]]
    assert {row["document_id"] for row in shared["evidence"]} == {ids["doc_A"], ids["doc_B"]}
    assert _ids(_group(section, "Properties")) == [ids["k_property"]]
    (prerequisite,) = _group(section, "Prerequisites")["items"]
    assert prerequisite["neighbours"][0]["canonical_name"] == "Semiconductor"
    assert prerequisite["links"][0]["concept_end"] == "TO"
    (application,) = _group(section, "Applications")["items"]
    assert application["neighbours"][0]["canonical_name"] == "Amplifier"
    assert application["links"][0]["edge"]["relationship"]["relation_type"] == "USES"
    assert _group(section, "Dependencies")["items"] == []


def test_199_relationships_keep_their_stored_direction_and_origin(library):
    relationships = _group(_answer(library, "199")["concept"], "Relationships")
    (gate_oxide,) = _edge(relationships, "USES")
    assert gate_oxide["neighbours"][0]["canonical_name"] == "Gate oxide"
    assert gate_oxide["links"][0]["concept_end"] == "TO"  # "Gate oxide USES MOSFET", not an application
    assert [i["neighbours"][0]["canonical_name"] for i in _edge(relationships, "INSTANCE_OF")] == [
        "Field-effect transistor"]
    assert [i["neighbours"][0]["canonical_name"] for i in _edge(relationships, "PARENT_OF")] == [
        "Field-effect transistor"]
    (stated,) = _edge(relationships, "EQUIVALENT_TO")
    assert stated["neighbours"][0]["canonical_name"] == "MOS transistor"
    names = {n["canonical_name"] for i in relationships["items"] if "knowledge" not in i for n in i["neighbours"]}
    assert not names & {"JFET", "Gain"}  # the distractors are not MOSFET's relationships


def test_199_rule_r1_edges_are_labelled_inferred_with_their_basis(library):
    relationships = _group(_answer(library, "199")["concept"], "Relationships")
    inferred = _edge(relationships, "RELATED_TO")
    assert inferred and all(i["links"][0]["edge"]["relationship"]["origin"] == "INFERRED" for i in inferred)
    (bjt,) = [i for i in inferred if i["neighbours"][0]["canonical_name"] == "BJT"]
    edge = bjt["links"][0]["edge"]
    assert edge["label"].startswith("INFERRED - related by definition mention (rule R1)")
    (basis,) = edge["bases"]
    assert basis["inference"]["rule"] == "R1" and basis["occurrence"]["evidence_text"] == BJT
    assert edge["evidence"] == []  # a basis, never a source statement
    assert library["k_bjt"] not in _ids(_group(_answer(library, "199")["concept"], "Definitions"))


def test_199_d1_equations_variables_and_examples_are_stated_unlinked(library):
    answer = _answer(library, "199")
    for name, kind in (("Equations", "EQUATION"), ("Variables", "VARIABLE"), ("Examples", "EXAMPLE")):
        group = _group(answer["concept"], name)
        assert group["items"] == [] and f"No stored edge links any {kind} object" in group["note"]
        assert "PARTIALLY IMPLEMENTED" in group["note"]
    assert any("PARTIALLY IMPLEMENTED" in note for note in answer["notes"])
    shown = library["answers"]["199"]["out"]
    for unlinked in ("k_equation", "k_legend", "k_example"):
        assert f'"id": "{library[unlinked]}"' not in shown


def test_199_conflict_both_claims_editions_and_no_winner(library):
    section = _answer(library, "199")["concept"]
    (conflict,) = section["conflicts"]
    assert conflict["conflict"]["cause"] == "UNDETERMINED"
    assert conflict["resolution"] == "not automatically selected"
    assert (conflict["claim_a"]["knowledge"]["statement"], conflict["claim_b"]["knowledge"]["statement"]) == (
        C_DEFINITION, D_DEFINITION)
    assert conflict["claim_a"]["evidence"] and conflict["claim_b"]["evidence"]
    editions = {d["document"]["id"]: [v["version_label"] for v in d["editions"]]
                for d in section["provenance"]["documents"]}
    assert editions[library["doc_C"]] == ["1st edition"] and editions[library["doc_D"]] == ["2nd edition"]


def test_199_a_superseded_object_stays_under_its_own_concept_with_its_pointer(library):
    section = _answer(library, "199")["concept"]
    items = {i["knowledge"]["id"]: i for i in _group(section, "Definitions")["items"]}
    superseded = [k for k in library["k_mosfet_all"] if k != library["k_mosfet"]][0]
    assert items[superseded]["knowledge"]["lifecycle_status"] == "SUPERSEDED"
    assert items[superseded]["superseded_by"] == library["k_mosfet"]
    assert [link["concept_id"] for link in items[superseded]["links"]] == [library["concept"]["E:MOSFET"]]
    canonical = items[library["k_mosfet"]]
    assert canonical["superseded_into"] == [superseded]
    assert canonical["number_of_sources"] == 3  # books A, B and E - informational (section 78)
    assert any("extractor version below 4" in note for note in _answer(library, "199")["notes"])


def test_199_d2_possible_equivalents_are_one_step_labelled_and_separate(library):
    answer = _answer(library, "199")
    possible = {p["concept"]["canonical_name"]: p for p in answer["possible_equivalents"]
                if not p["already_resolved"]}
    assert list(possible) == ["MOS transistor"]  # the one concept not itself named MOSFET
    mos = possible["MOS transistor"]
    assert mos["label"].startswith("POSSIBLE equivalent")
    assert mos["of_concept_ids"] == [library["concept"]["B:MOSFET"]]
    assert [r["basis"] for r in mos["records"]] == ["STATED_EQUIVALENT_TO"]
    assert mos["stated_edges"][0]["evidence"][0]["evidence_text"] == MOS_SAME
    assert library["k_mos"] in _ids(_group(mos["section"], "Definitions"))
    assert library["k_mos"] not in _ids(_group(answer["concept"], "Definitions"))  # never merged
    # Its own section resolves MOS transistor alone - no MOSFET concept is merged into it.
    # (This library has no second equivalence step to refuse; the one-step rule is tested
    # on a chain in tests/unit/test_query_concepts.py.)
    assert [rc["concept"]["id"] for rc in mos["section"]["concepts"]] == [library["concept"]["B:MOS transistor"]]
    assert all(p["section"] is None for p in answer["possible_equivalents"] if p["already_resolved"])
    assert library["concept"]["B:MOS transistor"] in answer["trace"]["widened"]


def test_199_unauthorised_evidence_is_never_returned(library):
    shown = library["answers"]["199"]["out"]
    answer = _answer(library, "199")
    assert "vacuum" not in shown and UNAUTHORISED not in shown
    assert library["doc_U"] not in shown
    assert all(s["authorization"] == "AUTHORIZED" and s["source_category"] == "USER_PROVIDED_SOURCE"
               for s in answer["concept"]["provenance"]["sources"])
    assert answer["withheld"]["unauthorized_evidence"] >= 1
    assert answer["withheld"]["items_without_authorized_evidence"] >= 1
    only = _answer(library, "exact_unauthorised")
    assert library["answers"]["exact_unauthorised"]["code"] == 3
    assert only["status"] == "INSUFFICIENT_AUTHORIZED_INFORMATION"
    assert only["message"].startswith("Insufficient authorized information")


def test_199_provenance_availability_and_file_presence_are_separate(library):
    provenance = _answer(library, "199")["concept"]["provenance"]
    documents = {d["document"]["id"]: d for d in provenance["documents"]}
    assert all(documents[library[f"doc_{b}"]]["preserved_file_present"] for b in "ABCD")
    assert documents[library["doc_E"]]["preserved_file_present"] is False  # never preserved a file
    assert {s["availability"] for s in provenance["sources"]} == {"AVAILABLE"}  # stored, not derived
    runs = {r["id"]: r for r in provenance["runs"]}
    assert runs[library["run_E"]]["extractor_version"] == "3" and runs[library["run_A"]]["status"] == "COMPLETED"


def test_199_trace_is_returned_never_stored_and_output_is_deterministic(library):
    first, again = library["answers"]["199"], library["answers"]["199_again"]
    assert first["out"] == again["out"]  # byte-identical, two separate processes
    trace = first["json"]["trace"]
    assert trace["persisted"] is False and trace["database"]["read_only"] is True
    assert trace["index"].startswith("not used")
    assert library["unchanged"] and not library["index_created_by_queries"]


def test_199_text_output_carries_the_same_facts(library):
    text = library["answers"]["199_text"]
    assert text["code"] == 0
    for line in ("Answer     : FOUND", "[Definitions] 5", "SUPERSEDED - stored pointer",
                 "not automatically selected", "POSSIBLE equivalent", "rule R1"):
        assert line in text["out"]


# ------------------------------------------------------- the other answers


def test_a_regular_plural_is_answered_from_its_singular_and_says_so(library):
    # One fixed English rule since grammar version 2: "MOSFETs" asks about the concept
    # "MOSFET". The substitution is stated in the answer; no other variance is folded
    # (tests/unit/test_query_concepts.py).
    plural = library["answers"]["plural"]
    assert plural["code"] == 0 and plural["json"]["status"] != "NOT_FOUND"
    assert "its singular 'mosfet' was used" in json.dumps(plural["json"])


def test_hierarchy_closures_follow_stored_parent_edges(library):
    (mosfet,) = [rc for rc in _answer(library, "199")["concept"]["concepts"]
                 if rc["concept"]["id"] == library["concept"]["A:MOSFET"]]
    assert [c["canonical_name"] for c in mosfet["ancestors"]] == ["Transistor", "Field-effect transistor"]
    (transistor,) = _answer(library, "transistor")["concept"]["concepts"]
    assert [c["canonical_name"] for c in transistor["descendants"]] == ["MOSFET", "Field-effect transistor"]


def test_scope_excludes_an_authorised_source_outside_my_books(library):
    mine, wider = library["answers"]["jfet_my_books"], library["answers"]["jfet_authorized"]
    assert mine["code"] == 3 and mine["json"]["status"] == "INSUFFICIENT_AUTHORIZED_INFORMATION"
    assert mine["json"]["withheld"]["out_of_scope_evidence"] >= 1
    assert wider["code"] == 0 and _ids(_group(wider["json"]["concept"], "Definitions")) == [library["k_jfet"]]


def test_filters_remove_otherwise_authorised_candidates(library):
    properties = _answer(library, "properties_only")["concept"]
    assert _group(properties, "Definitions")["items"] == []
    assert _ids(_group(properties, "Properties")) == [library["k_property"]]
    only_c = _answer(library, "in_run_c")
    assert [rc["concept"]["id"] for rc in only_c["concept"]["concepts"]] == [library["concept"]["C:MOSFET"]]
    nothing = library["answers"]["nothing_on_page_99"]
    assert nothing["code"] == 3 and nothing["json"]["status"] == "NOT_FOUND"
    assert "filters" in nothing["json"]["message"]


def test_exact_and_page_modes(library):
    conflicting = _answer(library, "exact_conflicting")["exact"]
    assert conflicting["knowledge"]["knowledge"]["statement"] == C_DEFINITION
    assert conflicting["conflicts"][0]["resolution"] == "not automatically selected"
    document = _answer(library, "exact_document_c")["exact"]
    assert [v["version_label"] for v in document["document"]["editions"]] == ["1st edition"]
    page = _answer(library, "page_a")["page"]
    assert MOSFET in page["segments"][0]["text"]
    starts = [o["evidence"]["char_start"] for o in page["occurrences"] if o["evidence"]["char_start"] is not None]
    assert starts == sorted(starts)


def test_invalid_requests_are_refused_with_exit_code_2(library):
    for key in ("two_modes", "bad_scope", "empty_keyword"):
        assert library["answers"][key]["code"] == 2, library["answers"][key]["err"]


# ------------------------------------------------ the index lifecycle (keyword)


def test_keyword_search_through_the_index_lifecycle(library, tmp_path):
    """Before any index: refused. After `index`: works. After a later extraction:
    refused as stale until rebuilt. Every other mode works throughout. On a copy of the
    library, so the other tests' store is untouched."""
    import shutil

    root = tmp_path / "project"
    shutil.copytree(library["root_path"], root)
    index = root / "data" / "indexes" / "index.db"
    before = library["answers"]["keyword_before_index"]
    assert before["code"] == 5 and "MISSING" in before["err"] and not index.exists()

    code, out, err = cli(root, "index", "--json")
    assert code == 0, err
    built = json.loads(out)
    assert built["index"]["status"]["state"] == "FRESH" and index.exists()
    ingestion = {d["document_id"]: d for d in built["documents"]}
    assert ingestion[library["doc_A"]]["fully_ingested"] is True
    assert ingestion[library["doc_E"]]["fully_ingested"] is False  # extractor v3 only
    state = _state(root)

    code, out, _ = cli(root, "query", "--keyword", "V_GS", "--json")
    hits = json.loads(out)["keyword"]["knowledge"]
    assert code == 0 and [h["knowledge"]["id"] for h in hits] == [library["k_equation"]]
    assert hits[0]["label"].startswith("KEYWORD HIT")  # D1: reachable only as a keyword hit
    code, out, _ = cli(root, "query", "--keyword", "threshold voltage", "--json")
    found = {h["knowledge"]["id"] for h in json.loads(out)["keyword"]["knowledge"]}
    assert code == 0 and found == {library["k_legend"], library["k_example"], library["k_c"], library["k_d"]}
    code, out, _ = cli(root, "query", "--keyword", "vacuum", "--json")
    assert code == 3 and json.loads(out)["status"] == "INSUFFICIENT_AUTHORIZED_INFORMATION"
    for term in ("NOT mosfet", 'mosfet" OR "diode', "body:mosfet"):
        first = cli(root, "query", "--keyword", term, "--json")[:2]  # stderr carries log timestamps
        assert first[0] == 3 and first == cli(root, "query", "--keyword", term, "--json")[:2]
    assert _state(root) == state  # keyword queries write nothing

    extract(root, "V")  # a later extraction changes knowledge.db
    code, _, err = cli(root, "query", "--keyword", "MOSFET")
    assert code == 5 and "STALE" in err
    for args in (("--name", "MOSFET"), (library["k_c"],), ("--document", library["doc_A"], "--page", "1")):
        assert cli(root, "query", *args)[0] == 0
    code, out, _ = cli(root, "query", "--name", "MOSFETs", "--json")
    assert code == 0
    assert [rc["concept"]["canonical_name"] for rc in json.loads(out)["concept"]["concepts"]] == ["MOSFETs"]
    code, out, _ = cli(root, "query", "--name", "MOSFET", "--json")
    assert "MOSFETs" not in {rc["concept"]["canonical_name"] for rc in json.loads(out)["concept"]["concepts"]}

    assert cli(root, "index")[0] == 0
    code, out, _ = cli(root, "query", "--keyword", "insulated gate", "--json")
    assert code == 0 and [h["knowledge"]["statement"] for h in json.loads(out)["keyword"]["knowledge"]] == [PLURAL]
