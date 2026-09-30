"""Exact numbers and their display (ADR 0042 P11-13, P11-14; decision N6).

Every value is an exact rational (`fractions.Fraction`); binary floating point is never
used. A numeric literal is read exactly from its decimal text: `0.1` is exactly 1/10,
and `1e-3` exactly 1/1000.

**Display.** A value whose exact decimal representation terminates within six
significant digits is shown exactly (relation `=`). Any other value is rounded to six
significant figures, round-half-even, and shown with `≈`. Section 203's expected output
follows: 30 is shown as `30` and 1/3 as `0.333333`. Six significant figures, not six
decimal places, because significant figures survive an SI prefix: 0.333333 A is
333.333 mA. The rounding mode is not fixed by any record. Round-half-even is Python's
`decimal` default and was chosen under the standing implementation rule.

Values between 1e-4 (inclusive) and 1e6 (exclusive) in magnitude are written in
positional notation; others in scientific notation (`1e-12`, `1.23457e6`). A rounded
value keeps all six significant digits (`2.50000`), because they state its precision.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal
from fractions import Fraction

#: Significant figures kept when a value is rounded for display (ADR 0042 P11-14).
SIGNIFICANT_FIGURES = 6

#: The display rule, stated in every returned trace (section 9: precision/rounding).
DISPLAY_RULE = (
    f"exact when the value terminates within {SIGNIFICANT_FIGURES} significant digits; "
    f"otherwise {SIGNIFICANT_FIGURES} significant figures, round-half-even"
)

#: An unsigned decimal literal in ASCII digits: digits, an optional fraction, an optional
#: exponent. Nothing else is a number: no hexadecimal, no underscores, no `nan` or `inf`.
NUMBER_PATTERN = r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
_NUMBER = re.compile(rf"[-−]?{NUMBER_PATTERN}")

_ROUNDING = Context(prec=SIGNIFICANT_FIGURES, rounding=ROUND_HALF_EVEN)

#: Positional notation is used for magnitudes 1e-4 <= |x| < 1e6.
_POSITIONAL = range(-4, 6)


class NumberError(ValueError):
    """Text that is not a decimal literal."""


def parse_number(text: str) -> Fraction:
    """The exact value of a decimal literal, with an optional sign (`-` or `−`).

    `0.1` is exactly 1/10. Raises `NumberError` for anything else, so that nothing is
    ever guessed from malformed text.
    """
    if not isinstance(text, str) or _NUMBER.fullmatch(text) is None:
        raise NumberError(f"{text!r} is not a decimal number.")
    return Fraction(text.replace("−", "-"))


@dataclass(frozen=True, slots=True)
class Displayed:
    """A value as shown: its text, and whether that text is exact (`=`) or rounded (`≈`)."""

    text: str
    exact: bool

    @property
    def relation(self) -> str:
        return "=" if self.exact else "≈"


def display(value: Fraction | int) -> Displayed:
    """Show an exact value by the rule in this module's docstring."""
    value = Fraction(value)
    exact = _exact_decimal(value)
    if exact is not None:
        normalized = exact.normalize()
        if len(normalized.as_tuple().digits) <= SIGNIFICANT_FIGURES:
            return Displayed(_format(normalized), exact=True)
    rounded = _ROUNDING.divide(Decimal(value.numerator), Decimal(value.denominator))
    return Displayed(_format(rounded), exact=False)


def _exact_decimal(value: Fraction) -> Decimal | None:
    """The exact decimal of a value whose denominator has no prime factor but 2 and 5."""
    denominator, twos, fives = value.denominator, 0, 0
    while denominator % 2 == 0:
        denominator, twos = denominator // 2, twos + 1
    while denominator % 5 == 0:
        denominator, fives = denominator // 5, fives + 1
    if denominator != 1:
        return None
    places = max(twos, fives)
    scaled = value.numerator * (10**places // value.denominator)
    return Decimal(scaled).scaleb(-places)


def _format(number: Decimal) -> str:
    """Positional notation for 1e-4 <= |x| < 1e6, scientific otherwise; zero is `0`."""
    if number.is_zero():
        return "0"
    sign, digits, _ = number.as_tuple()
    magnitude = number.adjusted()
    if magnitude in _POSITIONAL:
        return format(number, "f")
    mantissa = str(digits[0])
    if len(digits) > 1:
        mantissa += "." + "".join(str(d) for d in digits[1:])
    return f"{'-' if sign else ''}{mantissa}e{magnitude}"
