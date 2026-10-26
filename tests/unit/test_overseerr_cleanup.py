"""Tests for clearing a deleted movie's Overseerr requests, against a fake server."""

from __future__ import annotations

from unittest.mock import patch

import requests

from services.overseerr import OverseerrService


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


class FakeSeerr:
    """Just enough of the API: movie details and request deletion."""

    def __init__(self, requests_by_tmdb=None, broken=()):
        self.requests_by_tmdb = requests_by_tmdb or {}
        self.broken = set(broken)       # paths that answer 500
        self.deleted_requests = []

    def __call__(self, method, url, **kwargs):
        path = url.split("/api/v1", 1)[1]
        if path in self.broken:
            return Resp(500)
        if method == "GET" and path.startswith("/movie/"):
            tmdb = int(path.rsplit("/", 1)[1])
            return Resp(200, {"mediaInfo": {"requests": [{"id": i} for i in self.requests_by_tmdb.get(tmdb, [])]}})
        if method == "DELETE" and path.startswith("/request/"):
            self.deleted_requests.append(int(path.rsplit("/", 1)[1]))
            return Resp(204)
        raise AssertionError(f"unexpected call {method} {path}")


def run(fake, titles):
    with patch("services.overseerr.requests.request", side_effect=fake):
        return OverseerrService(url="http://seerr:5055", api_key="key").clear_requests(titles)


def test_deletes_every_request_for_each_movie():
    fake = FakeSeerr(requests_by_tmdb={1: [10, 11], 3: [12]})
    assert run(fake, {1: "Heat", 2: "Alien", 3: "Ronin"}) == []
    assert fake.deleted_requests == [10, 11, 12]


def test_a_movie_with_no_requests_needs_nothing():
    fake = FakeSeerr()
    assert run(fake, {1: "Heat"}) == []
    assert fake.deleted_requests == []


def test_reports_a_request_that_cannot_be_deleted():
    fake = FakeSeerr(requests_by_tmdb={1: [10]})
    fake.broken = {"/request/10"}
    assert run(fake, {1: "Heat"}) == ["Heat: request 10 not deleted"]


def test_unreadable_movie_details_are_reported():
    assert run(FakeSeerr(broken=["/movie/1"]), {1: "Heat"}) == ["Heat: could not read its requests"]


def test_network_errors_do_not_raise():
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    with patch("services.overseerr.requests.request", side_effect=boom):
        svc = OverseerrService(url="http://seerr:5055", api_key="key")
        assert svc.delete_request(5) is False
        assert svc.request_ids(1) is None
