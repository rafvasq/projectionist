import pytest
from unittest.mock import patch, MagicMock, mock_open
import json
from datetime import datetime, timedelta
import cleaner

@pytest.fixture
def mock_config():
    return {
        "deletion": {
            "enabled": True,
            "dry_run": False,
            "max_age_days": 120,
            "protect_tag": "Keep Forever"
        },
        "radarr": {
            "url": "http://fake-radarr",
            "api_key": "fake_key"
        },
        "whatsapp": {
            "enabled": True,
            "twilio_account_sid": "sid",
            "twilio_auth_token": "token",
            "twilio_phone_number": "whatsapp:+13438423015",
            "allowed_numbers": ["whatsapp:+14168589652"]
        }
    }

@patch('cleaner.argparse.ArgumentParser.parse_args')
@patch('cleaner.yaml.safe_load')
@patch('cleaner.from_config')
@patch('cleaner.requests.get')
@patch('cleaner.requests.delete')
@patch('builtins.open', new_callable=mock_open)
def test_cleaner_run_deletion(mock_file, mock_delete, mock_get, mock_from_config, mock_yaml, mock_args, mock_config):
    # Setup args
    mock_args.return_value.run_deletion = True
    mock_args.return_value.send_notification = False
    
    mock_yaml.return_value = mock_config
    
    # Setup Plex mock
    mock_plex = MagicMock()
    mock_from_config.return_value = mock_plex
    
    # Fake movies
    old_movie = MagicMock()
    old_movie.title = "Old Movie"
    old_movie.tmdb_id = "123"
    old_movie.added_at = datetime.now() - timedelta(days=130)
    old_movie.last_viewed_at = datetime.now() - timedelta(days=30)
    old_movie.collections = []
    old_movie.labels = []
    
    protected_movie = MagicMock()
    protected_movie.title = "Protected Movie"
    protected_movie.added_at = datetime.now() - timedelta(days=150)
    protected_movie.last_viewed_at = None
    protected_movie.collections = ["Keep Forever"]
    protected_movie.labels = []
    
    recent_movie = MagicMock()
    recent_movie.title = "Recent Movie"
    recent_movie.added_at = datetime.now() - timedelta(days=10)
    recent_movie.last_viewed_at = None
    recent_movie.collections = []
    recent_movie.labels = []
    
    mock_plex.fetch_movies.return_value = [old_movie, protected_movie, recent_movie]
    
    # Setup Radarr mock
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = [{"id": 999}]
    mock_delete.return_value.status_code = 200
    
    cleaner.run()
    
    # Assert
    mock_plex.connect.assert_called_once()
    mock_get.assert_called_once_with("http://fake-radarr/api/v3/movie?tmdbId=123", headers={"X-Api-Key": "fake_key"})
    mock_delete.assert_called_once_with("http://fake-radarr/api/v3/movie/999?deleteFiles=true&addImportExclusion=false", headers={"X-Api-Key": "fake_key"})
    
    # Check that summary file was written
    written_data = "".join(call.args[0] for call in mock_file().write.call_args_list)
    assert "Old Movie" in written_data

@patch('cleaner.argparse.ArgumentParser.parse_args')
@patch('cleaner.yaml.safe_load')
@patch('cleaner.os.path.exists')
@patch('cleaner.os.remove')
@patch('cleaner.Client')
@patch('builtins.open', new_callable=mock_open)
def test_cleaner_send_notification(mock_file, mock_twilio_client, mock_remove, mock_exists, mock_yaml, mock_args, mock_config):
    mock_args.return_value.run_deletion = False
    mock_args.return_value.send_notification = True
    
    mock_yaml.return_value = mock_config
    mock_exists.return_value = True
    
    # Mock summary file contents (first read is config, second is summary)
    mock_file.side_effect = [
        mock_open(read_data="").return_value, # Config (handled by yaml mock)
        mock_open(read_data=json.dumps({
            "deleted_titles": ["Old Movie"],
            "failed_titles": [],
            "dry_run": False
        })).return_value
    ]
    
    mock_twilio_instance = MagicMock()
    mock_twilio_client.return_value = mock_twilio_instance
    
    cleaner.run()
    
    mock_twilio_instance.messages.create.assert_called_once()
    args, kwargs = mock_twilio_instance.messages.create.call_args
    assert "Old Movie" in kwargs["body"]
    assert kwargs["to"] == "whatsapp:+14168589652"
