"""Gemini provider — uses Google's Generative AI API to categorize movies."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google import genai

from .base import AIProvider

logger = logging.getLogger(__name__)

# Gemini free tier supports up to ~1M tokens; batching avoids context overflow
# on very large libraries.
_BATCH_SIZE = 150


class GeminiProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model

    def categorize(self, movies: list[dict[str, Any]], row_prompt: str) -> list[int]:
        """
        Send movies to Gemini in batches and collect ratingKeys that qualify.
        Returns a deduplicated list preserving first-seen order.
        """
        results: list[int] = []
        seen: set[int] = set()

        batches = [movies[i:i + _BATCH_SIZE] for i in range(0, len(movies), _BATCH_SIZE)]
        logger.info("Categorizing %d movies via Gemini (%d batch(es))", len(movies), len(batches))

        for idx, batch in enumerate(batches, 1):
            keys = self._categorize_batch(batch, row_prompt, batch_num=idx)
            for k in keys:
                if k not in seen:
                    seen.add(k)
                    results.append(k)

        logger.info("Gemini selected %d qualifying movies", len(results))
        return results

    def _categorize_batch(
        self, batch: list[dict[str, Any]], row_prompt: str, batch_num: int
    ) -> list[int]:
        prompt = self._build_prompt(batch, row_prompt)

        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=prompt,
            )
        except Exception as exc:
            logger.error("Gemini API error on batch %d: %s", batch_num, exc)
            return []

        return self._parse_keys(response.text, batch_num)

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
        prompt = self._build_curate_prompt(movies)
        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=prompt,
            )
        except Exception as exc:
            logger.error("Gemini curate API error: %s", exc)
            return ("Curator's Pick", [])

        return self._parse_curate_response(response.text)

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
            "Look at this entire movie collection and invent ONE unexpected, thematic collection "
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
            logger.warning("curate: no JSON object found in Gemini response")
            logger.debug("Raw response: %s", text)
            return ("Curator's Pick", [])
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("curate: could not parse JSON from Gemini response: %s", exc)
            return ("Curator's Pick", [])

        name = data.get("name", "Curator's Pick")
        keys = [k for k in data.get("keys", []) if isinstance(k, int)]
        logger.info("curate: invented collection '%s' with %d films", name, len(keys))
        return (name, keys)

    @staticmethod
    def _parse_keys(text: str, batch_num: int) -> list[int]:
        """Extract a JSON integer array from the model's response."""
        # Strip markdown code fences if the model ignores instructions
        cleaned = re.sub(r"```(?:json)?|```", "", text).strip()

        # Find the first [...] array in the response
        match = re.search(r"\[.*?\]", cleaned, re.DOTALL)
        if not match:
            logger.warning("Batch %d: no JSON array found in Gemini response", batch_num)
            logger.debug("Raw response: %s", text)
            return []

        try:
            keys = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("Batch %d: could not parse JSON from Gemini response: %s", batch_num, exc)
            logger.debug("Raw response: %s", text)
            return []

        valid = [k for k in keys if isinstance(k, int)]
        if len(valid) != len(keys):
            logger.warning("Batch %d: %d non-integer values dropped", batch_num, len(keys) - len(valid))

        return valid
