"""Abstract base class for AI provider backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    """All AI backends implement this interface."""

    @abstractmethod
    def categorize(self, movies: list[dict[str, Any]], row_prompt: str) -> list[int]:
        """
        Given a list of movie metadata dicts and a row-specific prompt,
        return the ratingKeys of movies that belong in that row.
        """
        ...
