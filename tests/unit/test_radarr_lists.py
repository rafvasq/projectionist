"""Tests for RadarrService.get_queue and get_missing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from services.radarr import RadarrService


def service():
    return RadarrService("http://radarr:7878", "key")


def reply(status=200, body=None):
    return MagicMock(status_code=status, json=lambda: body or {}, text="")


@patch("services.radarr.requests.get")
def test_get_queue_asks_for_the_movie_details(mock_get):
    mock_get.return_value = reply(body={"records": [{"movieId": 1}, "junk"], "totalRecords": 3})
    records, total = service().get_queue()
    assert records == [{"movieId": 1}] and total == 3
    assert mock_get.call_args.args[0] == "http://radarr:7878/api/v3/queue"
    assert mock_get.call_args.kwargs["params"]["includeMovie"] == "true"
    assert mock_get.call_args.kwargs["headers"] == {"X-Api-Key": "key"}


@patch("services.radarr.requests.get")
def test_get_missing_asks_for_monitored_movies_only(mock_get):
    mock_get.return_value = reply(body={"records": [{"id": 2}], "totalRecords": 1})
    assert service().get_missing() == ([{"id": 2}], 1)
    assert mock_get.call_args.args[0] == "http://radarr:7878/api/v3/wanted/missing"
    assert mock_get.call_args.kwargs["params"]["monitored"] == "true"


@patch("services.radarr.requests.get")
def test_errors_return_none_instead_of_raising(mock_get):
    mock_get.return_value = reply(status=500)
    assert service().get_queue() is None
    mock_get.side_effect = requests.ConnectionError("down")
    assert service().get_missing() is None
    mock_get.side_effect = None
    mock_get.return_value = MagicMock(status_code=200, json=MagicMock(side_effect=ValueError("not json")), text="")
    assert service().get_queue() is None


@patch("services.radarr.requests.get")
def test_unconfigured_service_makes_no_calls(mock_get):
    assert RadarrService().get_queue() is None
    mock_get.assert_not_called()
