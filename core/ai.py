"""
core/ai.py
Core AI module for Projectionist.
Defines Pydantic models, generative AI curation and intent parsing, and environment setup.
"""

from __future__ import annotations
import os
from contextlib import contextmanager
from typing import Any, Generator, Optional

from pydantic import BaseModel, Field
from mellea import generative, start_session


class CurationResult(BaseModel):
    """Result schema for generative film collection curation."""
    name: str = Field(description="The creative, evocative name for the collection (3-6 words).")
    keys: list[int] = Field(description="The ratingKeys of 10-20 films from the library that fit the theme.", min_length=10)


class MovieIntent(BaseModel):
    """Extracted intent from a natural language movie query."""
    title: str = Field(description="The exact official title of the movie or TV show. Resolve trivia to actual titles.")
    year: Optional[int] = Field(default=None, description="The release year if specified, else None.")


@generative
def curate_collection(movies_json: str, exclude_themes_str: str) -> CurationResult:
    """You are a creative film curator for a small, personal Plex library.
    Look at this entire movie collection and invent ONE unexpected, thematic collection that would delight the owner. The collection should have a creative, evocative name and a distinct mood or theme.
    {exclude_themes_str}

    Rules:
    - Pick 10–20 films that genuinely fit your theme
    - The name should be punchy and fun (3–6 words)
    - Be creative — think beyond obvious genres

    Movie library:
    {movies_json}
    """
    ...


@generative
def parse_movie(text: str) -> MovieIntent:
    """You are a movie concierge. The user will give you a text query that might be an exact title, or a piece of trivia (e.g. 'the movie with the guy who has scissors for hands'). Resolve the trivia to the exact official movie or TV show title. Extract the year if provided."""
    ...


def setup_ai_env(cfg: dict[str, Any], default_model: str = "gemini-3.5-flash-lite") -> str:
    """
    Configure environment variables for AI backend (Gemini or Ollama) and return model string.
    Raises ValueError for unsupported providers.
    """
    ai_cfg = cfg.get("ai", {}) if isinstance(cfg, dict) else {}
    provider_name = ai_cfg.get("provider", "gemini")
    model = ai_cfg.get("model", "")
    api_key = ai_cfg.get("api_key", "")

    if provider_name == "gemini":
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        model_name = model or default_model
        return f"gemini/{model_name}"

    if provider_name == "ollama":
        base_url = ai_cfg.get("base_url", "http://localhost:11434")
        os.environ["OLLAMA_API_BASE"] = base_url
        model_name = model or "llama3"
        return f"ollama/{model_name}"

    raise ValueError(f"Unknown AI provider '{provider_name}'. Choose: gemini, ollama")



@contextmanager
def ai_session(model: str, backend: str = "litellm") -> Generator[Any, None, None]:
    """Context manager for running Mellea AI sessions."""
    with start_session(backend, model) as session:
        yield session
