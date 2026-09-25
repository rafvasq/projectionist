import os
import pytest
import requests
import yaml

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')

@pytest.fixture(scope="module")
def overseerr_config():
    if not os.path.exists(CONFIG_PATH):
        pytest.skip("No config.yaml found, cannot test live Overseerr API")
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    os_cfg = config.get('overseerr', {})
    url = os_cfg.get('url', '').rstrip('/')
    api_key = os_cfg.get('api_key')
    if not url or not api_key:
        pytest.skip("Overseerr URL or API Key missing from config")
    return {"url": url, "headers": {"X-Api-Key": api_key}}

def test_overseerr_search_schema(overseerr_config):
    """
    Validates that a live search to Overseerr returns the expected JSON structure.
    This ensures our mocks in test_whatsapp_bot.py remain accurate.
    """
    url = overseerr_config["url"]
    headers = overseerr_config["headers"]
    
    # Use a known query that will definitely have results
    res = requests.get(f"{url}/api/v1/search?query=Inception", headers=headers)
    assert res.status_code == 200, f"Failed to search Overseerr: {res.text}"
    
    data = res.json()
    assert "results" in data, "Response missing 'results' key"
    
    results = data["results"]
    assert len(results) > 0, "Expected at least one result for Inception"
    
    first_result = results[0]
    
    # Assert essential schema fields exist
    assert "id" in first_result
    assert "mediaType" in first_result
    
    # Ensure title or name exists depending on mediaType
    if first_result["mediaType"] == "movie":
        assert "title" in first_result
        assert "releaseDate" in first_result
    elif first_result["mediaType"] == "tv":
        assert "name" in first_result
        assert "firstAirDate" in first_result
    else:
        pytest.fail(f"Unknown mediaType: {first_result['mediaType']}")

def test_overseerr_tv_details_schema(overseerr_config):
    """
    Validates that a live GET for TV show details returns expected schema.
    Specifically checks for the 'seasons' array and 'seasonNumber'.
    """
    url = overseerr_config["url"]
    headers = overseerr_config["headers"]
    
    # Search for a popular TV show to get its ID (e.g. Breaking Bad)
    search_res = requests.get(f"{url}/api/v1/search?query=Breaking%20Bad", headers=headers)
    tv_id = None
    for r in search_res.json().get("results", []):
        if r.get("mediaType") == "tv":
            tv_id = r["id"]
            break
            
    if not tv_id:
        pytest.skip("Could not find a TV show to test details endpoint")
        
    res = requests.get(f"{url}/api/v1/tv/{tv_id}", headers=headers)
    assert res.status_code == 200
    
    data = res.json()
    assert "seasons" in data, "TV show details missing 'seasons' array"
    
    seasons = data["seasons"]
    assert isinstance(seasons, list)
    
    if len(seasons) > 0:
        first_season = seasons[0]
        assert "seasonNumber" in first_season, "Season missing 'seasonNumber'"
