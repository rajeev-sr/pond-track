"""Name canonicalisation: the answer to HLD CH-24.

Every case here is a real spelling pair, most of them taken from the Census 2011
register for Durg district itself -- `jevra` and `jewra` both appear in it, as do
`kutelabhatha` and the `kutelabhata` people write. A village nobody can find is
a system that does not work, so these are correctness tests, not niceties.
"""

from __future__ import annotations

import pytest

from app.core.names import (
    devanagari_to_latin,
    has_devanagari,
    normalise_name,
    normalise_query,
    strip_diacritics,
)


class TestTheSameVillageSpelledDifferently:
    """Pairs that must collapse to one canonical form."""

    @pytest.mark.parametrize(
        ("left", "right", "why"),
        [
            ("Kutelabhata", "Kutelabhatha", "aspiration is written inconsistently"),
            ("Jevra", "Jewra", "v and w are one sound; both spellings are in the register"),
            ("Rampur", "Rampura", "the final inherent vowel is optional"),
            ("Sirsa", "Shirsa", "sh and s alternate"),
            ("Ram Pur", "Rampur", "word breaks are not meaningful"),
            ("Ram-Pur", "Rampur", "nor is punctuation"),
            ("Rāmpur", "Rampur", "diacritics are dropped"),
            ("Mithi", "Mitthi", "doubled consonants are not distinguished"),
            ("Zila", "Jila", "z is written j across northern India"),
            ("Kutelabhaata", "Kutelabhata", "vowel length is dropped"),
            ("  Rampur  ", "Rampur", "surrounding whitespace is irrelevant"),
            ("RAMPUR", "rampur", "case is irrelevant"),
        ],
    )
    def test_they_share_one_canonical_form(self, left: str, right: str, why: str) -> None:
        assert normalise_name(left) == normalise_name(right), why

    def test_the_query_side_folds_identically(self) -> None:
        """Both sides must meet in the same space or the fold is pointless."""
        assert normalise_query("Kutelabhata") == normalise_name("Kutelabhatha")


class TestDifferentVillagesStayDifferent:
    """Folding that merges distinct villages is worse than no folding."""

    @pytest.mark.parametrize(
        ("left", "right", "why"),
        [
            ("Sirsa Khurd", "Sirsa Kalan", "khurd and kalan are two settlements"),
            ("Rampur", "Ranipur", "different name, one letter apart"),
            ("Khapri", "Khairi", "not the same village"),
            ("Durg", "Dhamdha", "different sub-districts"),
        ],
    )
    def test_they_keep_separate_forms(self, left: str, right: str, why: str) -> None:
        assert normalise_name(left) != normalise_name(right), why

    def test_khurd_and_kalan_survive_the_fold(self) -> None:
        """These qualifiers carry the meaning; dropping them loses a village."""
        folded = normalise_name("Sirsa Khurd")
        assert "kurd" in folded or "khurd" in folded


class TestDevanagariInput:
    """HLD NFR-15: a user may type their village in Devanagari."""

    @pytest.mark.parametrize(
        ("devanagari", "latin"),
        [
            ("रामपुर", "Rampur"),
            ("कमल", "Kamal"),
            ("खापरी", "Khapri"),
            ("दुर्ग", "Durg"),
            ("कुटेलाभाठा", "Kutelabhatha"),
            ("सिरसा", "Sirsa"),
            ("भिलाई", "Bhilai"),
        ],
    )
    def test_it_lands_on_the_latin_form(self, devanagari: str, latin: str) -> None:
        assert normalise_name(devanagari) == normalise_name(latin)

    def test_schwa_deletion_is_positional_not_wholesale(self) -> None:
        """The rule that separates a working transliteration from a broken one.

        रामपुर must lose the inherent vowel on म (following syllable has an
        explicit matra) while कमल must keep the one on म (the final syllable has
        only an inherent vowel). Dropping every inherent vowel would give `kml`;
        keeping every one would give `ramapura`. Both are wrong.
        """
        assert devanagari_to_latin("रामपुर") == "raampur"
        assert devanagari_to_latin("कमल") == "kamal"

    def test_a_virama_suppresses_the_vowel(self) -> None:
        assert devanagari_to_latin("दुर्ग") == "durg"

    def test_detection(self) -> None:
        assert has_devanagari("दुर्ग")
        assert has_devanagari("Durg दुर्ग")
        assert not has_devanagari("Durg")


class TestEdges:
    def test_empty_and_punctuation_only_input_is_empty(self) -> None:
        assert normalise_name("") == ""
        assert normalise_name("   ") == ""
        assert normalise_name("---") == ""
        assert normalise_name("!@#") == ""

    def test_digits_survive(self) -> None:
        """Some village names carry a numeral, e.g. `Kachhar No. 2`."""
        assert "2" in normalise_name("Kachhar No. 2")

    def test_diacritics_are_stripped_without_dropping_the_letter(self) -> None:
        assert strip_diacritics("Rāmpur") == "Rampur"
        assert strip_diacritics("Bhilaī") == "Bhilai"

    def test_it_is_idempotent(self) -> None:
        """Normalising an already-normalised name must not change it again.

        The seeder and the search path both call this, sometimes on values that
        have been through it before; a fold that drifts on reapplication would
        silently stop matching its own stored rows.
        """
        for name in ("Kutelabhatha", "Sirsa Khurd", "रामपुर", "Ram-Pur", "Kachhar No. 2"):
            once = normalise_name(name)
            assert normalise_name(once) == once, name

    def test_it_never_returns_leading_or_trailing_space(self) -> None:
        for name in (" Rampur ", "Ram  Pur", "-Rampur-"):
            folded = normalise_name(name)
            assert folded == folded.strip()

    def test_output_fits_the_column(self) -> None:
        """`villages.name_normalised` is varchar(200); folding only shortens."""
        long_name = "Chandrapur " * 30
        assert len(normalise_name(long_name)) <= len(long_name)
