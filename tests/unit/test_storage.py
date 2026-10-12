"""
Unit tests for core/storage.py.
Verifies thread-safety, atomic tempfile replacement, Windows safety, and state helpers.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import pytest

from core.storage import (
    _get_lock,
    atomic_read_json,
    atomic_write_json,
    load_cooldown,
    save_cooldown,
    load_curator_state,
    save_curator_state,
    load_whatsapp_state,
    save_whatsapp_state,
    load_tracked_collections,
    save_tracked_collections,
    get_whatsapp_state_file,
    get_tracked_collections_file,
)


def test_atomic_read_missing_file(tmp_path: Path):
    missing_path = tmp_path / "nonexistent.json"
    assert atomic_read_json(missing_path) is None
    assert atomic_read_json(missing_path, default={"fallback": True}) == {"fallback": True}


def test_atomic_read_corrupt_file(tmp_path: Path):
    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{bad-json-content", encoding="utf-8")
    assert atomic_read_json(corrupt_path, default=[]) == []


def test_atomic_write_and_read_roundtrip(tmp_path: Path):
    target = tmp_path / "data.json"
    payload = {"title": "Solaris", "year": 1972, "genres": ["Sci-Fi", "Drama"], "unicode": "🎬 🍿"}
    atomic_write_json(target, payload)

    loaded = atomic_read_json(target)
    assert loaded == payload


def test_atomic_write_creates_parent_directories(tmp_path: Path):
    nested_path = tmp_path / "deeply" / "nested" / "dir" / "state.json"
    atomic_write_json(nested_path, {"created": True})
    assert nested_path.exists()
    assert atomic_read_json(nested_path) == {"created": True}


def test_atomic_write_cleans_up_temp_on_failure(tmp_path: Path):
    target = tmp_path / "fail.json"
    bad_data = {"unserializable": object()}

    with pytest.raises(TypeError):
        atomic_write_json(target, bad_data)

    assert not target.exists()
    # Confirm no orphaned temp files
    temp_files = list(tmp_path.glob(".*_tmp_*"))
    assert len(temp_files) == 0


def test_concurrent_multi_threaded_writes(tmp_path: Path):
    """
    Stress test verifying multi-threaded concurrent write safety without data loss or corruption.
    20 threads execute 25 iterations each of atomic writes and reads on the same file.
    """
    target = tmp_path / "concurrent.json"
    atomic_write_json(target, {"initial": True, "count": 0})

    num_threads = 20
    iterations = 25
    errors: list[Exception] = []

    def worker(thread_id: int):
        try:
            for i in range(iterations):
                data = {
                    "thread_id": thread_id,
                    "iteration": i,
                    "payload": list(range(50)),
                }
                atomic_write_json(target, data)
                # Interleaved read
                read_back = atomic_read_json(target)
                assert isinstance(read_back, dict)
                assert "payload" in read_back or "initial" in read_back
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent workers encountered errors: {errors}"
    final_data = atomic_read_json(target)
    assert isinstance(final_data, dict)
    assert "thread_id" in final_data
    # Verify no dangling temp files remain
    temp_files = list(tmp_path.glob(".*_tmp_*"))
    assert len(temp_files) == 0


def test_cooldown_load_and_save(tmp_path: Path):
    cooldown_file = tmp_path / "cooldown.json"

    # Missing file returns empty
    history, keys = load_cooldown(max_weeks=3, path=cooldown_file)
    assert history == []
    assert keys == set()

    # Save initial cooldown
    save_cooldown(history, [101, 102], path=cooldown_file)
    history, keys = load_cooldown(max_weeks=3, path=cooldown_file)
    assert len(history) == 1
    assert keys == {101, 102}

    # Verify pruning of older entries
    old_date = (datetime.now() - timedelta(weeks=5)).isoformat()[:10]
    history.append({"date": old_date, "keys": [999]})
    atomic_write_json(cooldown_file, history)

    pruned_history, pruned_keys = load_cooldown(max_weeks=3, path=cooldown_file)
    assert len(pruned_history) == 1
    assert 999 not in pruned_keys


def test_curator_state_helpers(tmp_path: Path):
    state_file = tmp_path / "curator_state.json"
    assert load_curator_state(path=state_file) == []

    records = [{"title": "Sci-Fi Hits", "library": "Movies"}]
    save_curator_state(records, path=state_file)
    assert load_curator_state(path=state_file) == records


def test_whatsapp_and_tracked_state_helpers(tmp_path: Path):
    with patch("core.storage.get_data_dir", return_value=tmp_path):
        assert load_whatsapp_state() == {}
        save_whatsapp_state({"step": "awaiting_search"})
        assert load_whatsapp_state() == {"step": "awaiting_search"}

        assert load_tracked_collections() == {}
        save_tracked_collections({"Cyberpunk": {"protect": True}})
        assert load_tracked_collections() == {"Cyberpunk": {"protect": True}}


def test_path_normalization_shares_lock(tmp_path):
    target = tmp_path / "state.json"
    p1 = str(target)
    p2 = str(tmp_path / "subdir" / ".." / "state.json")
    assert _get_lock(p1) is _get_lock(p2)
