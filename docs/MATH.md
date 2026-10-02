# Mathematics in RUDRA

## The stored form

Every equation is stored as text in one linear notation, a subset of LaTeX. It is
searchable, comparable and exact, and it is what provenance quotes and backups carry. The
typeset picture is drawn from it on screen; no image is ever stored in its place.

| Structure | Notation |
|---|---|
| Fraction, nested fraction | `\frac{a}{b}`, `\frac{1}{\frac{1}{a} + b}` |
| Power, subscript | `x^{2}`, `V_{th}`, `a_{i}^{2}` |
| Radical | `\sqrt{x}`, `\sqrt[3]{x}` |
| Integral, sum, product with limits | `\int_{0}^{T} p\,dt`, `\sum_{i=1}^{n} x_{i}` |
| Derivatives | `\frac{dv}{dt}`, `\frac{\partial f}{\partial x}` |
| Matrices | `\begin{pmatrix} a & b \\ c & d \end{pmatrix}` (also `bmatrix`, `vmatrix`, `Vmatrix`, `Bmatrix`) |
| Piecewise | `\begin{cases} x & x \ge 0 \\ -x & x < 0 \end{cases}` |
| Aligned derivation | `\begin{aligned} y &= (a+b)^{2} \\ &= a^{2} + 2ab + b^{2} \end{aligned}` |
| Equation number | `V = IR \tag{2.13}` |
| Accents, binomials | `\hat{x}`, `\vec{v}`, `\bar{x}`, `\binom{n}{k}` |
| Greek letters and symbols | `\alpha … \omega`, `\le`, `\approx`, `\infty`, `\partial`, … (Unicode letters are kept as they are) |

Plain notation typed by a user (`a/b`, `x^2`, `sqrt(x)`, `sin^-1 x`) is displayed too.

## Where equations come from

- **Word and PowerPoint** store equations as Office Math, and **web pages and EPUB** as
  MathML. Both are trees — a fraction *is* a numerator and a denominator — so they are
  converted exactly and stored as the source's statement.
- **PDF** has no mathematical structure, only glyphs placed on a page. RUDRA reads where
  each glyph run is (its baseline, extent and size) and the thin rules the page draws,
  and rebuilds **display equations** from that geometry:
  - a fraction — a horizontal rule with glyphs above and below it, inside its width;
  - a superscript or subscript — a smaller glyph raised or lowered right after its base;
  - limits — glyphs centred above or below a big operator, or set as its scripts;
  - a radical — a √ with a bar starting at its right edge, over the radicand;
  - a matrix or cases — tall delimiters around rows whose cells line up in columns;
  - an aligned derivation — lines that begin with their relation sign, aligned under the
    one above;
  - an equation number — a parenthesised number set well apart at the right.

### Certain and uncertain PDF equations

A rebuilt equation is **certain** only when every glyph in its area was placed by these
rules and the page's plain text agrees about where the equation begins and ends. It then
replaces its flattened text in the stored page and is stored as the source's statement.
An equation with no hidden structure at all (every glyph on one baseline, nothing above,
below or beside it) is also certain; its text is kept exactly.

When anything is doubtful — a glyph that fits no structure, a rule with glyphs on one side
only, a radical without a bar, matrix cells that do not line up, glyphs printed over one
another, a glyph the font could not decode — the page's text is kept **exactly as it
was**, and the equation is stored as **uncertain** with its tentative structure. Its
answer says why, and **View Sources → Open source document** shows the printed page.

For every PDF page with equations, RUDRA keeps a record beside the document
(`data\extracted\<document hash>\page-N.math.json`): the rebuilt form, the page's
original text for it, whether it was certain, the structural decisions with their
coordinates, and the reasons for any doubt. It travels with knowledge-base backups.

Mathematics inside a sentence is left as the PDF's text layer gives it (uncertain), and
equations inside scanned images are not reconstructed.

## Display

The display lays each equation out as a textbook would — stacked fractions with a rule,
raised and lowered scripts, radicals over their radicand, big operators with limits,
matrices and cases in their delimiters, aligned lines, the equation number at the right —
and breaks a long equation across lines to fit the answer. It uses fonts that ship with
Windows.

### A calculation as a worked solution

A calculation is not shown as a line of linear text. For `solve` and `calculate` answers
the window sets it out as a textbook worked example:

- **Result** — the quantity and its value on a card of its own, with the unit upright
  (*I = 0.4 A*). A rounded value says so (*≈*, "rounded to 6 significant figures; the
  exact value is 1/3").
- **Given** — each value you supplied, as an equation (*V = 12 V*, *R₁ = 10 Ω*), with
  the name your documents give the quantity.
- **Working** — for each step, the equation as used (*I = V ⁄ R*, as a stacked fraction),
  when it was turned around the stored equation it came from, and the same equation with
  its values put in, ending in the value the step gave:

      I = 12 V / 30 Ω = 0.4 A        (the fraction stacked, units upright)

- **A check line** — "Checked: an independent evaluation reproduced every step", or a
  plain warning when it did not.

Subscripted symbols are set as such (*R*<sub>total</sub>, *V*<sub>out</sub>); large
numbers are grouped in threes (*20 000 Ω*) and powers of ten written as powers
(*1.5 × 10⁷ Hz*). This is presentation only: every symbol, number and unit is the one the
command returned, nothing is recomputed or re-rounded, and the plain lines of the same
answer are unchanged in the command line, in the JSON and in **Copy**. A substitution the
display does not recognise is shown as the command wrote it.

LaTeX written in the middle of any answer — a quotation from your Markdown or LaTeX
sources — is typeset where it stands: `\( … \)`, `\[ … \]`, `$$ … $$` and `$ … $` are
never shown as delimiters (a dollar amount such as "$5 and $6" is left alone), and a
formula in a sentence that is not fenced (*"where E = \frac{1}{2} m v^{2} is the
energy"*) is found and typeset without the words around it. **View Sources** shows a stored
equation typeset; a quotation from the document is shown exactly as the document has it,
and when it is written in LaTeX the typeset form follows it.

The display is RUDRA's own typesetter (`app/ui/gui/mathrender.py`, standard library only,
drawn on a Tk canvas with Cambria Math from Windows). The open-source alternatives that
were considered for it - and why none was adopted - are in the "Open-source review" in
[DESIGN.md](DESIGN.md).
