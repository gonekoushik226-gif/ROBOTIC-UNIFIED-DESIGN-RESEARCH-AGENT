# RUDRA 1.2.0 — Manual Knowledge-Base Test Report

- **Run date:** 2026-10-03
- **Application found on the laptop:** RUDRA 1.1.0
- **Knowledge base:** 80 PDFs in the installed user's RUDRA data folder
**Build tested:** RUDRA 1.2.0 CLI and desktop package, using that existing knowledge base

## Scope and method

I checked the installed inventory, selected questions across the subjects represented by the
PDF collection, and asked them through RUDRA's normal `Ask` pipeline. After changing the
question interpreter, I asked the affected MOSFET and application questions again using the
1.2.0 build. Answers were checked for status and document/page citations. This was a
representative cross-subject sample; it did not test every one of the 80 PDFs or every page.

Topic selection followed the listed titles and published contents, including MOSFETs in
Sedra and Smith's *Microelectronic Circuits*, sampling in Oppenheim and Willsky's *Signals
and Systems*, and root-locus/PID topics in Ogata's *Modern Control Engineering*.

## Results

| Subject area | Questions tried | Observed result |
|---|---|---|
| Electronic devices | “What is a MOSFET?”, “Explain how a MOSFET works” | Both answered from the stored device notes; three cited records. The latter phrasing was fixed in 1.2.0 to resolve the topic name correctly. |
| Analog/network circuits | “What is capacitance?”, “What is Thevenin's theorem?” | Capacitance returned direct definitions with five sources. Thevenin returned relevant mentions, but no recognized direct definition. |
| Signals and systems | “What is Nyquist rate?”, “What is convolution?” | Both returned answers with citations (five and two sources respectively). |
| Control systems | “What is root locus?”, “How does a PID controller work?” | Root locus returned a direct definition. PID returned mention-level/question-bank material rather than a useful explanation. |
| Communications | “What is channel capacity?”, “What is Shannon capacity?” | Channel capacity returned a cited definition. Shannon capacity produced mentions rather than a clear direct definition. |
| Electromagnetics | “What is the Poynting vector?” | Answered with three cited source records. |
| Probability and mathematics | “What is conditional probability?”, “What is an eigenvalue?” | Answered with two and one cited source records respectively. |
| Digital logic | “What is a flip-flop?” | Answered with five cited source records. |
| Physics | “What is Newton's second law?” | Returned mentions, including noisy extracted text, rather than a clean definition. |
| Programming and HDL | “How does a Python for loop work?”, “What is a Verilog module?” | Python loop was not found as a stored concept. Verilog module returned page-text mentions, not a stored explanation. |
| Engineering drawing | “What is a section view in engineering drawing?” | No answer was found in the stored knowledge. |
| Aptitude/vocabulary | “What is a synonym of ubiquitous?” | No answer; the request was treated as an exact concept name. |
| Applications | “How is a MOSFET used?”, “What are applications of a MOSFET?” | The database has no stored application link for MOSFET. RUDRA now says that clearly and labels matching text as mentions. |
| Calculation | “Calculate I when V = 12 V and R = 3 ohms.” | Correctly calculated **I = 4 A** and reported an independent check. Asking for “the current” without symbol `I` did not resolve the quantity reliably from the extracted knowledge. |

## Changes in 1.2.0

- Added four topic-based patterns on the Ask page: **What is it?**, **How does it work?**,
  **How is it used?** and **What are its applications?** A user chooses one and enters a
  topic; RUDRA fills in the question and uses the ordinary answer and source flow.
- Advanced the request grammar to version 3 and added deterministic handling for “Explain how … works”, “State … law”, “What does … state?”,
  applications questions and “How is … used?”
- Ensured missing application links are reported as missing; text matches remain identified
  as mentions, not asserted applications.
- Updated application version, package metadata, README, changelog and release notes.

## Build checks

- Full project test suite on the final source: **2,535 passed, 57 skipped**.
- Packaged CLI and desktop self-tests passed, including a template-generated question and
  sourced answer.
- Windows installer built as `RUDRA-Setup-1.2.0-win64.exe`; SHA-256 verified against
  `dist/installer/SHA256SUMS.txt`:
  `4a2d534db2cc3e2448161b9e8fd465ef1d3544277226c3ac85427119804cfb31`.
- The installer install/upgrade test was not run on this laptop: its safety check refuses to
  start when RUDRA is already installed for the current Windows user. The existing 1.1.0
  installation and its data were left in place. The CI release workflow runs that installer
  test on a clean Windows runner.

## Remaining gaps seen in this sample

The questions that returned only mentions, or no stored knowledge, expose extraction and
retrieval limits in the existing library. In particular, application relationships are not
stored for MOSFET, and some natural quantity names do not map to equation symbols. The new
question patterns improve how users express a request; they do not add facts that the PDFs'
stored knowledge does not contain.

## Book-content references used to choose topics

- [Oxford University Press — *Microelectronic Circuits*](https://www.oupjapan.co.jp/en/node/4875?language=en)
- [Pearson — *Signals and Systems*](https://www.pearson.com/en-us/subject-catalog/p/Oppenheim-Signals-and-Systems-2nd-Edition/P200000003155/9780138147570)
- [Pearson — *Modern Control Engineering*](https://www.pearson.com/en-ca/subject-catalog/p/modern-control-engineering/P200000003521/9780136156734)
