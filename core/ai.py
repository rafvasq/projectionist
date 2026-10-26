"""
core/ai.py
Core AI module for Projectionist.
Defines Pydantic models, generative AI curation and intent parsing, and environment setup.
"""

from __future__ import annotations
import json
import os
from datetime import date
from contextlib import contextmanager
from typing import Any, Generator, Optional

from pydantic import BaseModel, Field
from mellea import generative, start_session
from mellea.stdlib.requirements import req, simple_validate
from mellea.stdlib.sampling import RejectionSamplingStrategy

MIN_ROW_KEYS = 10
RETRY_BUDGET = 3
MIN_FILM_YEAR = 1888      # the first film
INTENT_RETRY_BUDGET = 2


class CurationResult(BaseModel):
    """Result schema for generative film collection curation."""
    name: str = Field(description="The creative, evocative name for the collection (3-6 words).")
    keys: list[int] = Field(description="The ratingKeys of 10-20 films from the library that fit the theme.")


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


def curate_row(m: Any, movies_json: str, exclude_themes_str: str, valid_keys: set[int]) -> CurationResult:
    """
    Ask the model for one themed row of films. A retry is triggered when it names a film that is
    not in the library or returns too few. When the retries run out the framework hands back a
    failing answer without raising, so the keys are checked again here and unknown ones dropped.
    Raises ValueError when fewer than MIN_ROW_KEYS valid films remain.
    """
    def keys_in_library(output: str) -> bool:
        try:
            keys = json.loads(output)["result"]["keys"]
        except (ValueError, KeyError, TypeError):
            return False
        return len(set(keys)) >= MIN_ROW_KEYS and all(k in valid_keys for k in keys)

    result = curate_collection(
        m=m,
        movies_json=movies_json,
        exclude_themes_str=exclude_themes_str,
        requirements=[req(
            f"Use only ratingKeys from the library and return at least {MIN_ROW_KEYS}.",
            validation_fn=simple_validate(keys_in_library, reason="A key was not in the library, or there were too few."),
        )],
        strategy=RejectionSamplingStrategy(loop_budget=RETRY_BUDGET),
    )
    valid = [k for k in dict.fromkeys(result.keys) if k in valid_keys]
    if len(valid) < MIN_ROW_KEYS:
        raise ValueError(f"only {len(valid)} of {len(result.keys)} returned keys are in the library")
    return CurationResult(name=result.name, keys=valid)


def _year_is_plausible(year: Optional[int]) -> bool:
    return year is None or MIN_FILM_YEAR <= year <= date.today().year + 2


def read_movie(m: Any, text: str) -> MovieIntent:
    """
    Resolve a message to a title and an optional year with parse_movie. A missing title or an
    implausible year triggers one retry; if the year is still implausible it is dropped and the
    title kept. Raises ValueError when no title comes back, so the caller falls back to the raw text.
    """
    def sensible(output: str) -> bool:
        try:
            found = json.loads(output)["result"]
        except (ValueError, KeyError, TypeError):
            return False
        return bool((found.get("title") or "").strip()) and _year_is_plausible(found.get("year"))

    intent = parse_movie(
        m=m,
        text=text,
        requirements=[req(
            "Give a non-empty title, and a release year between 1888 and two years from now, or no year.",
            validation_fn=simple_validate(sensible, reason="The title was empty or the year was not a plausible film year."),
        )],
        strategy=RejectionSamplingStrategy(loop_budget=INTENT_RETRY_BUDGET),
    )
    if not intent.title.strip():
        raise ValueError("no title in the model's answer")
    if not _year_is_plausible(intent.year):
        return MovieIntent(title=intent.title, year=None)
    return intent


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
