"""Tests for the Overseerr cleanup methods (requests and watchlist) against a fake server."""

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
    """Just enough of the API: movie details, request deletion, and a paged watchlist."""

    def __init__(self, requests_by_tmdb=None, watchlist=(), sticky=(), broken=()):
        self.requests_by_tmdb = requests_by_tmdb or {}
        self.watchlist = list(watchlist)
        self.sticky = set(sticky)       # watchlist entries that DELETE reports as removed but keeps
        self.broken = set(broken)       # paths that answer 500
        self.deleted_requests, self.removed_from_watchlist = [], []

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
        if method == "GET" and path == "/discover/watchlist":
            page = kwargs["params"]["page"]
            chunk = self.watchlist[(page - 1) * 2: page * 2]
            return Resp(200, {"totalPages": max(1, -(-len(self.watchlist) // 2)),
                              "results": [{"mediaType": "movie", "tmdbId": t} for t in chunk]})
        if method == "DELETE" and path.startswith("/watchlist/"):
            tmdb = int(path.rsplit("/", 1)[1])
            assert kwargs["params"] == {"mediaType": "movie"}
            self.removed_from_watchlist.append(tmdb)
            if tmdb not in self.sticky:
                self.watchlist.remove(tmdb)
            return Resp(204)
        raise AssertionError(f"unexpected call {method} {path}")


def service():
    return OverseerrService(url="http://seerr:5055", api_key="key")


def run(fake, titles):
    with patch("services.overseerr.requests.request", side_effect=fake):
        return service().forget_movies(titles)


def test_clears_requests_and_watchlist_entries_across_pages():
    fake = FakeSeerr(requests_by_tmdb={1: [10, 11], 3: [12]}, watchlist=[1, 2, 3, 4, 5])
    assert run(fake, {1: "Heat", 3: "Alien"}) == []
    assert fake.deleted_requests == [10, 11, 12]
    assert fake.removed_from_watchlist == [1, 3]
    assert fake.watchlist == [2, 4, 5]


def test_nothing_to_clear_does_nothing():
    fake = FakeSeerr(watchlist=[9])
    assert run(fake, {1: "Heat"}) == []
    assert fake.deleted_requests == [] and fake.removed_from_watchlist == []


def test_reports_a_watchlist_entry_that_does_not_go_away():
    fake = FakeSeerr(watchlist=[1], sticky=[1])
    assert run(fake, {1: "Heat"}) == ["Heat: still on the watchlist after removal"]


def test_reports_a_request_that_cannot_be_deleted():
    fake = FakeSeerr(requests_by_tmdb={1: [10]})
    fake.broken = {"/request/10"}
    assert run(fake, {1: "Heat"}) == ["Heat: request 10 not deleted"]


def test_unreadable_watchlist_is_reported_but_requests_are_still_cleared():
    fake = FakeSeerr(requests_by_tmdb={1: [10]}, broken=["/discover/watchlist"])
    assert run(fake, {1: "Heat"}) == ["could not read the watchlist"]
    assert fake.deleted_requests == [10]


def test_unreadable_movie_details_are_reported():
    fake = FakeSeerr(broken=["/movie/1"])
    assert run(fake, {1: "Heat"}) == ["Heat: could not read its requests"]


def test_network_errors_do_not_raise():
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    with patch("services.overseerr.requests.request", side_effect=boom):
        svc = service()
        assert svc.delete_request(5) is False
        assert svc.watchlist_tmdb_ids() is None
        assert svc.remove_from_watchlist(1) is False
        assert svc.request_ids(1) is None
