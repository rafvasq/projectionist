import os
from pathlib import Path
import pytest
from mellea import start_session
from core.ai import read_movie, MovieIntent, setup_ai_env
from core.config import load_config

# Check if Gemini API Key is available
project_root = Path(__file__).resolve().parent.parent.parent
config_path = str(project_root / "config.yaml")
HAS_GEMINI = "GEMINI_API_KEY" in os.environ or (
    os.path.exists(config_path) and 
    any(line.strip().startswith("api_key:") for line in open(config_path, encoding='utf-8'))
)

@pytest.mark.live_llm
@pytest.mark.skipif(not HAS_GEMINI, reason="No Gemini API Key found")
def test_parse_movie_complex_query():
    cfg = load_config(config_path)
    model = setup_ai_env(cfg)

    with start_session("litellm", model) as session:
        intent = read_movie(m=session, text="that movie with the guy who has scissors for hands from 1990")

    assert "Edward Scissorhands" in intent.title
    assert intent.year == 1990


def test_movie_intent_offline_validation():
    intent = MovieIntent(title="Edward Scissorhands", year=1990)
    assert intent.title == "Edward Scissorhands"
    assert intent.year == 1990

    intent_no_year = MovieIntent(title="Inception", year=None)
    assert intent_no_year.title == "Inception"
    assert intent_no_year.year is None
