"""The desktop window's self-test (ADR 0057): the real window, driven through its own forms.

    RUDRA.exe --self-test REPORT.json --project-root SCRATCH [--self-test-pdf PDF]

`windows/build.py` runs it on the packaged program. It opens the full interface on a
scratch project the caller chose and runs representative workflows exactly as clicks
would - each page's own Run handler, the compact assistant's input, the Help page's
documents - checking each command line's exit code and output. It writes a JSON report
and closes the window. Approvals are answered yes: the project is the caller's scratch
folder, never the default project (`launch.main` refuses the self-test without
--project-root).
"""

from __future__ import annotations

import json
import os
import sys
import time
import tkinter as tk
import urllib.error
import zipfile
from collections.abc import Callable
from pathlib import Path

from app.storage.archive import export_knowledge, inspect_backup, restore_knowledge
from app.ui.gui.commands import CommandResult
from app.ui.gui.window import RudraWindow
from app.updates import UpdateChecker, UpdateStatus
from app.version import EDITION, VERSION

TIMEOUT_MS = 240_000

Check = Callable[[CommandResult], tuple[bool, str]]
Step = tuple[str, Callable[[], object], Check | None]

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def sample_docx(path: Path) -> Path:
    """A small Word document with a definition and an Office Math fraction."""
    body = ('<w:p><w:r><w:t>Inductance is defined as the ratio of flux linkage to current.</w:t></w:r></w:p>'
            '<w:p><m:oMathPara><m:oMath><m:r><m:t>L = </m:t></m:r><m:f><m:num><m:r><m:t>N\u03a6</m:t></m:r>'
            '</m:num><m:den><m:r><m:t>I</m:t></m:r></m:den></m:f></m:oMath></m:oMathPara></w:p>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
                                                '/package/2006/content-types"/>')
        archive.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}" '
                                              f'xmlns:m="{_M}"><w:body>{body}</w:body></w:document>')
    return path


def sample_equation_pdf(path: Path) -> Path:
    """One PDF page with a displayed fraction drawn as a typesetter draws it: glyphs and a bar."""
    stream = (b"BT /F1 11 Tf 72 720 Td (The bandwidth of a series circuit is) Tj ET\n"
              b"BT /F2 12 Tf 250 680 Td (BW) Tj ET\nBT /F1 12 Tf 272 680 Td (=) Tj ET\n"
              b"BT /F2 12 Tf 290 688 Td (R) Tj ET\n287 683.75 14 0.5 re f\nBT /F2 12 Tf 290 670 Td (L) Tj ET")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R /F2 5 0 R "
               b">> >> /Contents 6 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Italic >>",
               b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(data))
        data += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    data += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(data))
    return path


def _contains(*needles: str) -> Check:
    def check(result: CommandResult) -> tuple[bool, str]:
        missing = [needle for needle in needles if needle not in result.stdout]
        ok = result.ok and not missing
        return ok, "found " + ", ".join(repr(n) for n in needles) if ok else \
            f"exit {result.exit_code} ({result.meaning}); missing {missing}; {result.stderr.strip()[-300:]}"
    return check


def steps(window: RudraWindow, pdf: Path | None) -> list[Step]:
    """The workflows, in order. A step without a check runs no command and returns (ok, detail)."""
    pages = window.full.pages
    root = window.root

    def window_check() -> tuple[bool, str]:
        ok = root.title() == "RUDRA" and window.icon_set and window.mark(84) is not None
        return ok, f"title {root.title()!r}, icon set {window.icon_set}, mark image loaded"

    def startup_check(result: CommandResult) -> tuple[bool, str]:
        ok, detail = _contains("Startup complete.")(result)
        compact = window.compact.output.text_content()
        return ok and "RUDRA is ready." in compact, detail + "; compact summary shown"

    def compact(text: str) -> Callable[[], object]:
        def start() -> object:
            window.show(full=False)
            window.compact.entry.delete(0, "end")
            window.compact.entry.insert(0, text)
            return window.compact.send()
        return start

    def form_error() -> tuple[bool, str]:
        window.show(full=True)
        pages["provenance"].identifier.set("")
        started = pages["provenance"].run()
        shown = window.full.output.text_content()
        return not started and "Type an identifier" in shown, "an empty form is refused before any command runs"

    def fill(page: str, **values: str) -> Callable[[], object]:
        def start() -> object:
            window.show(full=True)
            target = pages[page]
            for name, value in values.items():
                getattr(target, name).set(value)
            return target.run()
        return start

    def documents() -> tuple[bool, str]:
        pages["help"].document("README")
        readme = window.full.output.text_content()
        pages["help"].document("Limitations")
        limitations = window.full.output.text_content()
        shown = "\n# RUDRA\n" in readme and "View Sources" in readme and "Limitations" in limitations
        return shown, "README.md and docs/LIMITATIONS.md shown"

    def settle() -> None:
        deadline = time.monotonic() + 120
        while window.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)

    def answer_only() -> tuple[bool, str]:
        shown = window.full.output.text_content()
        hidden = [marker for marker in ("DOC-", "K-0", "CPT-") if marker in shown]
        ok = "Definition: Resistance is defined" in shown and not hidden and bool(window.full.output.source_buttons)
        return ok, "the answer is shown alone, with View Sources" if ok else f"shown: {shown[:200]!r}"

    def view_sources() -> tuple[bool, str]:
        output = window.full.output
        next(iter(output.source_buttons.values())).invoke()
        settle()
        shown = output.text_content()
        ok = "DOC-00000001 p.1" in shown and "Verification: VERIFIED" in shown
        return ok, "View Sources shows the document, page and verified provenance" if ok else shown[-300:]

    def formula() -> tuple[bool, str]:
        canvas = window.full.output.formula("d/dx(x^n) = n x^(n-1)")
        texts = "".join(canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text")
        lines = [item for item in canvas.find_all() if canvas.type(item) == "line"]
        ok = bool(lines) and "^" not in texts and "/" not in texts and "d" in texts
        return ok, f"typeset with {len(lines)} rule(s), glyphs {texts!r}"

    def backup_round_trip() -> tuple[bool, str]:
        folder = window.project_root.parent / "selftest-backup"
        report = export_knowledge(window.layout(), folder / "kb", app_version=VERSION)
        contents = inspect_backup(report.path)
        second = RudraWindow(root, window.project_root.parent / "selftest-restored", full=True, autostart=False)
        restored = restore_knowledge(second.layout(), report.path)
        second.root.update()
        ok = restored.verified.get("integrity") == "ok" and restored.verified.get("files_verified", 0) >= 1
        return ok, (f"exported {contents.document_count} document(s), {report.size} bytes; restored: "
                    f"{restored.verified}")

    def complex_formulas() -> tuple[bool, str]:
        drawn = []
        for source in (r"A = \begin{pmatrix} a & b \\ c & d \end{pmatrix}",
                       r"f(x) = \begin{cases} x, & x \ge 0 \\ -x, & x < 0 \end{cases}",
                       r"\begin{aligned} y &= (a+b)^{2} \\ &= a^{2} + 2ab + b^{2} \end{aligned}",
                       r"Z = \frac{1}{\frac{1}{a} + b} \tag{2.1}", r"W = \int_{0}^{T} p \, dt"):
            canvas = window.full.output.formula(source)
            texts = "".join(canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text")
            drawn.append(bool(texts) and "\\" not in texts and "begin" not in texts)
        return all(drawn), f"{sum(drawn)} of {len(drawn)} complex formulas typeset"

    def windows_ocr() -> tuple[bool, str]:
        from app.documents import ocr

        state = ocr.status(refresh=True)
        if not state.available:
            return True, f"Windows OCR is not available on this machine ({state.reason}); imports go on without it"
        image = ocr.render_pdf_page(pdf, 1, window.project_root.parent / "selftest-ocr.png", scale=2.0)
        text = " ".join(page.text for page in ocr.recognize_image(image))
        return "Resistance" in text, f"recognized {len(text)} characters with {', '.join(state.languages)}"

    def ai_local_only() -> tuple[bool, str]:
        settings_file = window.project_root / "config" / "ai.json"
        ok = not window.ai_active() and not settings_file.exists() and "ai" in pages
        return ok, "external AI is off until the user enables it; nothing is configured"

    def offline_update_check() -> tuple[bool, str]:
        def offline(request, timeout):
            raise urllib.error.URLError("offline")

        checker = UpdateChecker(window.project_root / "config" / "updates-selftest.json", fetch=offline)
        info = checker.check(manual=True)
        if os.environ.get("RUDRA_SELFTEST_NO_NETWORK") == "1":
            # The caller cut this process off from the network: the real request must fail quietly.
            real = UpdateChecker(window.project_root / "config" / "updates-selftest-real.json").check(manual=True)
            ok = info.status is real.status is UpdateStatus.UNAVAILABLE
            return ok, f"{info.message()} Real request without network: {real.status} ({real.reason})"
        return info.status is UpdateStatus.UNAVAILABLE, info.message()

    def calculate_through_ask() -> tuple[bool, str]:
        """No standalone Calculate page: the same calculation, asked for naturally."""
        window.show(full=True)
        pages["ask"].question.set("Calculate I given I = V / Rtotal, Rtotal = R1 + R2, R1 = 10 Ω, R2 = 20 Ω, "
                                  "V = 10 V")
        return pages["ask"].run()

    def import_rendered_plainly() -> tuple[bool, str]:
        # "Added to your knowledge base" alone, or "...with some extraction warnings" -
        # either is correct; this fixture is expected to raise a couple of warnings.
        shown = window.full.output.text_content()
        ok = "Added to your knowledge base" in shown and pdf.name in shown and "Status     :" not in shown
        window.full.output.toggle_extract_details()
        detailed = window.full.output.text_content()
        window.full.output.toggle_extract_details()
        ok = ok and "Status     : COMPLETED" in detailed
        return ok, shown[:200] + " | details: " + detailed[:200]

    def knowledge_page() -> tuple[bool, str]:
        """The Knowledge page lists what the import stored, with each document's result."""
        window.show(full=True)
        view.select("knowledge")
        settle()
        page = pages["knowledge"]
        page.kind.set("Equations")
        page.load()
        settle()
        listed = [page.tree.item(row, "text") for row in page.tree.get_children()]
        ok = bool(listed) and any("R1 + R2" in text for text in listed) and not page_leaks(window)
        page.kind.set("Documents")
        page.load()
        settle()
        documents = page.tree.get_children()
        view.select("ask")  # leave the page: showing the window again would re-read the inventory
        return ok and bool(documents), f"{len(listed)} equation(s) listed; {len(documents)} document row(s)"

    def page_leaks(win) -> list[str]:
        """Development vocabulary anywhere in the main pages' text (it must never be there)."""
        import re

        words = re.compile(r"Phase\s*\d|\bADR\b|\bAPI-\d|\bP\d+-\d+\b")
        found = []
        for key in ("ask", "import", "knowledge", "settings"):
            page = win.full.pages[key]
            texts = [page.heading, page.description]
            stack = [page.frame]
            while stack:
                widget = stack.pop()
                stack.extend(widget.winfo_children())
                if "text" in widget.keys():  # a widget without a text option has no words to check
                    texts.append(str(widget.cget("text")))
            found += [t for t in texts if words.search(t)]
        return found

    def chosen_equations() -> tuple[bool, str]:
        """RUDRA picks and chains the stored equations itself: no formula in the question."""
        window.show(full=True)
        pages["ask"].question.set("Calculate I when V = 10 V, R1 = 10 ohms and R2 = 20 ohms")
        return pages["ask"].run()

    def worked_solution() -> tuple[bool, str]:
        """The calculation just run is shown as a worked solution: typeset, not as linear text."""
        output = window.full.output
        sources = [widget.formula_source for widget in output.embedded if isinstance(widget, tk.Canvas)]
        shown = output.text_content()
        result = next((source for source in sources if source.startswith("I ≈ 0.333333")), "")
        fractions = [source for source in sources if r"\frac{" in source and r"\mathrm{" in source]
        ok = bool(result) and bool(fractions) and "÷" not in shown and "→" not in shown and "\\frac" not in shown
        return ok, f"{len(sources)} typeset formulas; result {result!r}; {len(fractions)} with stacked fractions and units"

    def voice_hidden() -> tuple[bool, str]:
        """The speech engine's helper process must have no console window of its own."""
        import subprocess as sp

        from app.voice import speech

        options = speech._hidden_process()
        ok = bool(options["creationflags"] & sp.CREATE_NO_WINDOW) and options["startupinfo"].wShowWindow == sp.SW_HIDE
        return ok, "speech recognition starts with no console window"

    def settings_page() -> tuple[bool, str]:
        """Settings reaches backup, AI and update checks without a separate page for each."""
        window.show(full=True)
        view.select("settings")
        page = pages["settings"]
        ok = ("ai" not in view.nav and "backup" not in view.nav and page.ai_status.cget("text").startswith("Off"))
        return ok, page.ai_status.cget("text")

    view = window.full
    plan: list[Step] = [
        ("window", window_check, None),
        ("startup", lambda: pages["status"].run("start"), startup_check),
        ("version", lambda: pages["status"].run("version"), _contains(EDITION)),
        ("calculate through ask", calculate_through_ask, _contains('"status": "CALCULATED"', '"displayed": "0.333333"')),
        ("assistant command", compact("/version"), _contains(EDITION)),
        ("form check", form_error, None),
        ("update check offline", offline_update_check, None),
        ("settings page", settings_page, None),
    ]
    if pdf is not None:
        plan += [
                ("import", fill("import", pdf=str(pdf)), _contains("\nStatus     : COMPLETED")),
            ("import rendered plainly", import_rendered_plainly, None),
            ("lookup", fill("lookup", mode="name", value="Resistance"), _contains("CPT-00000001")),
            ("provenance", fill("provenance", identifier="K-00000001"), _contains("Verification: VERIFIED")),
            ("ask", fill("ask", question="What is resistance?"), _contains("ANSWERED")),
            ("answer only", answer_only, None),
            ("view sources", view_sources, None),
            ("formula", formula, None),
            ("rudra chooses the equations", chosen_equations, _contains('"status": "ANSWERED"', "I ≈ 0.333333 A", "R1 + R2")),
            ("calculation typeset", worked_solution, None),
            ("knowledge page", knowledge_page, None),
            ("voice runs hidden", voice_hidden, None),
            ("backup", backup_round_trip, None),
            ("assistant question", compact("What is resistance?"), _contains("ANSWERED")),
            ("word document", fill("import", pdf=str(sample_docx(pdf.parent / "inductance.docx"))),
             _contains(", DOCX,", "Status     : COMPLETED")),
            ("word question", fill("ask", question="What is inductance?"), _contains("ANSWERED", "flux linkage")),
            ("pdf equation layout", fill("import", pdf=str(sample_equation_pdf(pdf.parent / "bandwidth.pdf"))),
             _contains("1 certain (stored in structured form)")),
            ("complex formulas", complex_formulas, None),
            ("windows ocr", windows_ocr, None),
            ("ai local only", ai_local_only, None),
        ]
    plan += [
        ("command reference", lambda: (window.show(full=True), pages["help"].reference())[1], _contains("usage:")),
        ("documents", documents, None),
    ]
    return plan


def run(report_path: Path, project_root: Path, pdf: Path | None) -> int:
    """Run every step in the real window; 0 when all passed."""
    root = tk.Tk()
    window = RudraWindow(root, project_root, full=True, autostart=False)
    window.confirm = lambda approval: True
    plan = steps(window, pdf)
    checks: dict[str, dict] = {}
    state: dict[str, object] = {"next": 0, "pending": None, "done": False}

    def record(name: str, ok: bool, detail: str, result: CommandResult | None = None) -> None:
        entry: dict[str, object] = {"ok": bool(ok), "detail": detail}
        if result is not None:
            entry.update(argv=list(result.argv), exit_code=result.exit_code, seconds=round(result.seconds, 3))
        checks[name] = entry

    def advance() -> None:
        index = int(state["next"])  # type: ignore[arg-type]
        if index >= len(plan):
            finish()
            return
        state["next"] = index + 1
        name, start, check = plan[index]
        if check is None:
            try:
                ok, detail = start()  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 - a failed step is recorded, and the test goes on
                ok, detail = False, repr(exc)
            record(name, ok, detail)
            root.after(50, advance)
            return
        state["pending"] = (name, check)
        if not start():
            state["pending"] = None
            record(name, False, f"the window did not start the command (busy={window.busy}: {window.running})")
            root.after(50, advance)

    def on_result(result: CommandResult) -> None:
        pending = state["pending"]
        if pending is None:
            return
        state["pending"] = None
        name, check = pending  # type: ignore[misc]
        ok, detail = check(result)
        record(name, ok, detail, result)
        root.after(50, advance)

    def finish() -> None:
        if state["done"]:
            return
        state["done"] = True
        report = {
            "passed": bool(checks) and all(entry["ok"] for entry in checks.values()),
            "checks": checks,
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
            "project_root": str(project_root),
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        root.destroy()

    def timeout() -> None:
        record("timeout", False, f"the self-test did not finish within {TIMEOUT_MS // 1000} s")
        finish()

    window.on_result = on_result
    root.after(300, advance)
    root.after(TIMEOUT_MS, timeout)
    root.mainloop()
    return 0 if json.loads(report_path.read_text(encoding="utf-8"))["passed"] else 1
