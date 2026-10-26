"""Tests for the checked AI calls in core/ai.py, run offline against scripted model answers."""

from __future__ import annotations

import json

import pytest

from core.ai import MIN_ROW_KEYS, curate_row
from mellea.core.base import ComponentParseError
from tests.scripted_backend import scripted_session

LIBRARY = set(range(1, 31))


def row(keys, name="Midnight Reels"):
    return json.dumps({"result": {"name": name, "keys": keys}})


def ask(session):
    return curate_row(m=session, movies_json="[]", exclude_themes_str="", valid_keys=LIBRARY)


# ==============================================================================
# curate_row
# ==============================================================================

def test_a_good_answer_is_accepted_without_a_retry():
    session, backend = scripted_session(row(list(range(1, 13))))
    result = ask(session)
    assert result.name == "Midnight Reels" and result.keys == list(range(1, 13))
    assert backend.idx == 1


@pytest.mark.parametrize("bad", [
    list(range(1, 11)) + [99],                    # names a film that is not in the library
    list(range(1, 6)),                            # too few
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 9],         # repeats a key to reach the count
])
def test_a_bad_answer_triggers_a_retry(bad):
    session, backend = scripted_session(row(bad), row(list(range(1, 13))))
    assert ask(session).keys == list(range(1, 13))
    assert backend.idx == 2


def test_unknown_keys_are_dropped_when_retries_run_out_but_enough_remain():
    twelve_with_two_unknown = row(list(range(1, 11)) + [98, 99])
    session, backend = scripted_session(*[twelve_with_two_unknown] * 3)
    assert ask(session).keys == list(range(1, 11))          # the two unknown keys are gone
    assert backend.idx == 3


def test_too_few_valid_keys_after_retries_raises_so_the_row_is_skipped():
    session, backend = scripted_session(*[row(list(range(1, 8)) + [98, 99])] * 3)
    with pytest.raises(ValueError, match="only 7"):
        ask(session)
    assert backend.idx == 3


def test_duplicates_are_removed_from_an_accepted_answer():
    session, _ = scripted_session(row(list(range(1, 13)) * 2))
    assert ask(session).keys == list(range(1, 13))


def test_malformed_output_propagates_for_the_caller_to_handle():
    session, _ = scripted_session("not json at all")
    with pytest.raises(ComponentParseError):
        ask(session)


def test_the_minimum_matches_the_prompt():
    assert MIN_ROW_KEYS == 10       # the generative prompt asks for 10-20 films


# ==============================================================================
# read_movie
# ==============================================================================

def intent(title, year=None):
    return json.dumps({"result": {"title": title, "year": year}})


def read(session):
    from core.ai import read_movie

    return read_movie(m=session, text="that movie with the scissor hands")


def test_a_sensible_answer_is_accepted_without_a_retry():
    session, backend = scripted_session(intent("Edward Scissorhands", 1990))
    result = read(session)
    assert (result.title, result.year) == ("Edward Scissorhands", 1990)
    assert backend.idx == 1


def test_no_year_is_fine():
    session, backend = scripted_session(intent("Heat"))
    assert read(session).year is None and backend.idx == 1


def test_the_newest_allowed_year_is_two_years_ahead():
    from datetime import date

    session, _ = scripted_session(intent("Upcoming", date.today().year + 2))
    assert read(session).year == date.today().year + 2


@pytest.mark.parametrize("bad_year", [1500, 3024, 0])
def test_an_implausible_year_triggers_a_retry(bad_year):
    session, backend = scripted_session(intent("Edward Scissorhands", bad_year), intent("Edward Scissorhands", 1990))
    assert read(session).year == 1990 and backend.idx == 2


def test_a_year_that_stays_implausible_is_dropped_but_the_title_is_kept():
    session, backend = scripted_session(intent("Edward Scissorhands", 3024), intent("Edward Scissorhands", 3024))
    result = read(session)
    assert (result.title, result.year) == ("Edward Scissorhands", None)
    assert backend.idx == 2


def test_an_empty_title_retries_and_then_raises_so_the_raw_text_is_used():
    session, backend = scripted_session(intent("  ", 1990), intent("", 1990))
    with pytest.raises(ValueError):
        read(session)
    assert backend.idx == 2
