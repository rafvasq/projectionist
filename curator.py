"""
Projectionist — main entry point.
Fetches Plex metadata, runs row filters, writes collections back.
"""

from __future__ import annotations

import json
import logging
import random
import sys
from pathlib import Path

import yaml

from plex_client import PlexClient, from_config
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider
from providers.base import AIProvider
from rows.collecting_dust import filter_collecting_dust
from rows.easy_watch import filter_easy_watch
from rows.existential import filter_existential
from rows.adrenaline import filter_adrenaline
from rows.quick_watch import filter_quick_watch
from rows.tv_collecting_dust import filter_tv_collecting_dust
from rows.give_it_a_shot import filter_give_it_a_shot
from rows.wildcard import filter_wildcard

logger = logging.getLogger(__name__)

COLLECTION_COLLECTING_DUST      = "Collecting Dust"
COLLECTION_EASY_WATCH           = "Easy Watch"
COLLECTION_EXISTENTIAL          = "Existential & Atmospheric"
COLLECTION_ADRENALINE           = "Second-Hand Adrenaline"
COLLECTION_QUICK_WATCH          = "90-Minute Dash"
COLLECTION_TV_COLLECTING_DUST   = "Collecting Dust"
COLLECTION_GIVE_IT_A_SHOT       = "Give it a Shot"

DEFAULT_MAX_RESULTS = 15


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _enabled(row_cfg: dict) -> bool:
    return row_cfg.get("enabled", True)


def _pick(keys: list[int], seen: set[int], max_results: int) -> list[int]:
    """
    Shuffle candidates, exclude already-used keys, cap at max_results.
    Updates seen in-place so subsequent rows don't repeat the same titles.
    """
    random.shuffle(keys)
    fresh = [k for k in keys if k not in seen][:max_results]
    seen.update(fresh)
    return fresh


def _delete_previous_collections(client: PlexClient, state_path: Path) -> None:
    """Delete all collections generated in the previous run (both Movies and TV)."""
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

    # 1. State Setup and Initial Cleanup
    state_path_str = cfg.get("state_path") or cfg.get("wildcard_state_path", "state.json")
    state_path = Path(state_path_str)
    _delete_previous_collections(client, state_path)

    collections_created_records: list[dict[str, str]] = []

    def _upsert_and_record(title: str, keys: list[int], library_name: str | None = None) -> None:
        client.upsert_collection(title, keys, library_name=library_name)
        lib = library_name or client._library_name
        collections_created_records.append({"title": title, "library": lib})

    rows_cfg = cfg.get("rows", {})
    tv_library = cfg.get("plex", {}).get("tv_library", "TV Shows")
    max_results = rows_cfg.get("max_results", DEFAULT_MAX_RESULTS)

    # ------------------------------------------------------------------
    # Movies — shared deduplication pool
    # ------------------------------------------------------------------
    movies = client.fetch_movies()
    logger.info("Movies library: %d titles", len(movies))

    movie_seen: set[int] = set()
    provider = _build_provider(cfg)
    collections_written: list[tuple[str, int]] = []

    hg_cfg = rows_cfg.get("collecting_dust", {})
    if _enabled(hg_cfg):
        dust = filter_collecting_dust(movies, min_age_days=hg_cfg.get("min_age_days", 30))
        dust_keys = _pick([m.ratingKey for m in dust], movie_seen, max_results)
        logger.info("Collecting Dust (movies): %d items", len(dust_keys))
        _upsert_and_record(COLLECTION_COLLECTING_DUST, dust_keys)
        collections_written.append((COLLECTION_COLLECTING_DUST, len(dust_keys)))
    else:
        logger.info("Collecting Dust (movies): disabled")

    ew_cfg = rows_cfg.get("easy_watch", {})
    if _enabled(ew_cfg):
        ew_keys = _pick(filter_easy_watch(movies, provider), movie_seen, max_results)
        logger.info("Easy Watch: %d items", len(ew_keys))
        _upsert_and_record(COLLECTION_EASY_WATCH, ew_keys)
        collections_written.append((COLLECTION_EASY_WATCH, len(ew_keys)))
    else:
        logger.info("Easy Watch: disabled")

    ex_cfg = rows_cfg.get("existential", {})
    if _enabled(ex_cfg):
        ex_keys = _pick(filter_existential(movies, provider), movie_seen, max_results)
        logger.info("Existential & Atmospheric: %d items", len(ex_keys))
        _upsert_and_record(COLLECTION_EXISTENTIAL, ex_keys)
        collections_written.append((COLLECTION_EXISTENTIAL, len(ex_keys)))
    else:
        logger.info("Existential & Atmospheric: disabled")

    ad_cfg = rows_cfg.get("adrenaline", {})
    if _enabled(ad_cfg):
        ad_keys = _pick(filter_adrenaline(movies, provider), movie_seen, max_results)
        logger.info("Second-Hand Adrenaline: %d items", len(ad_keys))
        _upsert_and_record(COLLECTION_ADRENALINE, ad_keys)
        collections_written.append((COLLECTION_ADRENALINE, len(ad_keys)))
    else:
        logger.info("Second-Hand Adrenaline: disabled")

    qw_cfg = rows_cfg.get("quick_watch", {})
    if _enabled(qw_cfg):
        quick = filter_quick_watch(movies, max_minutes=qw_cfg.get("max_minutes", 90))
        quick_keys = _pick([m.ratingKey for m in quick], set(), max_results)
        logger.info("90-Minute Dash: %d items", len(quick_keys))
        _upsert_and_record(COLLECTION_QUICK_WATCH, quick_keys)
        collections_written.append((COLLECTION_QUICK_WATCH, len(quick_keys)))
    else:
        logger.info("90-Minute Dash: disabled")

    wc_cfg = rows_cfg.get("wildcard", {})
    if _enabled(wc_cfg):
        count = wc_cfg.get("count", 1)
        exclude_themes = []

        for i in range(count):
            wc_name, wc_keys = filter_wildcard(movies, provider, exclude_themes)
            wc_keys = _pick(wc_keys, movie_seen, max_results)
            if wc_keys:
                logger.info("Wildcard '%s' (%d/%d): %d items", wc_name, i + 1, count, len(wc_keys))
                _upsert_and_record(wc_name, wc_keys)
                exclude_themes.append(wc_name)
                collections_written.append((wc_name, len(wc_keys)))
            else:
                logger.warning("Wildcard '%s' (%d/%d) had no items after filtering — skipping", wc_name, i + 1, count)
    else:
        logger.info("Wildcard: disabled")

    # ------------------------------------------------------------------
    # Custom AI Rows
    # ------------------------------------------------------------------
    custom_rows = rows_cfg.get("custom_ai_rows", [])
    for i, row in enumerate(custom_rows):
        if _enabled(row):
            name = row.get("name")
            prompt = row.get("prompt")
            dedup = row.get("deduplicate", True)

            if not name or not prompt:
                logger.warning("Custom row at index %d is missing 'name' or 'prompt' — skipping", i)
                continue

            # Select deduplication pool
            dedup_pool = movie_seen if dedup else set()

            raw_keys = provider.categorize([
                {
                    "ratingKey": m.ratingKey,
                    "title": m.title,
                    "year": m.year,
                    "summary": m.summary,
                    "genres": m.genres,
                    "rating": m.rating_pct,
                    "audience_rating": m.audience_rating_pct,
                }
                for m in movies
            ], prompt)

            row_keys = _pick(raw_keys, dedup_pool, max_results)

            if row_keys:
                logger.info("Custom AI Row '%s': %d items", name, len(row_keys))
                _upsert_and_record(name, row_keys)
                collections_written.append((name, len(row_keys)))
            else:
                logger.info("Custom AI Row '%s' returned 0 items — skipping", name)

    # ------------------------------------------------------------------
    # TV Shows — separate deduplication pool
    # ------------------------------------------------------------------
    shows = client.fetch_shows(tv_library)
    logger.info("TV library: %d shows", len(shows))

    tv_seen: set[int] = set()

    tv_dust_cfg = rows_cfg.get("tv_collecting_dust", {})
    if _enabled(tv_dust_cfg):
        abandoned = filter_tv_collecting_dust(shows, idle_days=tv_dust_cfg.get("idle_days", 60))
        abandoned_keys = _pick([s.ratingKey for s in abandoned], tv_seen, max_results)
        logger.info("Collecting Dust (TV): %d items", len(abandoned_keys))
        _upsert_and_record(COLLECTION_TV_COLLECTING_DUST, abandoned_keys, library_name=tv_library)
        collections_written.append((COLLECTION_TV_COLLECTING_DUST + " (TV)", len(abandoned_keys)))
    else:
        logger.info("Collecting Dust (TV): disabled")

    gs_cfg = rows_cfg.get("give_it_a_shot", {})
    if _enabled(gs_cfg):
        unstarted = filter_give_it_a_shot(shows)
        unstarted_keys = _pick([s.ratingKey for s in unstarted], tv_seen, max_results)
        logger.info("Give it a Shot: %d items", len(unstarted_keys))
        _upsert_and_record(COLLECTION_GIVE_IT_A_SHOT, unstarted_keys, library_name=tv_library)
        collections_written.append((COLLECTION_GIVE_IT_A_SHOT, len(unstarted_keys)))
    else:
        logger.info("Give it a Shot: disabled")

    for name, count in collections_written:
        logger.info("  ✓ %s (%d items)", name, count)

    # Save the unified state of all created collections
    _save_state(state_path, collections_created_records)
    logger.info("Done — %d collections updated and pinned to library views.", len(collections_written))


def _build_provider(cfg: dict) -> AIProvider:
    ai_cfg = cfg.get("ai", {})
    provider_name = ai_cfg.get("provider", "gemini")
    model = ai_cfg.get("model", "")
    api_key = ai_cfg.get("api_key", "")

    if provider_name == "gemini":
        return GeminiProvider(api_key=api_key, model=model or "gemini-2.5-flash")
    if provider_name == "ollama":
        base_url = ai_cfg.get("base_url", "http://localhost:11434")
        return OllamaProvider(model=model or "llama3", base_url=base_url)

    raise ValueError(f"Unknown AI provider '{provider_name}'. Choose: gemini, ollama")


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    run(config_path)
