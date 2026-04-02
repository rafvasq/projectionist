"""Ollama provider — local inference, production backend."""

from __future__ import annotations

import json
import logging
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
