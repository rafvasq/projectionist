"""
Characterization of Mellea 0.8.0 behaviour that the AI code relies on. If an upgrade changes
one of these, the code that uses requirements and retries needs another look.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from mellea import generative
from mellea.core.base import ComponentParseError
from mellea.stdlib.requirements import req, simple_validate
from mellea.stdlib.sampling import RejectionSamplingStrategy
from tests.scripted_backend import scripted_session

VALID = {1, 2, 3, 4, 5}


class Row(BaseModel):
    name: str = Field(description="name")
    keys: list[int] = Field(description="keys")


@generative
def pick_row(movies_json: str) -> Row:
    """Pick movies. {movies_json}"""
    ...


def answer(keys):
    return json.dumps({"result": {"name": "Row", "keys": keys}})


seen: list[str] = []


def keys_exist(output: str) -> bool:
    seen.append(output)
    return all(k in VALID for k in json.loads(output)["result"]["keys"])


KEYS_EXIST = req("Every key must be a provided key", validation_fn=simple_validate(keys_exist, reason="unknown key"))


def call(session, budget=3):
    seen.clear()
    return pick_row(session, movies_json="[]", requirements=[KEYS_EXIST], strategy=RejectionSamplingStrategy(loop_budget=budget))


def test_validator_receives_the_json_wrapper_with_the_result_inside():
    session, _ = scripted_session(answer([1, 2]))
    assert call(session).keys == [1, 2]
    assert json.loads(seen[0]) == {"result": {"name": "Row", "keys": [1, 2]}}


def test_a_failing_requirement_triggers_a_retry():
    session, backend = scripted_session(answer([1, 99]), answer([1, 2]))
    assert call(session).keys == [1, 2]
    assert backend.idx == 2


def test_exhausting_the_budget_returns_a_failing_answer_instead_of_raising():
    session, backend = scripted_session(answer([1, 98]), answer([1, 99]), answer([1, 97]))
    result = call(session)
    assert backend.idx == 3
    assert result.keys == [1, 98]            # the first attempt: never trust it without checking in code


def test_malformed_output_raises_and_is_not_retried():
    session, backend = scripted_session("not json at all", answer([1, 2]))
    with pytest.raises(ComponentParseError):
        call(session, budget=2)
    assert backend.idx == 1
