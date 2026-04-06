"""
Projectionist — main entry point.
Fetches Plex metadata, runs row filters, writes collections back.
"""

from __future__ import annotations

import logging
import random
import sys

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


def run(config_path: str = "config.yaml") -> None:
    cfg = load_config(config_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    client = from_config(cfg)
    client.connect()

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
        client.upsert_collection(COLLECTION_COLLECTING_DUST, dust_keys)
        collections_written.append((COLLECTION_COLLECTING_DUST, len(dust_keys)))
    else:
        logger.info("Collecting Dust (movies): disabled")

    ew_cfg = rows_cfg.get("easy_watch", {})
    if _enabled(ew_cfg):
        ew_keys = _pick(filter_easy_watch(movies, provider), movie_seen, max_results)
        logger.info("Easy Watch: %d items", len(ew_keys))
        client.upsert_collection(COLLECTION_EASY_WATCH, ew_keys)
        collections_written.append((COLLECTION_EASY_WATCH, len(ew_keys)))
    else:
        logger.info("Easy Watch: disabled")

    ex_cfg = rows_cfg.get("existential", {})
    if _enabled(ex_cfg):
        ex_keys = _pick(filter_existential(movies, provider), movie_seen, max_results)
        logger.info("Existential & Atmospheric: %d items", len(ex_keys))
        client.upsert_collection(COLLECTION_EXISTENTIAL, ex_keys)
        collections_written.append((COLLECTION_EXISTENTIAL, len(ex_keys)))
    else:
        logger.info("Existential & Atmospheric: disabled")

    ad_cfg = rows_cfg.get("adrenaline", {})
    if _enabled(ad_cfg):
        ad_keys = _pick(filter_adrenaline(movies, provider), movie_seen, max_results)
        logger.info("Second-Hand Adrenaline: %d items", len(ad_keys))
        client.upsert_collection(COLLECTION_ADRENALINE, ad_keys)
        collections_written.append((COLLECTION_ADRENALINE, len(ad_keys)))
    else:
        logger.info("Second-Hand Adrenaline: disabled")

    qw_cfg = rows_cfg.get("quick_watch", {})
    if _enabled(qw_cfg):
        quick = filter_quick_watch(movies, max_minutes=qw_cfg.get("max_minutes", 90))
        quick_keys = _pick([m.ratingKey for m in quick], set(), max_results)
        logger.info("90-Minute Dash: %d items", len(quick_keys))
        client.upsert_collection(COLLECTION_QUICK_WATCH, quick_keys)
        collections_written.append((COLLECTION_QUICK_WATCH, len(quick_keys)))
    else:
        logger.info("90-Minute Dash: disabled")

    wc_cfg = rows_cfg.get("wildcard", {})
    if _enabled(wc_cfg):
        wc_name, wc_keys = filter_wildcard(movies, provider)
        wc_keys = _pick(wc_keys, movie_seen, max_results)
        logger.info("Wildcard '%s': %d items", wc_name, len(wc_keys))
        client.upsert_collection(wc_name, wc_keys)
        collections_written.append((wc_name, len(wc_keys)))
    else:
        logger.info("Wildcard: disabled")

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
        client.upsert_collection(COLLECTION_TV_COLLECTING_DUST, abandoned_keys, library_name=tv_library)
        collections_written.append((COLLECTION_TV_COLLECTING_DUST + " (TV)", len(abandoned_keys)))
    else:
        logger.info("Collecting Dust (TV): disabled")

    gs_cfg = rows_cfg.get("give_it_a_shot", {})
    if _enabled(gs_cfg):
        unstarted = filter_give_it_a_shot(shows)
        unstarted_keys = _pick([s.ratingKey for s in unstarted], tv_seen, max_results)
        logger.info("Give it a Shot: %d items", len(unstarted_keys))
        client.upsert_collection(COLLECTION_GIVE_IT_A_SHOT, unstarted_keys, library_name=tv_library)
        collections_written.append((COLLECTION_GIVE_IT_A_SHOT, len(unstarted_keys)))
    else:
        logger.info("Give it a Shot: disabled")

    for name, count in collections_written:
        logger.info("  ✓ %s (%d items)", name, count)
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
