import os
import pytest
from mellea import start_session
from whatsapp_bot import parse_movie

# Check if Gemini API Key is available
HAS_GEMINI = "GEMINI_API_KEY" in os.environ or any(
    line.startswith("  api_key:") for line in open(os.path.join(os.path.dirname(__file__), '..', 'config.yaml'), encoding='utf-8')
    if os.path.exists(os.path.join(os.path.dirname(__file__), '..', 'config.yaml'))
)

@pytest.mark.skipif(not HAS_GEMINI, reason="No Gemini API Key found")
def test_parse_movie_complex_query():
    # Make sure we load the key from config if it's there
    import whatsapp_bot
    whatsapp_bot.init_config()
    
    with start_session("litellm", whatsapp_bot.AI_MODEL) as session:
        intent = parse_movie(m=session, text="that movie with the guy who has scissors for hands from 1990")
        
    assert "Edward Scissorhands" in intent.title
    assert intent.year == 1990
