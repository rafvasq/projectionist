"""Tests for AI provider response parsing — no API calls."""

from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider


# Both providers share identical _parse_keys logic, test both

PROVIDERS = [GeminiProvider, OllamaProvider]


def test_parses_clean_array():
    for cls in PROVIDERS:
        assert cls._parse_keys("[1, 2, 3]", 1) == [1, 2, 3]


def test_parses_code_fenced_json():
    for cls in PROVIDERS:
        assert cls._parse_keys("```json\n[1, 2, 3]\n```", 1) == [1, 2, 3]


def test_parses_code_fenced_no_lang():
    for cls in PROVIDERS:
        assert cls._parse_keys("```\n[10, 20]\n```", 1) == [10, 20]


def test_returns_empty_on_no_array():
    for cls in PROVIDERS:
        assert cls._parse_keys("Sure, here are my picks!", 1) == []


def test_returns_empty_on_malformed_json():
    for cls in PROVIDERS:
        assert cls._parse_keys("[1, 2, oops]", 1) == []


def test_drops_non_integer_values():
    for cls in PROVIDERS:
        result = cls._parse_keys('[1, "two", 3, null]', 1)
        assert result == [1, 3]


def test_returns_empty_on_empty_array():
    for cls in PROVIDERS:
        assert cls._parse_keys("[]", 1) == []


def test_ignores_text_before_array():
    for cls in PROVIDERS:
        assert cls._parse_keys("Here you go: [42, 99]", 1) == [42, 99]


def test_nested_array_returns_only_integers():
    # Inner array [1, 2] is matched first; non-integer inner array is dropped
    for cls in PROVIDERS:
        result = cls._parse_keys("[[1, 2], 3]", 1)
        assert isinstance(result, list)
        assert all(isinstance(k, int) for k in result)


def test_drops_float_values():
    for cls in PROVIDERS:
        result = cls._parse_keys("[1, 2.0, 3]", 1)
        assert result == [1, 3]
