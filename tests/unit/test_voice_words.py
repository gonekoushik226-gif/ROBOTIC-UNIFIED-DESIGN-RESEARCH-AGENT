"""Words the person wants spelt as written: kept locally, tidied, and given to the recogniser as context."""

from __future__ import annotations

import json

import pytest

from app.voice import vocabulary_prompt, words


@pytest.mark.parametrize("text, expected", [
    ("Kaushik, Anantham", ("Kaushik", "Anantham")),
    ("Kaushik;  Koushik Reddy\nKrishnamurthy", ("Kaushik", "Koushik Reddy", "Krishnamurthy")),
    ("kaushik, Kaushik, KAUSHIK", ("kaushik",)),                       # no repeats, whatever the case
    ("  , ,, ", ()),
    ("O'Brien, Jean-Luc, St. John", ("O'Brien", "Jean-Luc", "St. John")),
    ("Ohm's law; <script>alert(1)</script>", ("Ohm's law", "scriptalert1script")),  # markup is not kept as markup
    ("x" * 100, ("x" * words.MAX_LENGTH,)),
])
def test_words_are_tidied(text, expected):
    assert words.clean(text) == expected


def test_at_most_twenty_words_are_kept():
    assert len(words.clean(", ".join(f"word{i}" for i in range(50)))) == words.MAX_WORDS


def test_words_are_saved_in_the_config_folder_and_read_back(tmp_path):
    config = tmp_path / "config"
    assert words.load(config) == ()                                    # nothing saved yet: no error
    assert words.save(config, "Kaushik, Anantham") == ("Kaushik", "Anantham")
    assert json.loads((config / "voice.json").read_text(encoding="utf-8")) == {"words": ["Kaushik", "Anantham"]}
    assert words.load(config) == ("Kaushik", "Anantham")
    assert words.save(config, "") == () and words.load(config) == ()


@pytest.mark.parametrize("content", ["{not json", "[]", '{"words": 5}', '{"words": null}', ""])
def test_an_unreadable_words_file_is_no_words_never_an_error(tmp_path, content):
    (tmp_path / "voice.json").write_text(content, encoding="utf-8")
    assert words.load(tmp_path) == ()


def test_the_words_lead_the_text_the_recogniser_is_told_it_is_continuing():
    plain = vocabulary_prompt()
    hinted = vocabulary_prompt(("Kaushik", "Koushik Reddy"))
    assert hinted == "Kaushik, Koushik Reddy. " + plain
    assert vocabulary_prompt(()) == plain
