import os
import json
import pytest
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup
import whatsapp_bot

@pytest.fixture
def client(tmp_path):
    # Set up a temporary state file
    state_file = tmp_path / "whatsapp_state.json"
    
    # Configure bot with safe test values
    test_config = {
        "whatsapp": {
            "allowed_numbers": ["whatsapp:+14168589652"],
            "twilio_account_sid": "test_sid",
            "twilio_auth_token": "test_token",
            "twilio_phone_number": "whatsapp:+13438423015"
        },
        "overseerr": {
            "url": "http://fake-overseerr:5055",
            "api_key": "fake_key"
        }
    }
    
    with patch('whatsapp_bot.STATE_FILE', str(state_file)):
        whatsapp_bot.init_config(test_config)
        whatsapp_bot.app.config['TESTING'] = True
        with whatsapp_bot.app.test_client() as client:
            yield client

def test_unauthorized_number(client):
    res = client.post('/webhook/twilio', data={
        "From": "whatsapp:+19999999999",
        "Body": "Hello"
    })
    assert res.status_code == 200
    assert "<Message>" not in res.text

def test_universal_cancel(client):
    # First set some state
    state_data = {"whatsapp:+14168589652": {"status": "WAITING_CONFIRM"}}
    with open(whatsapp_bot.STATE_FILE, 'w') as f:
        json.dump(state_data, f)
        
    res = client.post('/webhook/twilio', data={
        "From": "whatsapp:+14168589652",
        "Body": "0"
    })
    
    assert "Search cancelled" in res.text
    
    # Verify state was reset
    with open(whatsapp_bot.STATE_FILE, 'r') as f:
        state = json.load(f)
    assert state["whatsapp:+14168589652"]["status"] == "IDLE"

@patch('whatsapp_bot.search_overseerr_list')
@patch('whatsapp_bot.parse_movie')
def test_search_movie_flow(mock_parse_movie, mock_search, client):
    # Mock Gemini extraction
    mock_intent = MagicMock()
    mock_intent.title = "The Matrix"
    mock_intent.year = 1999
    mock_parse_movie.return_value = mock_intent
    
    # Mock Overseerr search results
    mock_search.return_value = [
        {"id": 603, "mediaType": "movie", "title": "The Matrix", "releaseDate": "1999-03-30"},
        {"id": 604, "mediaType": "movie", "title": "The Matrix Reloaded", "releaseDate": "2003-05-15"}
    ]
    
    # Send a natural query
    res = client.post('/webhook/twilio', data={
        "From": "whatsapp:+14168589652",
        "Body": "the matrix 1999"
    })
    
    assert "Reply with a number to request" in res.text
    assert "The Matrix (1999)" in res.text
    
    # Check that state was updated to waiting for confirmation
    with open(whatsapp_bot.STATE_FILE, 'r') as f:
        state = json.load(f)
    user_state = state["whatsapp:+14168589652"]
    assert user_state["status"] == "WAITING_CONFIRM"
    assert "1" in user_state["options"]
    assert user_state["options"]["1"]["tmdbId"] == 603

@patch('whatsapp_bot.request_overseerr')
def test_confirm_movie_request(mock_request_os, client):
    # Setup state as if user is waiting to confirm
    state_data = {
        "whatsapp:+14168589652": {
            "status": "WAITING_CONFIRM",
            "options": {
                "1": {"tmdbId": 603, "title": "The Matrix (1999)", "mediaType": "movie"}
            }
        }
    }
    with open(whatsapp_bot.STATE_FILE, 'w') as f:
        json.dump(state_data, f)
        
    mock_request_os.return_value = True
    
    # User replies with "1"
    res = client.post('/webhook/twilio', data={
        "From": "whatsapp:+14168589652",
        "Body": "1"
    })
    
    assert "Requested *The Matrix (1999)*" in res.text
    mock_request_os.assert_called_once_with("movie", 603)
    
    # Verify state was reset
    with open(whatsapp_bot.STATE_FILE, 'r') as f:
        state = json.load(f)
    assert state["whatsapp:+14168589652"]["status"] == "IDLE"

def test_tv_show_flow(client):
    # Setup state as if user selected a TV show
    state_data = {
        "whatsapp:+14168589652": {
            "status": "WAITING_CONFIRM",
            "options": {
                "2": {"tmdbId": 1399, "title": "Game of Thrones (2011)", "mediaType": "tv"}
            }
        }
    }
    with open(whatsapp_bot.STATE_FILE, 'w') as f:
        json.dump(state_data, f)
        
    # User replies with "2"
    res = client.post('/webhook/twilio', data={
        "From": "whatsapp:+14168589652",
        "Body": "2"
    })
    
    assert "Game of Thrones (2011)* is a TV Show" in res.text
    assert "Reply *1* for All Seasons" in res.text
    
    # Verify state moved to WAITING_SEASON
    with open(whatsapp_bot.STATE_FILE, 'r') as f:
        state = json.load(f)
    assert state["whatsapp:+14168589652"]["status"] == "WAITING_SEASON"

@patch('whatsapp_bot.get_media_details')
@patch('whatsapp_bot.request_overseerr')
def test_confirm_tv_season(mock_request_os, mock_details, client):
    # Setup state
    state_data = {
        "whatsapp:+14168589652": {
            "status": "WAITING_SEASON",
            "tmdbId": 1399,
            "title": "Game of Thrones (2011)"
        }
    }
    with open(whatsapp_bot.STATE_FILE, 'w') as f:
        json.dump(state_data, f)
        
    mock_details.return_value = {
        "seasons": [
            {"seasonNumber": 0}, # Specials
            {"seasonNumber": 1},
            {"seasonNumber": 2}
        ]
    }
    mock_request_os.return_value = True
    
    # User replies with "1" (All Seasons)
    res = client.post('/webhook/twilio', data={
        "From": "whatsapp:+14168589652",
        "Body": "1"
    })
    
    assert "Requested *Game of Thrones (2011)*" in res.text
    mock_request_os.assert_called_once_with("tv", 1399, seasons=[1, 2])

@patch('whatsapp_bot.send_whatsapp_message')
def test_overseerr_webhook(mock_send, client):
    res = client.post('/webhook/overseerr', json={
        "notification_type": "MEDIA_AVAILABLE",
        "subject": "The Matrix",
        "media": {"title": "The Matrix", "year": "1999"}
    })
    
    assert res.status_code == 200
    # Should send to all allowed numbers
    assert mock_send.call_count == 1
    args, kwargs = mock_send.call_args
    assert args[0] == "whatsapp:+14168589652"
    assert "The Matrix" in args[1]
    
@patch('whatsapp_bot.requests.get')
@patch('whatsapp_bot.requests.post')
def test_api_calls_internal(mock_post, mock_get):
    # Test the internal functions that format requests
    # get_media_details
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"id": 1}
    
    res = whatsapp_bot.get_media_details("movie", 1)
    assert res == {"id": 1}
    assert mock_get.call_args[0][0].endswith("/api/v1/movie/1")
    
    # request_overseerr
    mock_post.return_value.status_code = 201
    success = whatsapp_bot.request_overseerr("movie", 1)
    assert success is True
    assert mock_post.call_args[0][0].endswith("/api/v1/request")
    assert mock_post.call_args[1]["json"] == {"mediaType": "movie", "mediaId": 1, "is4k": False}
