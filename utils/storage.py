"""
utils/storage.py — depozit de date comun pentru bot SI dashboard.

Ambele citesc/scriu in acelasi fisier (data/store.json).
Asa, ce setezi in dashboard, botul vede automat la urmatorul eveniment.
"""

import json
import os
import threading

_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "store.json")
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(_DATA_FILE):
        return {}
    with open(_DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save(data: dict):
    os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
    with open(_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get(guild_id, key, default=None):
    with _lock:
        return _load().get(str(guild_id), {}).get(key, default)


def set(guild_id, key, value):
    with _lock:
        data = _load()
        data.setdefault(str(guild_id), {})[key] = value
        _save(data)


def get_guild(guild_id) -> dict:
    with _lock:
        return _load().get(str(guild_id), {})


def all_data() -> dict:
    with _lock:
        return _load()
