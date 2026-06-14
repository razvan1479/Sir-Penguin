"""
utils/storage.py — depozit de date comun pentru bot SI dashboard.

Ambele ruleaza in ACELASI proces (dashboardul e un thread in run.py), deci
tin datele intr-un cache in memorie ca sa nu recitesc fisierul de pe disc la
fiecare comanda. Asta face comenzile sa raspunda mult mai repede.

- citirile (get) intorc o COPIE din cache (rapid, fara acces la disc)
- scrierile (set) actualizeaza cache-ul SI salveaza pe disc (atomic, sigur)
"""

import os
import json
import copy
import threading

_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "store.json")
_lock = threading.Lock()
_cache = None  # copia in memorie a intregului depozit


def _ensure_loaded():
    """Incarca fisierul in cache o singura data (la prima folosire)."""
    global _cache
    if _cache is None:
        if os.path.exists(_DATA_FILE):
            try:
                with open(_DATA_FILE, "r", encoding="utf-8") as f:
                    _cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                _cache = {}
        else:
            _cache = {}
    return _cache


def _save_locked():
    """Scrie cache-ul pe disc atomic (tmp + replace) ca sa nu se corupa."""
    os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
    tmp = _DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _DATA_FILE)


def get(guild_id, key, default=None):
    with _lock:
        val = _ensure_loaded().get(str(guild_id), {}).get(key, default)
        return copy.deepcopy(val)


def set(guild_id, key, value):
    with _lock:
        data = _ensure_loaded()
        data.setdefault(str(guild_id), {})[key] = copy.deepcopy(value)
        _save_locked()


def get_guild(guild_id) -> dict:
    with _lock:
        return copy.deepcopy(_ensure_loaded().get(str(guild_id), {}))


def all_data() -> dict:
    with _lock:
        return copy.deepcopy(_ensure_loaded())
