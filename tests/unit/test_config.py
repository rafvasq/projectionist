"""Unit tests for core/config.py configuration loader and paths."""

from __future__ import annotations

import os
from pathlib import Path
import pytest
import yaml

from core.config import (
    DEFAULT_CONFIG_FILENAME,
    get_data_dir,
    get_project_root,
    load_config,
)


def test_get_project_root():
    root = get_project_root()
    assert isinstance(root, Path)
    assert root.exists()
    assert root.is_dir()
    assert (root / "core").is_dir()
    assert (root / "pyproject.toml").is_file()


def test_get_data_dir(tmp_path, monkeypatch):
    data_dir = get_data_dir()
    assert isinstance(data_dir, Path)
    assert data_dir.exists()
    assert data_dir.is_dir()
    assert data_dir.name == "data"

    # Test directory creation when data dir does not exist
    fake_root = tmp_path / "fake_proj"
    fake_root.mkdir()
    monkeypatch.setattr("core.config.get_project_root", lambda: fake_root)
    created_data_dir = get_data_dir()
    assert created_data_dir == fake_root / "data"
    assert created_data_dir.exists()
    assert created_data_dir.is_dir()


def test_load_config_missing_file_returns_default_sections(tmp_path):
    missing_path = tmp_path / "nonexistent.yaml"
    cfg = load_config(str(missing_path))
    assert isinstance(cfg, dict)
    expected_sections = [
        "plex",
        "ai",
        "whatsapp",
        "overseerr",
        "radarr",
        "deletion",
        "rows",
        "schedule",
    ]
    for section in expected_sections:
        assert section in cfg
        assert isinstance(cfg[section], dict)


def test_load_config_from_valid_yaml(tmp_path):
    yaml_content = {
        "plex": {"url": "http://localhost:32400", "token": "mytoken"},
        "ai": {"model": "gemini-2.5"},
        "deletion": {"unwatched_threshold_days": 45},
    }
    config_file = tmp_path / "custom_config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(yaml_content, f)

    cfg = load_config(str(config_file))
    assert cfg["plex"]["url"] == "http://localhost:32400"
    assert cfg["plex"]["token"] == "mytoken"
    assert cfg["ai"]["model"] == "gemini-2.5"
    assert cfg["deletion"]["unwatched_threshold_days"] == 45
    # Default unmentioned sections still initialized
    assert "whatsapp" in cfg
    assert isinstance(cfg["whatsapp"], dict)
    assert "radarr" in cfg


def test_load_config_corrupt_yaml_fallback(tmp_path):
    config_file = tmp_path / "corrupt_config.yaml"
    config_file.write_text("invalid_yaml: [unclosed list", encoding="utf-8")

    cfg = load_config(str(config_file))
    assert isinstance(cfg, dict)
    assert "plex" in cfg
    assert "schedule" in cfg


def test_load_config_non_dict_yaml(tmp_path):
    config_file = tmp_path / "list_config.yaml"
    config_file.write_text("- item1\n- item2\n", encoding="utf-8")

    cfg = load_config(str(config_file))
    assert isinstance(cfg, dict)
    assert "plex" in cfg
    assert "radarr" in cfg


def test_load_config_default_candidates(monkeypatch, tmp_path):
    fake_proj = tmp_path / "proj"
    fake_proj.mkdir()
    cfg_file = fake_proj / DEFAULT_CONFIG_FILENAME
    cfg_file.write_text("plex:\n  url: 'http://default-plex:32400'\n", encoding="utf-8")

    monkeypatch.setattr("core.config.get_project_root", lambda: fake_proj)

    # Clean out env vars to test baseline file loading
    for key in [
        "PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY",
        "GEMINI_API_KEY", "OVERSEERR_URL", "OVERSEERR_API_KEY",
        "RADARR_URL", "RADARR_API_KEY",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
    ]:
        monkeypatch.delenv(key, raising=False)

    cfg = load_config()
    assert cfg["plex"]["url"] == "http://default-plex:32400"


def test_load_config_environment_variable_overrides(tmp_path, monkeypatch):
    base_yaml = {
        "plex": {"url": "http://original:32400", "token": "old_token"},
        "ai": {"api_key": "old_gemini"},
        "overseerr": {"url": "http://old-overseerr:5055", "api_key": "old_os_key"},
        "radarr": {"url": "http://old-radarr:7878", "api_key": "old_radarr_key"},
        "whatsapp": {
            "twilio_account_sid": "old_sid",
            "twilio_auth_token": "old_auth",
            "twilio_phone_number": "+1111",
        },
    }
    config_file = tmp_path / "test_env.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(base_yaml, f)

    env_overrides = {
        "PLEX_URL": "http://override-plex:32400",
        "PLEX_TOKEN": "override_token",
        "PLEX_LIBRARY": "Custom Movies",
        "GEMINI_API_KEY": "override_gemini_key",
        "OVERSEERR_URL": "http://override-overseerr:5055",
        "OVERSEERR_API_KEY": "override_os_key",
        "RADARR_URL": "http://override-radarr:7878",
        "RADARR_API_KEY": "override_radarr_key",
        "TWILIO_ACCOUNT_SID": "override_sid",
        "TWILIO_AUTH_TOKEN": "override_auth",
        "TWILIO_PHONE_NUMBER": "+9999",
    }
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    cfg = load_config(str(config_file))

    assert cfg["plex"]["url"] == "http://override-plex:32400"
    assert cfg["plex"]["token"] == "override_token"
    assert cfg["plex"]["library"] == "Custom Movies"
    assert cfg["ai"]["api_key"] == "override_gemini_key"
    assert cfg["overseerr"]["url"] == "http://override-overseerr:5055"
    assert cfg["overseerr"]["api_key"] == "override_os_key"
    assert cfg["radarr"]["url"] == "http://override-radarr:7878"
    assert cfg["radarr"]["api_key"] == "override_radarr_key"
    assert cfg["whatsapp"]["twilio_account_sid"] == "override_sid"
    assert cfg["whatsapp"]["twilio_auth_token"] == "override_auth"
    assert cfg["whatsapp"]["twilio_phone_number"] == "+9999"


def test_load_config_partial_env_overrides(tmp_path, monkeypatch):
    config_file = tmp_path / "empty.yaml"
    config_file.write_text("{}", encoding="utf-8")

    # Clear relevant env vars except one
    for key in [
        "PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY",
        "GEMINI_API_KEY", "OVERSEERR_URL", "OVERSEERR_API_KEY",
        "RADARR_URL", "RADARR_API_KEY",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("PLEX_URL", "http://only-plex:32400")

    cfg = load_config(str(config_file))
    assert cfg["plex"]["url"] == "http://only-plex:32400"
    assert "token" not in cfg["plex"]
    assert "url" not in cfg["overseerr"]


def test_load_config_null_and_empty_sections_normalized_to_dict(tmp_path, monkeypatch):
    config_file = tmp_path / "null_sections.yaml"
    config_file.write_text(
        "plex:\n"
        "ai: null\n"
        "whatsapp: ~\n"
        "overseerr: null\n"
        "radarr:\n"
        "deletion: ~\n"
        "rows: null\n"
        "schedule:\n",
        encoding="utf-8",
    )

    for key in [
        "PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY",
        "GEMINI_API_KEY", "OVERSEERR_URL", "OVERSEERR_API_KEY",
        "RADARR_URL", "RADARR_API_KEY",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("PLEX_URL", "http://env-plex:32400")
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "env-twilio-sid")
    monkeypatch.setenv("OVERSEERR_URL", "http://env-overseerr:5055")
    monkeypatch.setenv("RADARR_URL", "http://env-radarr:7878")

    cfg = load_config(str(config_file))

    expected_sections = [
        "plex",
        "ai",
        "whatsapp",
        "overseerr",
        "radarr",
        "deletion",
        "rows",
        "schedule",
    ]
    for section in expected_sections:
        assert section in cfg
        assert isinstance(cfg[section], dict), f"Section {section} must be a dict"

    # Environment variables should be overlaid cleanly onto normalized empty dicts
    assert cfg["plex"]["url"] == "http://env-plex:32400"
    assert cfg["ai"]["api_key"] == "env-gemini-key"
    assert cfg["whatsapp"]["twilio_account_sid"] == "env-twilio-sid"
    assert cfg["overseerr"]["url"] == "http://env-overseerr:5055"
    assert cfg["radarr"]["url"] == "http://env-radarr:7878"
    assert cfg["deletion"] == {}
    assert cfg["rows"] == {}
    assert cfg["schedule"] == {}


def test_load_config_scalar_sections_normalized_to_dict(tmp_path, monkeypatch):
    config_file = tmp_path / "scalar_sections.yaml"
    config_file.write_text(
        "plex: 'invalid string'\n"
        "ai: 42\n"
        "whatsapp: 3.14\n"
        "overseerr: 123\n"
        "radarr: false\n"
        "deletion: [1, 2, 3]\n"
        "rows: true\n"
        "schedule: ['daily', 'weekly']\n",
        encoding="utf-8",
    )

    for key in [
        "PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY",
        "GEMINI_API_KEY", "OVERSEERR_URL", "OVERSEERR_API_KEY",
        "RADARR_URL", "RADARR_API_KEY",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("PLEX_URL", "http://scalar-plex:32400")
    monkeypatch.setenv("RADARR_URL", "http://scalar-radarr:7878")

    cfg = load_config(str(config_file))

    expected_sections = [
        "plex",
        "ai",
        "whatsapp",
        "overseerr",
        "radarr",
        "deletion",
        "rows",
        "schedule",
    ]
    for section in expected_sections:
        assert section in cfg
        assert isinstance(cfg[section], dict), f"Section {section} must be a dict"

    # Overlays apply without TypeErrors even if YAML had scalars
    assert cfg["plex"]["url"] == "http://scalar-plex:32400"
    assert cfg["radarr"]["url"] == "http://scalar-radarr:7878"
    assert cfg["ai"] == {}
    assert cfg["whatsapp"] == {}
    assert cfg["overseerr"] == {}
    assert cfg["deletion"] == {}
    assert cfg["rows"] == {}
    assert cfg["schedule"] == {}
