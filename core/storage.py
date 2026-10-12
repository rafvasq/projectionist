"""
core/storage.py
Thread-safe, atomic JSON read/write operations with per-path locking and Windows safety.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Set, Tuple, Union

from core.config import get_data_dir

logger = logging.getLogger(__name__)

_LOCKS: dict[str, threading.Lock] = {}
_GLOBAL_LOCK = threading.Lock()


def _get_lock(path_or_str: Union[Path, str]) -> threading.Lock:
    """
    Get or create a per-path threading.Lock using a normalized absolute path.
    On Windows, os.path.normcase normalizes drive letters and case insensitivity
    so that different path string casings reference the exact same lock.
    """
    resolved = os.path.normcase(str(Path(path_or_str).resolve()))
    with _GLOBAL_LOCK:
        if resolved not in _LOCKS:
            _LOCKS[resolved] = threading.Lock()
        return _LOCKS[resolved]


def atomic_read_json(path: Union[Path, str], default: Any = None) -> Any:
    """
    Thread-safe read of a JSON file with safe default on error or missing file.
    Per-path lock is acquired prior to existence checking to avoid TOCTOU races.
    """
    p = Path(path)
    lock = _get_lock(p)
    with lock:
        if not p.exists():
            return default
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Error reading JSON from %s: %s", p, e)
            return default


def atomic_write_json(path: Union[Path, str], data: Any, indent: int = 2) -> None:
    """
    Thread-safe, atomic write of a JSON file via temporary replacement.

    Windows Safety Guarantees:
    1. Temp file created in the same directory (same filesystem volume) to guarantee atomic rename.
    2. Temp file descriptor is explicitly flushed, fsynced, and closed before calling os.replace.
    3. If any exception occurs before replace, temporary file is cleaned up.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = _get_lock(p)

    with lock:
        dir_name = p.parent
        prefix = f".{p.stem}_tmp_"
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=dir_name, prefix=prefix, delete=False, encoding="utf-8") as tmp:
                tmp_path = Path(tmp.name)
                json.dump(data, tmp, indent=indent, ensure_ascii=False)
                tmp.flush()
                os.fsync(tmp.fileno())
            # File descriptor is closed when exiting context manager above.
            max_attempts = 10
            for attempt in range(max_attempts):
                try:
                    os.replace(tmp_path, p)
                    break
                except PermissionError:
                    if attempt == max_attempts - 1:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        except Exception as e:
            logger.error("Failed atomic write to %s: %s", p, e)
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise


# ==============================================================================
# Standard File Path Accessors
# ==============================================================================

def get_whatsapp_state_file() -> Path:
    return get_data_dir() / "whatsapp_state.json"


def get_tracked_collections_file() -> Path:
    return get_data_dir() / "tracked_collections.json"


def get_curator_cooldown_file() -> Path:
    return get_data_dir() / "recently_surfaced.json"


def get_curator_state_file() -> Path:
    return get_data_dir() / "state.json"



# ==============================================================================
# State Helpers
# ==============================================================================

def load_whatsapp_state(path: Optional[Union[Path, str]] = None) -> dict[str, Any]:
    p = Path(path) if path else get_whatsapp_state_file()
    res = atomic_read_json(p, default={})
    return res if isinstance(res, dict) else {}


def save_whatsapp_state(state: dict[str, Any], path: Optional[Union[Path, str]] = None) -> None:
    p = Path(path) if path else get_whatsapp_state_file()
    atomic_write_json(p, state)


def load_tracked_collections(path: Optional[Union[Path, str]] = None) -> dict[str, Any]:
    p = Path(path) if path else get_tracked_collections_file()
    res = atomic_read_json(p, default={})
    return res if isinstance(res, dict) else {}


def save_tracked_collections(data: dict[str, Any], path: Optional[Union[Path, str]] = None) -> None:
    p = Path(path) if path else get_tracked_collections_file()
    atomic_write_json(p, data)


def load_cooldown(*args: Any, **kwargs: Any) -> Tuple[list[dict[str, Any]], Set[int]]:
    """
    Load curator cooldown history and set of surfaced ratingKeys within the cutoff window.
    Supports:
      load_cooldown(max_weeks=4, path=None)
      load_cooldown(path, max_weeks=4)
      load_cooldown(max_weeks, path)
    """
    max_weeks: int = 4
    p: Optional[Path] = None

    if "max_weeks" in kwargs and kwargs["max_weeks"] is not None:
        max_weeks = int(kwargs["max_weeks"])
    if "path" in kwargs and kwargs["path"] is not None:
        p = Path(kwargs["path"])

    if len(args) == 1:
        if isinstance(args[0], (str, Path)):
            p = Path(args[0])
        else:
            max_weeks = int(args[0])
    elif len(args) >= 2:
        if isinstance(args[0], (str, Path)):
            p = Path(args[0])
            max_weeks = int(args[1])
        else:
            max_weeks = int(args[0])
            p = Path(args[1]) if args[1] is not None else None

    if p is None:
        p = get_curator_cooldown_file()

    history = atomic_read_json(p, default=[])
    if not isinstance(history, list):
        history = []

    cutoff = datetime.now() - timedelta(weeks=max_weeks)
    recent: list[dict[str, Any]] = []
    cooled: Set[int] = set()

    for entry in history:
        if isinstance(entry, dict) and "date" in entry:
            try:
                entry_date = datetime.fromisoformat(entry["date"])
                if entry_date > cutoff:
                    recent.append(entry)
                    for k in entry.get("keys", []):
                        try:
                            cooled.add(int(k))
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass

    return recent, cooled


def save_cooldown(*args: Any, **kwargs: Any) -> None:
    """
    Append new keys with current date and atomically persist cooldown history.
    Supports:
      save_cooldown(history, new_keys, path=None)
      save_cooldown(path, history, new_keys)
      save_cooldown(history=..., new_keys=..., path=...)
    """
    p: Optional[Path] = None
    history: list[dict[str, Any]] = []
    new_keys: list[int] = []

    if "path" in kwargs and kwargs["path"] is not None:
        p = Path(kwargs["path"])
    if "history" in kwargs and isinstance(kwargs["history"], list):
        history = kwargs["history"]
    if "new_keys" in kwargs and isinstance(kwargs["new_keys"], list):
        new_keys = kwargs["new_keys"]

    if len(args) == 2:
        history = args[0] if isinstance(args[0], list) else []
        new_keys = args[1] if isinstance(args[1], list) else []
    elif len(args) >= 3:
        if isinstance(args[0], (str, Path)):
            p = Path(args[0])
            history = args[1] if isinstance(args[1], list) else []
            new_keys = args[2] if isinstance(args[2], list) else []
        else:
            history = args[0] if isinstance(args[0], list) else []
            new_keys = args[1] if isinstance(args[1], list) else []
            p = Path(args[2]) if args[2] is not None else None

    if p is None:
        p = get_curator_cooldown_file()

    history.append({
        "date": datetime.now().isoformat()[:10],
        "keys": [int(k) for k in new_keys],
    })
    try:
        atomic_write_json(p, history)
    except Exception as exc:
        logger.warning("Could not save cooldown file %s: %s", p, exc)



def load_curator_state(path: Optional[Union[Path, str]] = None) -> list[dict[str, Any]]:
    p = Path(path) if path else get_curator_state_file()
    records = atomic_read_json(p, default=[])
    return records if isinstance(records, list) else []


def save_curator_state(records: list[dict[str, Any]], path: Optional[Union[Path, str]] = None) -> None:
    p = Path(path) if path else get_curator_state_file()
    try:
        atomic_write_json(p, records)
    except Exception as exc:
        logger.warning("Could not save curator state to %s: %s", p, exc)
