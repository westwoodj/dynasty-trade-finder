"""Tests for player-name normalization and matching."""

from __future__ import annotations

from src.name_matching import ALIAS_MAP, match_name, normalize_name


class TestNormalizeName:
    def test_lowercases_and_strips(self) -> None:
        assert normalize_name("  Justin Jefferson  ") == "justin jefferson"

    def test_strips_generational_suffixes(self) -> None:
        assert normalize_name("Marvin Harrison Jr.") == "marvin harrison"
        assert normalize_name("Kenneth Walker III") == "kenneth walker"
        assert normalize_name("Michael Pittman Jr") == "michael pittman"

    def test_removes_punctuation(self) -> None:
        assert normalize_name("Ja'Marr Chase") == "jamarr chase"
        assert normalize_name("Amon-Ra St. Brown") == "amonra st brown"

    def test_collapses_whitespace(self) -> None:
        assert normalize_name("A.J.  Brown") == "aj brown"


class TestMatchName:
    CANDIDATES = {
        "justin jefferson",
        "jamarr chase",
        "hollywood brown",
        "tank dell",
        "bijan robinson",
    }

    def test_exact_normalized_match(self) -> None:
        assert match_name("Ja'Marr Chase", self.CANDIDATES) == "jamarr chase"

    def test_alias_forward(self) -> None:
        # "marquise brown" → alias → "hollywood brown"
        assert match_name("Marquise Brown", self.CANDIDATES) == "hollywood brown"

    def test_alias_reverse(self) -> None:
        # Candidate set holds the key form, query arrives in the value form
        candidates = {"marquise brown"}
        assert match_name("Hollywood Brown", candidates) == "marquise brown"

    def test_fuzzy_match_close_spelling(self) -> None:
        assert match_name("Bijan Robinsen", self.CANDIDATES) == "bijan robinson"

    def test_no_match_for_distant_name(self) -> None:
        assert match_name("Patrick Mahomes", self.CANDIDATES) is None

    def test_alias_map_is_normalized(self) -> None:
        for key, value in ALIAS_MAP.items():
            assert key == normalize_name(key)
            assert value == normalize_name(value)
