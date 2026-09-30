"""SI units as dimension vectors, standard library only (ADR 0042 P11-15, P11-16; N8).

D-07 is decided: an in-house module, not `pint`. Dimensions are vectors of integer
exponents over the seven SI base quantities, in the order of `BASE_UNITS`. A `Quantity`
holds its value **in coherent SI units**, as an exact rational, with its dimension.

**The closed unit table** (`UNIT_TABLE`, version `UNIT_TABLE_VERSION`):

- the SI base units: `m`, `kg` (written `g` with the prefix `k`), `s`, `A`, `K`, `mol`,
  `cd`;
- the derived units: `V`, `Ω`, `W`, `F`, `H`, `Hz`, `J`, `C`, `S`, `Wb`, `T`.

**Prefixes** are the SI prefixes from pico to giga: `p`, `n`, `µ`/`μ`/`u`, `m`, `k`, `M`,
`G`. Symbols and prefixes are **case-sensitive**: `S` is the siemens and `s` the second,
`m` is milli and `M` mega. For that reason `app.extraction`'s case-insensitive
`KNOWN_UNITS` is not used here, and the import boundary forbids it anyway.

**Refused as unsupported** (not unknown): logarithmic units (`dB`, `Np`), affine
temperatures (`°C`, `°F`) and angles (`°`, `deg`, `rad`, `sr`). Angles need
trigonometry, which Phase 11 does not have (N7). **Any other text is an unknown unit**,
an error that is never guessed.

A unit is one symbol with at most one prefix. The symbol is matched exactly first, then
as a prefix followed by a table symbol, so `mm` is the millimetre, `ms` the
millisecond, `mS` the millisiemens and `T` the tesla. There is no tera prefix.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from app.calculation.numbers import NUMBER_PATTERN, NumberError, parse_number

#: The seven SI base units, in the order a `Dimension` holds their exponents.
BASE_UNITS: tuple[str, ...] = ("m", "kg", "s", "A", "K", "mol", "cd")

#: Recorded in every trace (P6 §11; ADR 0042 P11-24).
UNIT_TABLE_NAME = "RUDRA SI unit table"
UNIT_TABLE_VERSION = "1"


class UnitProblem(StrEnum):
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class UnitError(ValueError):
    """A unit that is not in the table (`UNKNOWN`) or is refused (`UNSUPPORTED`)."""

    def __init__(self, problem: UnitProblem, unit: str, detail: str) -> None:
        super().__init__(detail)
        self.problem = problem
        self.unit = unit


class ConversionError(ValueError):
    """A quantity asked for in a unit of another dimension."""


@dataclass(frozen=True, slots=True)
class Dimension:
    """Integer exponents over `BASE_UNITS`."""

    exponents: tuple[int, int, int, int, int, int, int]

    def times(self, other: "Dimension") -> "Dimension":
        return Dimension(tuple(a + b for a, b in zip(self.exponents, other.exponents)))  # type: ignore[arg-type]

    def over(self, other: "Dimension") -> "Dimension":
        return Dimension(tuple(a - b for a, b in zip(self.exponents, other.exponents)))  # type: ignore[arg-type]

    def power(self, exponent: int) -> "Dimension":
        return Dimension(tuple(a * exponent for a in self.exponents))  # type: ignore[arg-type]

    @property
    def dimensionless(self) -> bool:
        return not any(self.exponents)


def _dim(m: int = 0, kg: int = 0, s: int = 0, a: int = 0, k: int = 0, mol: int = 0, cd: int = 0) -> Dimension:
    return Dimension((m, kg, s, a, k, mol, cd))


DIMENSIONLESS = _dim()


@dataclass(frozen=True, slots=True)
class Unit:
    """A unit symbol and its exact factor to the coherent SI unit of its dimension."""

    symbol: str
    factor: Fraction
    dimension: Dimension


_OHM = _dim(m=2, kg=1, s=-3, a=-2)

#: symbol -> (factor to coherent SI, dimension). The table's derived units have
#: pairwise distinct dimensions, so the coherent name of a dimension is unique.
UNIT_TABLE: dict[str, tuple[Fraction, Dimension]] = {
    "m": (Fraction(1), _dim(m=1)),
    "g": (Fraction(1, 1000), _dim(kg=1)),
    "s": (Fraction(1), _dim(s=1)),
    "A": (Fraction(1), _dim(a=1)),
    "K": (Fraction(1), _dim(k=1)),
    "mol": (Fraction(1), _dim(mol=1)),
    "cd": (Fraction(1), _dim(cd=1)),
    "V": (Fraction(1), _dim(m=2, kg=1, s=-3, a=-1)),
    "Ω": (Fraction(1), _OHM),  # GREEK CAPITAL LETTER OMEGA, the canonical ohm
    "Ω": (Fraction(1), _OHM),  # OHM SIGN
    "W": (Fraction(1), _dim(m=2, kg=1, s=-3)),
    "F": (Fraction(1), _dim(m=-2, kg=-1, s=4, a=2)),
    "H": (Fraction(1), _dim(m=2, kg=1, s=-2, a=-2)),
    "Hz": (Fraction(1), _dim(s=-1)),
    "J": (Fraction(1), _dim(m=2, kg=1, s=-2)),
    "C": (Fraction(1), _dim(s=1, a=1)),
    "S": (Fraction(1), _dim(m=-2, kg=-1, s=3, a=2)),
    "Wb": (Fraction(1), _dim(m=2, kg=1, s=-2, a=-1)),
    "T": (Fraction(1), _dim(kg=1, s=-2, a=-1)),
}

#: SI prefixes from pico to giga, case-sensitive, as exact factors.
PREFIXES: dict[str, Fraction] = {
    "p": Fraction(1, 10**12),
    "n": Fraction(1, 10**9),
    "µ": Fraction(1, 10**6),  # MICRO SIGN
    "μ": Fraction(1, 10**6),  # GREEK SMALL LETTER MU
    "u": Fraction(1, 10**6),
    "m": Fraction(1, 10**3),
    "k": Fraction(10**3),
    "M": Fraction(10**6),
    "G": Fraction(10**9),
}

#: Units refused as unsupported, with the reason given for each kind.
UNSUPPORTED_UNITS: dict[str, str] = {
    **dict.fromkeys(("dB", "Np"), "a logarithmic unit"),
    **dict.fromkeys(
        ("°C", "℃", "degC", "°F", "℉", "degF"), "an affine temperature unit"
    ),
    **dict.fromkeys(("°", "deg", "degree", "degrees", "rad", "sr"), "an angular unit"),
}


def _coherent_names() -> dict[Dimension, str]:
    """The coherent unit named for each dimension: the table's base and derived units.

    `kg` for mass (the table holds `g`); the first table symbol with factor 1 otherwise,
    so the ohm is written with the canonical `Ω`.
    """
    names: dict[Dimension, str] = {_dim(kg=1): "kg"}
    for symbol, (factor, dimension) in UNIT_TABLE.items():
        if factor == 1:
            names.setdefault(dimension, symbol)
    return names


_COHERENT = _coherent_names()


def parse_unit(text: str) -> Unit:
    """One unit symbol with at most one prefix, from the closed table."""
    symbol = text.strip() if isinstance(text, str) else text
    if not isinstance(symbol, str) or not symbol:
        raise UnitError(UnitProblem.UNKNOWN, str(text), "A unit needs a symbol.")
    refused = _refused(symbol)
    if refused is not None:
        raise refused
    if symbol in UNIT_TABLE:
        factor, dimension = UNIT_TABLE[symbol]
        return Unit(symbol, factor, dimension)
    prefix, rest = symbol[:1], symbol[1:]
    if prefix in PREFIXES and rest:
        refused = _refused(rest, whole=symbol)
        if refused is not None:
            raise refused
        if rest in UNIT_TABLE:
            factor, dimension = UNIT_TABLE[rest]
            return Unit(symbol, PREFIXES[prefix] * factor, dimension)
    raise UnitError(
        UnitProblem.UNKNOWN,
        symbol,
        f"{symbol!r} is not a unit in the {UNIT_TABLE_NAME} (version {UNIT_TABLE_VERSION}); "
        "units are case-sensitive SI symbols such as V, Ω, kΩ, mA, µF, Hz.",
    )


def _refused(symbol: str, *, whole: str | None = None) -> UnitError | None:
    kind = UNSUPPORTED_UNITS.get(symbol)
    if kind is None:
        return None
    shown = whole or symbol
    return UnitError(
        UnitProblem.UNSUPPORTED, shown, f"{shown!r} is {kind}, which Phase 11 does not support."
    )


def coherent_unit(dimension: Dimension) -> str:
    """The coherent SI unit of a dimension: a named unit, a product of base units, or ``""``.

    A product is written in the order of `BASE_UNITS`, with `·` between factors and `^n`
    for any exponent other than 1: a velocity is `m·s^-1`.
    """
    if dimension in _COHERENT:
        return _COHERENT[dimension]
    parts = []
    for symbol, exponent in zip(BASE_UNITS, dimension.exponents):
        if exponent == 1:
            parts.append(symbol)
        elif exponent:
            parts.append(f"{symbol}^{exponent}")
    return "·".join(parts)


@dataclass(frozen=True, slots=True)
class Quantity:
    """An exact value in coherent SI units, with its dimension."""

    value: Fraction
    dimension: Dimension = DIMENSIONLESS

    @property
    def unit(self) -> str:
        """The coherent SI unit the value is expressed in (``""`` when dimensionless)."""
        return coherent_unit(self.dimension)


def quantity(number: Fraction | int, unit: str | None = None) -> Quantity:
    """`number` of `unit`, converted exactly to coherent SI (`None` means dimensionless)."""
    if unit is None:
        return Quantity(Fraction(number))
    parsed = parse_unit(unit)
    return Quantity(Fraction(number) * parsed.factor, parsed.dimension)


#: A value as a request writes it: a decimal literal, then optionally one unit symbol.
_VALUE = re.compile(rf"\s*([-−]?{NUMBER_PATTERN})\s*(\S*)\s*")


def parse_quantity(text: str) -> Quantity:
    """A request's value text, such as `10 Ω`, `0.5 mA` or `2`, as an exact quantity.

    Raises `NumberError` when the text is not a number followed by at most one unit, and
    `UnitError` for a unit that is unknown or unsupported.
    """
    match = _VALUE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise NumberError(
            f"{text!r} is not a value: a decimal number, optionally followed by one unit "
            "(for example '10 Ω' or '0.5 mA')."
        )
    number, unit = match.groups()
    return quantity(parse_number(number), unit or None)


def to_unit(value: Quantity, unit: str) -> Fraction:
    """The exact number of `unit` in `value`; the dimensions must be identical."""
    parsed = parse_unit(unit)
    if parsed.dimension != value.dimension:
        raise ConversionError(
            f"A quantity in {value.unit or 'no unit'} cannot be expressed in {parsed.symbol}: "
            "the dimensions differ."
        )
    return value.value / parsed.factor
