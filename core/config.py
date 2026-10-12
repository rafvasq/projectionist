from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional
import yaml

DEFAULT_CONFIG_FILENAME = "config.yaml"


def get_project_root() -> Path:
    """Return project root directory."""
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """Return path to persistent data directory."""
    data_dir = get_project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def load_config(config_path: Optional[str] = None) -> dict[str, Any]:
    """
    Load configuration from YAML file and overlay environment variable overrides.
    """
    if config_path:
        target = Path(config_path)
    else:
        # Check standard locations
        candidates = [
            get_project_root() / DEFAULT_CONFIG_FILENAME,
            Path("/app/config.yaml"),
            Path(DEFAULT_CONFIG_FILENAME),
        ]
        target = next((p for p in candidates if p.exists()), candidates[0])

    config: dict[str, Any] = {}
    if target.exists():
        try:
            with open(target, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    config = loaded
        except Exception:
            # Return empty config if parsing failed
            pass

    # Ensure root sections are dictionaries
    sections = [
        "plex",
        "ai",
        "whatsapp",
        "overseerr",
        "radarr",
        "deletion",
        "rows",
        "schedule",
    ]
    for section in sections:
        if not isinstance(config.get(section), dict):
            config[section] = {}

    # Apply environment variable overrides
    if "PLEX_URL" in os.environ:
        config["plex"]["url"] = os.environ["PLEX_URL"]
    if "PLEX_TOKEN" in os.environ:
        config["plex"]["token"] = os.environ["PLEX_TOKEN"]
    if "PLEX_LIBRARY" in os.environ:
        config["plex"]["library"] = os.environ["PLEX_LIBRARY"]

    if "GEMINI_API_KEY" in os.environ:
        config["ai"]["api_key"] = os.environ["GEMINI_API_KEY"]

    if "OVERSEERR_URL" in os.environ:
        config["overseerr"]["url"] = os.environ["OVERSEERR_URL"]
    if "OVERSEERR_API_KEY" in os.environ:
        config["overseerr"]["api_key"] = os.environ["OVERSEERR_API_KEY"]

    if "RADARR_URL" in os.environ:
        config["radarr"]["url"] = os.environ["RADARR_URL"]
    if "RADARR_API_KEY" in os.environ:
        config["radarr"]["api_key"] = os.environ["RADARR_API_KEY"]

    if "TWILIO_ACCOUNT_SID" in os.environ:
        config["whatsapp"]["twilio_account_sid"] = os.environ["TWILIO_ACCOUNT_SID"]
    if "TWILIO_AUTH_TOKEN" in os.environ:
        config["whatsapp"]["twilio_auth_token"] = os.environ["TWILIO_AUTH_TOKEN"]
    if "TWILIO_PHONE_NUMBER" in os.environ:
        config["whatsapp"]["twilio_phone_number"] = os.environ["TWILIO_PHONE_NUMBER"]

    return config
