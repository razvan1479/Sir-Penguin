"""
dashboard/discord_api.py — comunicarea dashboardului cu Discord.

Doua roluri:
  1. OAuth2: login cu Discord (vezi doar serverele pe care le administrezi).
  2. Nume reale: cu tokenul botului rezolvam canale/roluri/useri (nu ID-uri).

Daca DISCORD_CLIENT_ID/SECRET nu sunt setate, OAuth e dezactivat si dashboardul
merge in "mod deschis" (ca inainte). Numele reale necesita doar DISCORD_TOKEN.

NECESITA libraria 'requests' (pip install -r requirements.txt).
"""

import os
import time
from urllib.parse import urlencode

import requests

API = "https://discord.com/api/v10"

# permisiuni Discord
ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20

_cache = {}  # cache simplu: cheie -> (expira_la, valoare)


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = fn()
    _cache[key] = (now + ttl, val)
    return val


def _bot_headers():
    return {"Authorization": f"Bot {os.getenv('DISCORD_TOKEN')}"}


def _redirect_uri():
    return os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5000/callback")


# ----------------------------------------------------------------- OAuth
def oauth_enabled() -> bool:
    return bool(os.getenv("DISCORD_CLIENT_ID") and os.getenv("DISCORD_CLIENT_SECRET"))


def authorize_url() -> str:
    q = urlencode({
        "client_id": os.getenv("DISCORD_CLIENT_ID"),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "identify guilds",
    })
    return f"{API}/oauth2/authorize?{q}"


def exchange_code(code: str) -> dict:
    r = requests.post(f"{API}/oauth2/token", data={
        "client_id": os.getenv("DISCORD_CLIENT_ID"),
        "client_secret": os.getenv("DISCORD_CLIENT_SECRET"),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    r.raise_for_status()
    return r.json()


def get_user(access_token: str) -> dict:
    r = requests.get(f"{API}/users/@me",
                     headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    r.raise_for_status()
    return r.json()


def get_user_guilds(access_token: str) -> list:
    r = requests.get(f"{API}/users/@me/guilds",
                     headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    r.raise_for_status()
    return r.json()


def can_manage(guild: dict) -> bool:
    """True daca userul e owner / admin / are Manage Server pe serverul respectiv."""
    if guild.get("owner"):
        return True
    perms = int(guild.get("permissions", 0))
    return bool(perms & ADMINISTRATOR or perms & MANAGE_GUILD)


# ----------------------------------------------------------------- nume reale (bot token)
def get_guild_channels(guild_id) -> list:
    """Canalele text ale serverului: [{id, name}] (gol daca esueaza)."""
    def fn():
        try:
            r = requests.get(f"{API}/guilds/{guild_id}/channels",
                             headers=_bot_headers(), timeout=15)
            if r.status_code != 200:
                return []
            # type 0 = text, 5 = announcement
            return [{"id": str(c["id"]), "name": c["name"]}
                    for c in r.json() if c.get("type") in (0, 5)]
        except requests.RequestException:
            return []
    return _cached(f"ch:{guild_id}", 60, fn)


def get_guild_roles(guild_id) -> list:
    """Rolurile serverului: [{id, name}] (fara @everyone)."""
    def fn():
        try:
            r = requests.get(f"{API}/guilds/{guild_id}/roles",
                             headers=_bot_headers(), timeout=15)
            if r.status_code != 200:
                return []
            roles = [x for x in r.json() if x.get("name") != "@everyone"]
            roles.sort(key=lambda x: x.get("position", 0), reverse=True)
            return [{"id": str(x["id"]), "name": x["name"]} for x in roles]
        except requests.RequestException:
            return []
    return _cached(f"rl:{guild_id}", 60, fn)


def resolve_username(user_id) -> str:
    """Numele unui user dupa ID (cu fallback 'User <id>')."""
    def fn():
        try:
            r = requests.get(f"{API}/users/{user_id}", headers=_bot_headers(), timeout=15)
            if r.status_code != 200:
                return f"User {user_id}"
            u = r.json()
            return u.get("global_name") or u.get("username") or f"User {user_id}"
        except requests.RequestException:
            return f"User {user_id}"
    return _cached(f"u:{user_id}", 300, fn)
