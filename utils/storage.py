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


# Daca fisierul principal nu se poate citi, am pierde TOTUL la prima scriere
# urmatoare (cache-ul ar deveni {} si l-ar suprascrie). Ca sa nu se intample:
#   - pastram un backup rotativ (.bak) la fiecare salvare reusita
#   - daca principalul e corupt, incercam backup-ul; daca nici el nu merge,
#     RIDICAM eroare (nu pornim cu {} gol) ca sa nu stergem datele bune de pe disc
_BAK_FILE = _DATA_FILE + ".bak"
_load_failed = False  # True daca n-am putut citi nici fisierul, nici backup-ul


def _try_read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _ensure_loaded():
    """Incarca fisierul in cache o singura data (la prima folosire)."""
    global _cache, _load_failed
    if _cache is None:
        if os.path.exists(_DATA_FILE):
            try:
                _cache = _try_read(_DATA_FILE)
            except (json.JSONDecodeError, OSError):
                # principalul e corupt -> incercam backup-ul
                try:
                    _cache = _try_read(_BAK_FILE)
                    print("[storage] store.json corupt — am incarcat din store.json.bak")
                except (json.JSONDecodeError, OSError, FileNotFoundError):
                    # nici backup -> NU pornim gol (altfel prima scriere sterge tot).
                    # Marcam esecul: scrierile vor fi blocate pana se rezolva manual.
                    _load_failed = True
                    _cache = {}
                    print("[storage] ATENTIE: nu am putut citi store.json si nici "
                          "store.json.bak. Scrierile sunt BLOCATE ca sa nu pierzi "
                          "datele. Repara/sterge fisierul corupt si reporneste.")
        else:
            _cache = {}
    return _cache


def _save_locked():
    """Scrie cache-ul pe disc atomic (tmp + replace) ca sa nu se corupa.
    Pastreaza si un backup (.bak) cu versiunea dinainte de scriere."""
    if _load_failed:
        # nu scriem peste date pe care nu le-am putut citi la pornire
        return
    os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
    # backup: copiem fisierul curent (bun) inainte sa-l inlocuim
    if os.path.exists(_DATA_FILE):
        try:
            import shutil
            shutil.copy2(_DATA_FILE, _BAK_FILE)
        except OSError:
            pass
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
        bucket = data.setdefault(str(guild_id), {})
        # daca valoarea e IDENTICA cu cea deja salvata, nu rescriem fisierul degeaba
        # (multe bucle salveaza periodic aceleasi date — ex. lista de servere la 20s).
        # Cache-ul ramane oricum corect; economisim scrieri inutile pe disc.
        if key in bucket and bucket[key] == value:
            return
        bucket[key] = copy.deepcopy(value)
        _save_locked()


def get_guild(guild_id) -> dict:
    with _lock:
        return copy.deepcopy(_ensure_loaded().get(str(guild_id), {}))


def all_data() -> dict:
    with _lock:
        return copy.deepcopy(_ensure_loaded())
