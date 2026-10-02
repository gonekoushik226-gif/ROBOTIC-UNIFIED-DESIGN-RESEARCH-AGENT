"""Values as people say them: "10 volts", "a resistance of 5 kilohms", "R1 equals 4.7 kΩ".

The calculation engine reads `10 V` and `5 kΩ`: a number, then one SI unit symbol. People
write and say the unit as a word, with a prefix as another word, and name the quantity before
the value in several ways. This module is the language layer's closed table for that: it
turns the words into the symbols the engine reads, changing nothing else. A unit or a
number it does not recognise is left exactly as given, for the engine to refuse or accept
on its own terms - nothing is guessed here.
"""

import re

#: The unit words people use, and the SI symbols the calculation engine reads.
UNIT_WORDS: dict[str, str] = {
    "volt": "V", "volts": "V", "v": "V",
    "ampere": "A", "amperes": "A", "amp": "A", "amps": "A", "ampère": "A",
    "ohm": "Ω", "ohms": "Ω", "Ω": "Ω", "Ω": "Ω",
    "watt": "W", "watts": "W",
    "farad": "F", "farads": "F",
    "henry": "H", "henries": "H", "henrys": "H",
    "hertz": "Hz", "hz": "Hz",
    "joule": "J", "joules": "J",
    "coulomb": "C", "coulombs": "C",
    "siemens": "S", "mho": "S", "mhos": "S",
    "weber": "Wb", "webers": "Wb",
    "tesla": "T", "teslas": "T",
    "second": "s", "seconds": "s", "sec": "s",
    "metre": "m", "metres": "m", "meter": "m", "meters": "m",
    "gram": "g", "grams": "g", "kilogram": "kg", "kilograms": "kg",
    "kelvin": "K", "mole": "mol", "moles": "mol",
}
PREFIX_WORDS: dict[str, str] = {
    "pico": "p", "nano": "n", "micro": "µ", "milli": "m", "kilo": "k", "mega": "M", "giga": "G",
}
_NUMBER = r"[-−]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
#: A value: a number, then a unit given as words or as a symbol (or none).
VALUE = re.compile(rf"(?P<number>{_NUMBER})\s*(?P<unit>[^\s,;]+(?:\s+[A-Za-zΩΩ]+)?)?", re.UNICODE)
_VALUE_ONLY = re.compile(rf"\s*(?P<number>{_NUMBER})\s*(?P<unit>[^\s,;\d][^\s,;]*)?\s*")
_FILLER = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_VALUE_OF = re.compile(r"^(?:the\s+)?(?:value|magnitude)\s+of\s+(?:the\s+)?", re.IGNORECASE)
#: How a quantity and its value are joined: =, is, equals, of (as in "a resistance of 5 ohms").
_JOIN = re.compile(r"\s*(?:==|=|\bequals?\b|\bis\b|\bare\b|\bof\b|\bat\b)\s*", re.IGNORECASE)


def unit_symbol(text: str) -> str | None:
    """The SI symbol for a unit written as a word ("kilohms", "milli amps"), else None."""
    word = " ".join(text.split())
    folded = word.casefold()
    if folded in UNIT_WORDS and not (len(word) == 1 and word.isupper()):
        return UNIT_WORDS[folded]
    for prefix, symbol in PREFIX_WORDS.items():
        for separator in ("", " ", "-"):
            if folded.startswith(prefix + separator) and folded[len(prefix + separator):] in UNIT_WORDS:
                return symbol + UNIT_WORDS[folded[len(prefix + separator):]]
    # "kilohm" and "megohm" drop the vowel the prefix would otherwise double.
    for stem, symbol in (("kilohm", "kΩ"), ("kohm", "kΩ"), ("megohm", "MΩ"), ("gigohm", "GΩ"), ("microhm", "µΩ")):
        if folded in (stem, stem + "s"):
            return symbol
    return None


def normalize_value(text: str) -> str | None:
    """"10 volts" -> "10 V"; "5 kilohms" -> "5 kΩ"; "2" -> "2"; None when it is not a value."""
    found = _VALUE_ONLY.fullmatch(text.strip())
    if found is None:
        return None
    number, unit = found.group("number"), found.group("unit")
    if not unit:
        return number
    symbol = unit_symbol(unit)
    return f"{number} {symbol if symbol else unit}"


def split_quantity(piece: str) -> tuple[str, str] | None:
    """("the voltage", "10 V") from "the voltage is 10 volts"; None if the piece has no value.

    The quantity is whatever precedes the joining word, kept as said (articles removed); the
    value is normalised to what the engine reads. A piece may also be just a value with the
    quantity after it ("10 V source") - that is not read, because which quantity it is would
    be a guess.
    """
    text = " ".join(piece.split()).strip(" .,;")
    last = None
    for found in _JOIN.finditer(text):
        value = normalize_value(text[found.end():])
        if value is not None:
            last = (text[:found.start()], value)
            break
    if last is None:
        return None
    name = _VALUE_OF.sub("", _FILLER.sub("", last[0].strip())).strip()
    name = _FILLER.sub("", name).strip()
    return (name, last[1]) if name else None
