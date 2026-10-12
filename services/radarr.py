"""
services/radarr.py
Radarr API adapter service.
Handles movie lookup by TMDB ID and movie deletion (with file deletion) via Radarr v3 API
with explicit timeouts and robust error handling.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Union
import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: float = 10.0


class RadarrService:
    """
    Adapter client for Radarr v3 REST API.

    Features:
    - Explicit HTTP timeout bounds on all operations (default: 10.0s).
    - Movie lookup by TMDB ID.
    - Movie deletion with file removal and exclusion options.
    - Configuration factory `from_config(cfg)`.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.url = (url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout = float(timeout)

    @classmethod
    def from_config(cls, cfg: Optional[dict[str, Any]] = None) -> RadarrService:
        """Construct RadarrService from configuration dictionary or default config."""
        if cfg is None:
            from core.config import load_config
            cfg = load_config()

        radarr_cfg = cfg.get("radarr", {}) if isinstance(cfg, dict) else {}
        return cls(
            url=radarr_cfg.get("url", ""),
            api_key=radarr_cfg.get("api_key", ""),
            timeout=float(radarr_cfg.get("timeout", DEFAULT_TIMEOUT)),
        )

    @property
    def is_configured(self) -> bool:
        """Check if both URL and API key are configured."""
        return bool(self.url and self.api_key)

    @property
    def _headers(self) -> dict[str, str]:
        """HTTP headers required for Radarr API authentication."""
        if not self.api_key:
            return {}
        return {"X-Api-Key": self.api_key}

    def get_movie_by_tmdb_id(self, tmdb_id: Union[int, str]) -> Optional[dict[str, Any]]:
        """
        Query Radarr for movie matching TMDB ID.

        Endpoint: GET /api/v3/movie?tmdbId={tmdb_id}

        Returns:
            dict containing Radarr movie record (including 'id') if found.
            None if movie is not in Radarr, on HTTP error, or if service is not configured.
        """
        if not self.is_configured:
            logger.warning("Radarr get_movie_by_tmdb_id skipped: service is not configured")
            return None

        if tmdb_id is None or str(tmdb_id).strip() == "":
            logger.warning("Radarr get_movie_by_tmdb_id skipped: invalid tmdb_id '%s'", tmdb_id)
            return None

        endpoint = f"{self.url}/api/v3/movie"
        try:
            resp = requests.get(
                endpoint,
                params={"tmdbId": str(tmdb_id).strip()},
                headers=self._headers,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    if not data:
                        logger.info("TMDB ID %s not found in Radarr", tmdb_id)
                        return None
                    return data[0] if isinstance(data[0], dict) else None
                elif isinstance(data, dict):
                    return data
                return None

            logger.error(
                "Radarr lookup failed for TMDB %s with HTTP %d: %s",
                tmdb_id,
                resp.status_code,
                resp.text[:200],
            )
            return None

        except requests.RequestException as exc:
            logger.error("Radarr lookup network error for TMDB %s: %s", tmdb_id, exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error in Radarr lookup for TMDB %s: %s", tmdb_id, exc)
            return None

    def delete_movie(
        self,
        radarr_id: Union[int, str],
        delete_files: bool = True,
        add_import_exclusion: bool = False,
    ) -> bool:
        """
        Delete movie from Radarr.

        Endpoint: DELETE /api/v3/movie/{radarr_id}?deleteFiles=true&addImportExclusion=false

        Args:
            radarr_id: Radarr internal ID of movie
            delete_files: Whether to delete movie files from disk (default: True)
            add_import_exclusion: Whether to prevent Radarr from re-importing movie (default: False)

        Returns:
            True on successful deletion (HTTP 200, 202, 204), False otherwise.
        """
        if not self.is_configured:
            logger.warning("Radarr delete_movie skipped: service is not configured")
            return False

        try:
            numeric_id = int(radarr_id)
        except (ValueError, TypeError):
            logger.error("Radarr delete_movie failed: invalid radarr_id '%s'", radarr_id)
            return False

        endpoint = f"{self.url}/api/v3/movie/{numeric_id}"
        params = {
            "deleteFiles": "true" if delete_files else "false",
            "addImportExclusion": "true" if add_import_exclusion else "false",
        }

        try:
            resp = requests.delete(
                endpoint,
                params=params,
                headers=self._headers,
                timeout=self.timeout,
            )
            if resp.status_code in (200, 202, 204):
                logger.info(
                    "Successfully deleted movie (Radarr ID %d, deleteFiles=%s)",
                    numeric_id,
                    delete_files,
                )
                return True

            logger.error(
                "Radarr deletion failed for ID %d with HTTP %d: %s",
                numeric_id,
                resp.status_code,
                resp.text[:200],
            )
            return False

        except requests.RequestException as exc:
            logger.error("Radarr deletion network error for ID %d: %s", numeric_id, exc)
            return False
        except Exception as exc:
            logger.error("Unexpected error in Radarr deletion for ID %d: %s", numeric_id, exc)
            return False
