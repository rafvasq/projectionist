"""
Projectionist — main entry point.
Fetches Plex metadata, generates AI-curated collections, writes them back.
"""

from __future__ import annotations

import json
import logging
import random
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from mellea import generative, start_session

from plex_client import PlexClient, from_config

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 15
DEFAULT_COOLDOWN_WEEKS = 3
COOLDOWN_FILE = "data/recently_surfaced.json"


class CurationResult(BaseModel):
    name: str = Field(description="The creative, evocative name for the collection (3-6 words).")
    keys: list[int] = Field(description="The ratingKeys of 10-20 films from the library that fit the theme.", min_length=10)


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


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _pick(keys: list[int], seen: set[int], max_results: int) -> list[int]:
    """
    Shuffle candidates, exclude already-used keys, cap at max_results.
    Updates seen in-place so subsequent rows don't repeat the same titles.
    """
    random.shuffle(keys)
    fresh = [k for k in keys if k not in seen][:max_results]
    seen.update(fresh)
    return fresh


def _build_payload(movies) -> list[dict]:
    """Convert MovieMeta list to compact dicts for the AI provider."""
    return [
        {
            "ratingKey": m.ratingKey,
            "title": m.title,
            "year": m.year,
            "genres": m.genres,
            "rating": m.rating_pct,
        }
        for m in movies
    ]


def _load_cooldown(path: Path, max_weeks: int) -> tuple[list[dict], set[int]]:
    """Load cooldown history, prune old entries, return (history, cooled_keys)."""
    if not path.exists():
        return [], set()
    try:
        with open(path) as f:
            history = json.load(f)
    except Exception:
        return [], set()

    cutoff = datetime.now() - timedelta(weeks=max_weeks)
    recent = [
        entry for entry in history
        if datetime.fromisoformat(entry["date"]) > cutoff
    ]

    cooled: set[int] = set()
    for entry in recent:
        cooled.update(entry.get("keys", []))

    return recent, cooled


def _save_cooldown(path: Path, history: list[dict], new_keys: list[int]) -> None:
    """Append current run's keys to history and save."""
    history.append({
        "date": datetime.now().isoformat()[:10],
        "keys": new_keys,
    })
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as exc:
        logger.warning("Could not save cooldown file: %s", exc)


def _delete_previous_collections(client: PlexClient, state_path: Path) -> None:
    """Delete all collections generated in the previous run."""
    if not state_path.exists():
        return
    try:
        with open(state_path) as f:
            records = json.load(f)
        if isinstance(records, list):
            for rec in records:
                if isinstance(rec, dict):
                    title = rec.get("title")
                    lib_name = rec.get("library")
                    if title:
                        client.delete_collection(title, library_name=lib_name)
    except Exception as exc:
        logger.warning("Could not delete previous collections: %s", exc)


def _save_state(state_path: Path, records: list[dict[str, str]]) -> None:
    """Save all generated collection details to the state file."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w") as f:
            json.dump(records, f, indent=2)
    except Exception as exc:
        logger.warning("Could not save state file: %s", exc)


def run(config_path: str = "config.yaml") -> None:
    cfg = load_config(config_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    client = from_config(cfg)
    client.connect()

    # State setup and cleanup
    state_path = Path(cfg.get("state_path", "data/state.json"))
    _delete_previous_collections(client, state_path)

    rows_cfg = cfg.get("rows", {})
    count = rows_cfg.get("count", 1)
    max_results = rows_cfg.get("max_results", DEFAULT_MAX_RESULTS)
    cooldown_weeks = rows_cfg.get("cooldown_weeks", DEFAULT_COOLDOWN_WEEKS)

    # Load cooldown
    cooldown_path = Path(cfg.get("cooldown_path", COOLDOWN_FILE))
    cooldown_history, cooldown_keys = _load_cooldown(cooldown_path, cooldown_weeks)

    # Fetch movies
    movies = client.fetch_movies()
    logger.info("Movies library: %d titles", len(movies))

    # Filter out watched movies completely
    movies = [m for m in movies if not m.watched]
    logger.info("Unwatched movies: %d titles", len(movies))

    ai_model = _setup_ai_env(cfg)

    movie_seen: set[int] = set(cooldown_keys)
    exclude_themes: list[str] = []
    collections_written: list[tuple[str, int]] = []
    collections_created_records: list[dict[str, str]] = []
    all_surfaced_keys: list[int] = []

    for i in range(count):
        # Dynamically build payload to exclude already seen/surfaced movies
        available_movies = [m for m in movies if m.ratingKey not in movie_seen]
        if len(available_movies) < 10:
            logger.warning("Not enough eligible movies left (%d) to curate row. Stopping.", len(available_movies))
            break
            
        payload = _build_payload(available_movies)
        movies_json = json.dumps(payload, ensure_ascii=False)
        
        exclude_str = ""
        if exclude_themes:
            exclude_str = (
                "\nDo NOT reuse any of these existing collection themes: "
                + ", ".join(exclude_themes) + ".\n"
            )
            
        try:
            with start_session("litellm", ai_model) as session:
                result = curate_collection(m=session, movies_json=movies_json, exclude_themes_str=exclude_str)
            name, keys = result.name, result.keys
        except Exception as exc:
            logger.error("Mellea curate API error: %s", exc)
            name, keys = "Curator's Pick", []

        keys = _pick(keys, movie_seen, max_results)
        if keys:
            logger.info("Collection '%s' (%d/%d): %d items", name, i + 1, count, len(keys))
            client.upsert_collection(name, keys)
            collections_created_records.append({"title": name, "library": client._library_name})
            exclude_themes.append(name)
            collections_written.append((name, len(keys)))
            all_surfaced_keys.extend(keys)
        else:
            logger.warning("Collection '%s' (%d/%d) had no items — skipping", name, i + 1, count)

    # Save state and cooldown
    _save_state(state_path, collections_created_records)
    _save_cooldown(cooldown_path, cooldown_history, all_surfaced_keys)

    for name, cnt in collections_written:
        logger.info("  ✓ %s (%d items)", name, cnt)
    logger.info("Done — %d collections updated and pinned to library views.", len(collections_written))


def _setup_ai_env(cfg: dict) -> str:
    ai_cfg = cfg.get("ai", {})
    provider_name = ai_cfg.get("provider", "gemini")
    model = ai_cfg.get("model", "")
    api_key = ai_cfg.get("api_key", "")

    if provider_name == "gemini":
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        model_name = model or "gemini-3.5-flash-lite"
        return f"gemini/{model_name}"
    
    if provider_name == "ollama":
        base_url = ai_cfg.get("base_url", "http://localhost:11434")
        os.environ["OLLAMA_API_BASE"] = base_url
        model_name = model or "llama3"
        return f"ollama/{model_name}"

    raise ValueError(f"Unknown AI provider '{provider_name}'. Choose: gemini, ollama")


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    run(config_path)
