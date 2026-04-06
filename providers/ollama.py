"""Ollama provider — local inference, production backend."""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

import requests

from .base import AIProvider

logger = logging.getLogger(__name__)

# Smaller batch than Gemini — local 7B/9B models have a tighter context window
# and are more prone to losing track of the task with very large inputs.
_BATCH_SIZE = 50


class OllamaProvider(AIProvider):
    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    def categorize(self, movies: list[dict[str, Any]], row_prompt: str) -> list[int]:
        """
        Send movies to Ollama in batches and collect ratingKeys that qualify.
        Returns a deduplicated list preserving first-seen order.
        """
        results: list[int] = []
        seen: set[int] = set()

        batches = [movies[i:i + _BATCH_SIZE] for i in range(0, len(movies), _BATCH_SIZE)]
        logger.info("Categorizing %d movies via Ollama/%s (%d batch(es))", len(movies), self.model, len(batches))

        for idx, batch in enumerate(batches, 1):
            keys = self._categorize_batch(batch, row_prompt, batch_num=idx)
            for k in keys:
                if k not in seen:
                    seen.add(k)
                    results.append(k)

        logger.info("Ollama selected %d qualifying movies", len(results))
        return results

    def _categorize_batch(
        self, batch: list[dict[str, Any]], row_prompt: str, batch_num: int
    ) -> list[int]:
        prompt = self._build_prompt(batch, row_prompt)

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
                timeout=120,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Ollama API error on batch %d: %s", batch_num, exc)
            return []

        try:
            text = response.json().get("response", "")
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Ollama response parse error on batch %d: %s", batch_num, exc)
            return []

        return self._parse_keys(text, batch_num)

    @staticmethod
    def _build_prompt(batch: list[dict[str, Any]], row_prompt: str) -> str:
        movies_json = json.dumps(batch, ensure_ascii=False, indent=2)
        return (
            f"{row_prompt}\n\n"
            "Here is the list of movies as JSON:\n"
            f"{movies_json}\n\n"
            "Respond with ONLY a raw JSON array of ratingKey integers — "
            "no markdown, no explanation, no code fences. Example: [123, 456, 789]"
        )

    def curate(self, movies: list[dict[str, Any]]) -> tuple[str, list[int]]:
        """Single call: AI invents a collection name, theme, and picks films."""
        # Use a random 100-film sample — local models have tight context windows
        sample = movies if len(movies) <= 100 else random.sample(movies, 100)
        prompt = self._build_curate_prompt(sample)

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False, "format": "json"},
                timeout=180,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Ollama curate API error: %s", exc)
            return ("Curator's Pick", [])

        try:
            text = response.json().get("response", "")
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Ollama curate response parse error: %s", exc)
            return ("Curator's Pick", [])

        return self._parse_curate_response(text)

    @staticmethod
    def _build_curate_prompt(movies: list[dict[str, Any]]) -> str:
        compact = [
            {"ratingKey": m["ratingKey"], "title": m["title"], "year": m["year"],
             "genres": m["genres"], "rating": m["rating"]}
            for m in movies
        ]
        movies_json = json.dumps(compact, ensure_ascii=False)
        return (
            "You are a creative film curator for a small, personal Plex library.\n"
            "Look at this movie collection and invent ONE unexpected, thematic collection "
            "that would delight the owner. The collection should have a creative, evocative name "
            "and a distinct mood or theme not already covered by these existing rows: "
            "Collecting Dust, Easy Watch, Existential & Atmospheric, Second-Hand Adrenaline, "
            "90-Minute Dash, Give it a Shot.\n\n"
            "Rules:\n"
            "- Pick 10–20 films that genuinely fit your theme\n"
            "- The name should be punchy and fun (3–6 words)\n"
            "- Be creative — think beyond obvious genres\n\n"
            f"Movie library:\n{movies_json}\n\n"
            'Return ONLY a raw JSON object: {"name": "Your Creative Name", "keys": [ratingKey1, ratingKey2, ...]}\n'
            "No markdown, no explanation, no code fences."
        )

    @staticmethod
    def _parse_curate_response(text: str) -> tuple[str, list[int]]:
        cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            logger.warning("curate: no JSON object found in Ollama response")
            logger.debug("Raw response: %s", text)
            return ("Curator's Pick", [])
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("curate: could not parse JSON from Ollama response: %s", exc)
            return ("Curator's Pick", [])

        name = data.get("name", "Curator's Pick")
        keys = [k for k in data.get("keys", []) if isinstance(k, int)]
        logger.info("curate: invented collection '%s' with %d films", name, len(keys))
        return (name, keys)

    @staticmethod
    def _parse_keys(text: str, batch_num: int) -> list[int]:
        """Extract a JSON integer array from the model's response."""
        cleaned = re.sub(r"```(?:json)?|```", "", text).strip()

        match = re.search(r"\[.*?\]", cleaned, re.DOTALL)
        if not match:
            logger.warning("Batch %d: no JSON array found in Ollama response", batch_num)
            logger.debug("Raw response: %s", text)
            return []

        try:
            keys = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("Batch %d: could not parse JSON from Ollama response: %s", batch_num, exc)
            logger.debug("Raw response: %s", text)
            return []

        valid = [k for k in keys if isinstance(k, int)]
        if len(valid) != len(keys):
            logger.warning("Batch %d: %d non-integer values dropped", batch_num, len(keys) - len(valid))

        return valid
