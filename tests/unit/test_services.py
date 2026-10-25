"""
tests/unit/test_services.py
Comprehensive unit tests for the services layer:
- services/overseerr.py (OverseerrService)
- services/radarr.py (RadarrService)
- services/letterboxd.py (Scraper, FlareSolverr fallback, prompt generator)
- services/tracker.py (CollectionTracker, sync, webhook handler, list processor)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests

from core.plex import MovieMeta
from services.letterboxd import (
    build_list_prompt,
    extract_letterboxd_film_metadata,
    fetch_letterboxd_html,
    scrape_letterboxd_list,
)
from services.overseerr import OverseerrService
from services.radarr import RadarrService
from services.tracker import (
    CollectionTracker,
    handle_media_available,
    process_letterboxd_collection,
    sync_tracked_collections,
)


# ==============================================================================
# OverseerrService Tests
# ==============================================================================

def test_overseerr_init_defaults_and_url_normalization():
    svc = OverseerrService("http://fake-host:5055/", "api_key_123")
    assert svc.url == "http://fake-host:5055"
    assert svc.api_key == "api_key_123"
    assert svc.timeout == 10.0
    assert svc.is_configured is True
    assert svc._headers == {"X-Api-Key": "api_key_123"}


def test_overseerr_unconfigured():
    svc = OverseerrService("", "")
    assert svc.is_configured is False
    assert svc._headers == {}


def test_overseerr_from_config_custom_and_default():
    cfg = {
        "overseerr": {
            "url": "http://my-overseerr:5055/",
            "api_key": "custom_key",
            "timeout": 12.5,
        }
    }
    svc = OverseerrService.from_config(cfg)
    assert svc.url == "http://my-overseerr:5055"
    assert svc.api_key == "custom_key"
    assert svc.timeout == 12.5

    with patch("core.config.load_config", return_value=cfg):
        svc_default = OverseerrService.from_config()
        assert svc_default.url == "http://my-overseerr:5055"
        assert svc_default.api_key == "custom_key"


@patch("requests.get")
def test_overseerr_search_success_filters_non_media(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {"id": 1, "mediaType": "movie", "title": "Inception"},
            {"id": 2, "mediaType": "person", "name": "Christopher Nolan"},
            {"id": 3, "mediaType": "tv", "name": "Dark"},
        ]
    }
    mock_get.return_value = mock_resp

    svc = OverseerrService("http://fake-host:5055", "secret", timeout=8.0)
    results = svc.search("Inception")

    assert results is not None
    assert len(results) == 2
    assert results[0]["id"] == 1
    assert results[1]["id"] == 3

    mock_get.assert_called_once_with(
        "http://fake-host:5055/api/v1/search",
        params="query=Inception",
        headers={"X-Api-Key": "secret"},
        timeout=8.0,
    )


def test_overseerr_search_empty_query():
    svc = OverseerrService("http://fake-host:5055", "secret")
    assert svc.search("") == []
    assert svc.search("   ") == []


def test_overseerr_search_unconfigured():
    svc = OverseerrService("", "")
    assert svc.search("anything") is None


@patch("requests.get")
def test_overseerr_search_http_and_network_error(mock_get):
    svc = OverseerrService("http://fake-host:5055", "secret")

    # 1. Non-200 HTTP response
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_get.return_value = mock_resp
    assert svc.search("Fail Query") is None

    # 2. Network exception
    mock_get.side_effect = requests.RequestException("Connection timed out")
    assert svc.search("Fail Query") is None


def test_overseerr_find_best_match_avoids_tv_collision():
    mock_results = [
        {"mediaType": "tv", "id": 46392, "name": "Duel Masters", "firstAirDate": "2002-10-21"},
        {"mediaType": "movie", "id": 617653, "title": "The Last Duel", "releaseDate": "2021-10-13"},
        {"mediaType": "movie", "id": 839, "title": "Duel", "releaseDate": "1971-12-25"},
    ]
    svc = OverseerrService("http://fake-host:5055", "secret")

    # 1. Exact year string match
    mtype, item = svc.find_best_match("Duel", "1971", media_list=mock_results)
    assert mtype == "movie"
    assert item["id"] == 839
    assert item["title"] == "Duel"

    # 2. Exact integer year match
    mtype, item = svc.find_best_match("Duel", 1971, media_list=mock_results)
    assert mtype == "movie"
    assert item["id"] == 839

    # 3. Matching without year prefers exact title movie over TV
    mtype, item = svc.find_best_match("Duel", None, media_list=mock_results)
    assert mtype == "movie"
    assert item["id"] == 839


def test_overseerr_find_best_match_year_tolerance_plus_minus_one():
    mock_results = [
        {"mediaType": "movie", "id": 100, "title": "Festival Film", "releaseDate": "2022-09-01"},
    ]
    svc = OverseerrService("http://fake-host:5055", "secret")

    # Target year 2023 matches 2022 (+1 tolerance)
    mtype, item = svc.find_best_match("Festival Film", 2023, media_list=mock_results)
    assert mtype == "movie"
    assert item["id"] == 100

    # Tolerance exceeded: year 2020 should NOT match
    mtype, item = svc.find_best_match("Festival Film", 2020, media_list=mock_results)
    assert mtype is None
    assert item is None


def test_overseerr_find_best_match_tv_miniseries():
    mock_salem = [
        {"mediaType": "tv", "id": 16118, "name": "Salem's Lot", "firstAirDate": "1979-11-17"},
        {"mediaType": "movie", "id": 748230, "title": "Salem's Lot", "releaseDate": "2024-10-03"},
    ]
    svc = OverseerrService("http://fake-host:5055", "secret")

    # Excluded when allow_tv=False
    mtype, item = svc.find_best_match("Salem's Lot", "1979", media_list=mock_salem, allow_tv=False)
    assert mtype is None
    assert item is None

    # Matches when allow_tv=True
    mtype, item = svc.find_best_match("Salem's Lot", "1979", media_list=mock_salem, allow_tv=True)
    assert mtype == "tv"
    assert item["id"] == 16118


def test_overseerr_find_best_match_auto_searches_when_list_is_none():
    svc = OverseerrService("http://fake-host:5055", "secret")
    with patch.object(svc, "search") as mock_search:
        mock_search.return_value = [
            {"mediaType": "movie", "id": 550, "title": "Fight Club", "releaseDate": "1999-10-15"}
        ]
        mtype, item = svc.find_best_match("Fight Club", 1999)
        assert mtype == "movie"
        assert item["id"] == 550
        mock_search.assert_called_once_with("Fight Club")


def test_overseerr_find_best_match_empty_inputs():
    svc = OverseerrService("http://fake-host:5055", "secret")
    assert svc.find_best_match("", 1999) == (None, None)
    assert svc.find_best_match("   ", None) == (None, None)


@patch("requests.get")
def test_overseerr_get_media_details_success_and_error(mock_get):
    svc = OverseerrService("http://fake-host:5055", "secret", timeout=7.0)

    # Success
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": 123, "title": "Movie Details"}
    mock_get.return_value = mock_resp

    details = svc.get_media_details("movie", 123)
    assert details == {"id": 123, "title": "Movie Details"}
    mock_get.assert_called_once_with(
        "http://fake-host:5055/api/v1/movie/123",
        headers={"X-Api-Key": "secret"},
        timeout=7.0,
    )

    # Invalid media type
    assert svc.get_media_details("anime", 123) == {}

    # HTTP failure
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"
    assert svc.get_media_details("movie", 999) == {}

    # Network exception
    mock_get.side_effect = requests.RequestException("Timeout")
    assert svc.get_media_details("movie", 123) == {}


@patch("requests.post")
def test_overseerr_request_media_movie_and_tv(mock_post):
    svc = OverseerrService("http://fake-host:5055", "secret", timeout=9.0)

    # 1. Movie request (status 201)
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_post.return_value = mock_resp

    ok = svc.request_media("movie", 603, is_4k=False)
    assert ok is True
    mock_post.assert_called_once_with(
        "http://fake-host:5055/api/v1/request",
        headers={"X-Api-Key": "secret"},
        json={"mediaType": "movie", "mediaId": 603, "is4k": False},
        timeout=9.0,
    )

    # 2. TV request with seasons and 4K
    mock_post.reset_mock()
    mock_resp.status_code = 200
    ok_tv = svc.request_media("tv", "1399", seasons=[1, 2], is_4k=True)
    assert ok_tv is True
    mock_post.assert_called_once_with(
        "http://fake-host:5055/api/v1/request",
        headers={"X-Api-Key": "secret"},
        json={"mediaType": "tv", "mediaId": 1399, "is4k": True, "seasons": [1, 2]},
        timeout=9.0,
    )


def test_overseerr_request_media_invalid_id():
    svc = OverseerrService("http://fake-host:5055", "secret")
    assert svc.request_media("movie", "not_a_number") is False


@patch("requests.post")
def test_overseerr_request_media_failures(mock_post):
    svc = OverseerrService("http://fake-host:5055", "secret")

    # HTTP 400 error
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request"
    mock_post.return_value = mock_resp
    assert svc.request_media("movie", 603) is False

    # Network exception
    mock_post.side_effect = requests.RequestException("Connection error")
    assert svc.request_media("movie", 603) is False


# ==============================================================================
# RadarrService Tests
# ==============================================================================

def test_radarr_init_defaults_and_url_normalization():
    svc = RadarrService("http://fake-radarr:7878/", "radarr_key_456")
    assert svc.url == "http://fake-radarr:7878"
    assert svc.api_key == "radarr_key_456"
    assert svc.timeout == 10.0
    assert svc.is_configured is True
    assert svc._headers == {"X-Api-Key": "radarr_key_456"}


def test_radarr_unconfigured():
    svc = RadarrService("", "")
    assert svc.is_configured is False
    assert svc._headers == {}


def test_radarr_from_config_custom_and_default():
    cfg = {
        "radarr": {
            "url": "http://my-radarr:7878/",
            "api_key": "my_key",
            "timeout": 15.0,
        }
    }
    svc = RadarrService.from_config(cfg)
    assert svc.url == "http://my-radarr:7878"
    assert svc.api_key == "my_key"
    assert svc.timeout == 15.0

    with patch("core.config.load_config", return_value=cfg):
        svc_default = RadarrService.from_config()
        assert svc_default.url == "http://my-radarr:7878"
        assert svc_default.api_key == "my_key"


@patch("requests.get")
def test_radarr_get_movie_by_tmdb_id_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"id": 999, "title": "Old Movie", "tmdbId": 123}]
    mock_get.return_value = mock_resp

    svc = RadarrService("http://fake-radarr:7878", "secret", timeout=8.0)
    movie = svc.get_movie_by_tmdb_id(123)

    assert movie is not None
    assert movie["id"] == 999
    assert movie["title"] == "Old Movie"

    mock_get.assert_called_once_with(
        "http://fake-radarr:7878/api/v3/movie",
        params={"tmdbId": "123"},
        headers={"X-Api-Key": "secret"},
        timeout=8.0,
    )


@patch("requests.get")
def test_radarr_get_movie_by_tmdb_id_not_found_returns_none(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mock_get.return_value = mock_resp

    svc = RadarrService("http://fake-radarr:7878", "secret")
    assert svc.get_movie_by_tmdb_id(456) is None


def test_radarr_get_movie_by_tmdb_id_invalid_id():
    svc = RadarrService("http://fake-radarr:7878", "secret")
    assert svc.get_movie_by_tmdb_id(None) is None
    assert svc.get_movie_by_tmdb_id("") is None


@patch("requests.get")
def test_radarr_get_movie_by_tmdb_id_http_and_network_error(mock_get):
    svc = RadarrService("http://fake-radarr:7878", "secret")

    # HTTP 500 error
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Server Error"
    mock_get.return_value = mock_resp
    assert svc.get_movie_by_tmdb_id(123) is None

    # Network exception
    mock_get.side_effect = requests.RequestException("Timeout connecting to Radarr")
    assert svc.get_movie_by_tmdb_id(123) is None


@patch("requests.delete")
def test_radarr_delete_movie_success(mock_delete):
    svc = RadarrService("http://fake-radarr:7878", "secret", timeout=9.5)

    # Status 200 with default params
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_delete.return_value = mock_resp

    ok = svc.delete_movie(999)
    assert ok is True
    mock_delete.assert_called_once_with(
        "http://fake-radarr:7878/api/v3/movie/999",
        params={"deleteFiles": "true", "addImportExclusion": "false"},
        headers={"X-Api-Key": "secret"},
        timeout=9.5,
    )

    # Status 202 with custom params
    mock_delete.reset_mock()
    mock_resp.status_code = 202
    ok_custom = svc.delete_movie(999, delete_files=False, add_import_exclusion=True)
    assert ok_custom is True
    mock_delete.assert_called_once_with(
        "http://fake-radarr:7878/api/v3/movie/999",
        params={"deleteFiles": "false", "addImportExclusion": "true"},
        headers={"X-Api-Key": "secret"},
        timeout=9.5,
    )


def test_radarr_delete_movie_invalid_id():
    svc = RadarrService("http://fake-radarr:7878", "secret")
    assert svc.delete_movie("not_an_id") is False


@patch("requests.delete")
def test_radarr_delete_movie_failures(mock_delete):
    svc = RadarrService("http://fake-radarr:7878", "secret")

    # HTTP 404 error
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Movie Not Found"
    mock_delete.return_value = mock_resp
    assert svc.delete_movie(999) is False

    # Network exception
    mock_delete.side_effect = requests.RequestException("Radarr unreachable")
    assert svc.delete_movie(999) is False


# ==============================================================================
# Letterboxd Service Tests
# ==============================================================================

def test_build_list_prompt_formatting():
    prompt = build_list_prompt("Top 10 Sci-Fi", 10)
    assert "Top 10 Sci-Fi" in prompt
    assert "(10 films)" in prompt
    assert "Reply *1* to Download & create Plex Collection" in prompt
    assert "Reply *2* to Download only" in prompt
    assert "Reply *0* to Cancel" in prompt


def test_build_list_prompt_fallback_title():
    prompt = build_list_prompt("", 5)
    assert "Letterboxd List" in prompt


@patch("time.sleep", return_value=None)
@patch("requests.post")
@patch("requests.Session.get")
def test_fetch_letterboxd_html_direct_success(mock_get, mock_post, mock_sleep):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body>Film List</body></html>"
    mock_resp.url = "https://letterboxd.com/user/list/test/"
    mock_get.return_value = mock_resp

    html, resolved_url = fetch_letterboxd_html("https://letterboxd.com/user/list/test/")
    assert html == "<html><body>Film List</body></html>"
    assert resolved_url == "https://letterboxd.com/user/list/test/"
    mock_post.assert_not_called()


@patch("time.sleep", return_value=None)
@patch("requests.post")
@patch("requests.Session.get")
def test_fetch_letterboxd_html_flaresolverr_fallback(mock_get, mock_post, mock_sleep):
    # Direct GET returns 403 Cloudflare challenge
    mock_get.return_value = MagicMock(status_code=403)

    # FlareSolverr returns solved HTML
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json.return_value = {
        "solution": {
            "response": "<html><body>Solved via FlareSolverr</body></html>",
            "url": "https://letterboxd.com/user/list/test/resolved",
        }
    }
    mock_post.return_value = mock_post_resp

    html, resolved_url = fetch_letterboxd_html("https://letterboxd.com/user/list/test/")
    assert html == "<html><body>Solved via FlareSolverr</body></html>"
    assert resolved_url == "https://letterboxd.com/user/list/test/resolved"


@patch("services.letterboxd.fetch_letterboxd_html")
def test_scrape_letterboxd_list_multi_page(mock_fetch):
    page1 = """
    <html>
      <head><meta property="og:title" content="Best 90s Thrillers • Letterboxd" /></head>
      <body>
        <div class="react-component" data-component-class="LazyPoster" data-item-name="Se7en (1995)"></div>
        <div class="react-component" data-component-class="LazyPoster" data-item-name="Fargo (1996)"></div>
        <a class="next" href="/user/list/90s-thrillers/page/2/">Next</a>
      </body>
    </html>
    """
    page2 = """
    <html>
      <body>
        <div class="react-component" data-component-class="LazyPoster" data-item-name="The Usual Suspects (1995)"></div>
      </body>
    </html>
    """

    def fake_fetch(url, session=None):
        if "page/2" in url:
            return page2, url
        return page1, url

    mock_fetch.side_effect = fake_fetch

    title, films = scrape_letterboxd_list("https://letterboxd.com/user/list/90s-thrillers/")
    assert title == "Best 90s Thrillers"
    assert films == ["Se7en (1995)", "Fargo (1996)", "The Usual Suspects (1995)"]


def test_extract_letterboxd_film_metadata():
    html_movie = """
    <html>
      <head><meta property="og:type" content="video.movie" /><meta property="og:title" content="Fight Club • Letterboxd" /></head>
      <body data-tmdb-id="550"></body>
    </html>
    """
    tmdb_id, title = extract_letterboxd_film_metadata(html_movie)
    assert tmdb_id == 550
    assert title == "Fight Club"

    html_non_movie = "<html><head><meta property=\"og:type\" content=\"website\" /></head></html>"
    tmdb_id_none, title_none = extract_letterboxd_film_metadata(html_non_movie)
    assert tmdb_id_none is None
    assert title_none is None


# ==============================================================================
# CollectionTracker Tests
# ==============================================================================

def test_sync_tracked_collections_reconciles_by_tmdb_and_title(tmp_path):
    tracked_file = tmp_path / "tracked_collections.json"
    tracked_data = {
        "Cult Classics": {
            "title": "Cult Classics",
            "sender": "whatsapp:+15551234567",
            "protect": True,
            "promote": True,
            "items": {
                "1001": {"title": "Movie A", "status": "pending", "ratingKey": None},
                "1002": {"title": "Movie B", "status": "pending", "ratingKey": None},
            },
        }
    }
    tracked_file.write_text(json.dumps(tracked_data), encoding="utf-8")

    mock_plex = MagicMock()
    movie_a = MagicMock()
    movie_a.tmdb_id = 1001
    movie_a.ratingKey = 201
    movie_a.title = "Movie A"

    movie_b = MagicMock()
    movie_b.tmdb_id = None
    movie_b.ratingKey = 202
    movie_b.title = "Movie B"

    mock_plex.fetch_movies.return_value = [movie_a, movie_b]

    tracker = CollectionTracker(plex=mock_plex, storage_path=tracked_file)
    tracker.sync_tracked_collections()

    updated = json.loads(tracked_file.read_text(encoding="utf-8"))
    items = updated["Cult Classics"]["items"]
    assert items["1001"]["status"] == "available"
    assert items["1001"]["ratingKey"] == 201
    assert items["1002"]["status"] == "available"
    assert items["1002"]["ratingKey"] == 202

    mock_plex.add_to_collection.assert_called_once_with("Cult Classics", [201, 202], promote=True)
    mock_plex.add_label.assert_called_once_with([201, 202], "Keep Forever")


def test_handle_media_available_adds_to_collection(tmp_path):
    tracked_file = tmp_path / "tracked_collections.json"
    tracked_data = {
        "Sci-Fi Essentials": {
            "title": "Sci-Fi Essentials",
            "sender": "whatsapp:+15551234567",
            "protect": True,
            "promote": True,
            "items": {
                "999": {"title": "Arrival", "status": "pending", "ratingKey": None},
            },
        }
    }
    tracked_file.write_text(json.dumps(tracked_data), encoding="utf-8")

    mock_plex = MagicMock()
    arrival = MagicMock()
    arrival.tmdb_id = 999
    arrival.ratingKey = 501
    arrival.title = "Arrival"
    mock_plex.fetch_movies.return_value = [arrival]

    tracker = CollectionTracker(plex=mock_plex, storage_path=tracked_file)
    matches = tracker.handle_media_available(999, title_hint="Arrival", retry_delay=0.0)

    assert len(matches) == 1
    assert matches[0]["collection_title"] == "Sci-Fi Essentials"
    assert matches[0]["ready_count"] == 1
    assert matches[0]["total_count"] == 1
    assert matches[0]["sender"] == "whatsapp:+15551234567"

    mock_plex.add_to_collection.assert_called_once_with("Sci-Fi Essentials", [501], promote=True)
    mock_plex.add_label.assert_called_once_with([501], "Keep Forever")


def test_process_letterboxd_collection_flow(tmp_path):
    tracked_file = tmp_path / "tracked_collections.json"
    tracked_file.write_text("{}", encoding="utf-8")

    mock_plex = MagicMock()
    movie_in_plex = MagicMock()
    movie_in_plex.ratingKey = 101
    movie_in_plex.tmdb_id = 1001
    movie_in_plex.title = "Existing Film"
    mock_plex.fetch_movies.return_value = [movie_in_plex]

    mock_overseerr = MagicMock()
    def fake_search(title):
        if title == "Existing Film":
            return [{"id": 1001, "mediaType": "movie", "title": "Existing Film", "releaseDate": "2020-01-01"}]
        elif title == "Missing Film":
            return [{"id": 1002, "mediaType": "movie", "title": "Missing Film", "releaseDate": "2021-01-01"}]
        return []

    mock_overseerr.search.side_effect = fake_search
    mock_overseerr.find_best_match.side_effect = lambda t, target_year=None, media_list=None, allow_tv=False: (
        ("movie", media_list[0]) if media_list else (None, None)
    )
    mock_overseerr.request_media.return_value = True

    mock_notifier = MagicMock()

    tracker = CollectionTracker(plex=mock_plex, overseerr=mock_overseerr, storage_path=tracked_file)
    res = tracker.process_letterboxd_collection(
        sender="whatsapp:+15551234567",
        list_title="My List",
        titles=["Existing Film", "Missing Film (2021)"],
        create_collection=True,
        notifier=mock_notifier,
    )

    assert res["list_title"] == "My List"
    assert res["already_in_plex_keys"] == [101]
    assert res["requested"] == ["Missing Film"]

    mock_plex.add_to_collection.assert_called_once_with("My List", [101], promote=True)
    mock_plex.add_label.assert_called_once_with([101], "Keep Forever")
    mock_overseerr.request_media.assert_called_once_with("movie", 1002)
    mock_notifier.send_message.assert_called_once()


@patch("requests.get")
def test_overseerr_search_percent_encodes_query(mock_get):
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"results": []})

    svc = OverseerrService("http://fake-host:5055", "secret")
    svc.search("Prey for Rock & Roll")

    assert mock_get.call_args.kwargs["params"] == "query=Prey%20for%20Rock%20%26%20Roll"
