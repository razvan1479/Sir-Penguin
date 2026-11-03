"""
Server de licențe pentru Discord Community Manager.

Ce face:
  - login cu Discord pentru aplicație (schimbul cod -> token se face AICI, secretul nu ajunge la utilizator)
  - activare chei de licență cu durată personalizată (timpul curge de la activare)
  - licențe semnate Ed25519, verificabile offline de aplicație
  - pagină de admin (doar pentru ID-urile din ADMIN_DISCORD_IDS): creare chei, prelungire, revocare
"""

import base64
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


# ───────────────────────── Configurare ─────────────────────────

def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Lipsește variabila {name} din .env")
    return value


DISCORD_CLIENT_ID = _env("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = _env("DISCORD_CLIENT_SECRET")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
SESSION_SECRET = _env("SESSION_SECRET")
# Pagina de admin proprie (/admin). Implicit OPRITĂ: licențele se administrează din dashboard-ul Sir Penguin.
WEB_ADMIN_ENABLED = os.getenv("WEB_ADMIN_ENABLED", "false").lower() == "true"
ADMIN_DISCORD_IDS = {x.strip() for x in os.getenv("ADMIN_DISCORD_IDS", "").split(",") if x.strip()}
# Token secret partajat cu dashboard-ul Sir Penguin (API-ul /internal/*)
INTERNAL_ADMIN_TOKEN = _env("INTERNAL_ADMIN_TOKEN")
if len(INTERNAL_ADMIN_TOKEN) < 32:
    raise RuntimeError("INTERNAL_ADMIN_TOKEN trebuie să aibă cel puțin 32 de caractere")
# Redirect-urile pe care are voie aplicația desktop să le folosească (trebuie trecute și în Developer Portal)
APP_REDIRECT_URIS = {
    x.strip() for x in os.getenv("APP_REDIRECT_URIS", "http://127.0.0.1:53134/callback").split(",") if x.strip()
}
ADMIN_REDIRECT_URI = f"{PUBLIC_BASE_URL}/admin/callback"
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "licenses.db"))
SIGNING_KEY_PATH = Path(os.getenv("SIGNING_KEY_PATH", BASE_DIR / "data" / "signing_key.pem"))
KEY_PREFIX = os.getenv("KEY_PREFIX", "IDEAL")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Bucharest"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"

DISCORD_API = "https://discord.com/api/v10"
TICKET_MAX_AGE = 15 * 60          # cât e valabil „bonul” primit după login (secunde)
PERMANENT_SECONDS = 100 * 365 * 86400

UNITS = {"ore": 3600, "zile": 86400, "luni": 30 * 86400, "permanent": PERMANENT_SECONDS}

ticket_signer = URLSafeTimedSerializer(SESSION_SECRET, salt="app-login-ticket")


# ───────────────────────── Cheia de semnare ─────────────────────────

def load_or_create_signing_key() -> Ed25519PrivateKey:
    """Cheia privată Ed25519. Se generează o singură dată. FĂ BACKUP la fișier!"""
    if SIGNING_KEY_PATH.exists():
        return serialization.load_pem_private_key(SIGNING_KEY_PATH.read_bytes(), password=None)
    SIGNING_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    SIGNING_KEY_PATH.write_bytes(pem)
    try:
        os.chmod(SIGNING_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


SIGNING_KEY = load_or_create_signing_key()


def public_key_b64() -> str:
    raw = SIGNING_KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_license(discord_id: str, username: str, expires_at: int) -> str:
    """Licența = <payload base64url>.<semnătură base64url>. Aplicația o verifică offline."""
    payload = {"v": 1, "sub": discord_id, "name": username, "iat": int(time.time()), "exp": expires_at}
    body = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64u(SIGNING_KEY.sign(body.encode()))
    return f"{body}.{sig}"


def verify_license_token(token: str) -> dict | None:
    """Verifică semnătura (nu și expirarea). Întoarce payload-ul sau None."""
    try:
        body, sig = token.split(".")
        SIGNING_KEY.public_key().verify(_b64u_decode(sig), body.encode())
        return json.loads(_b64u_decode(body))
    except Exception:
        return None


# ───────────────────────── Baza de date ─────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    key              TEXT PRIMARY KEY,
    duration_seconds INTEGER NOT NULL,
    note             TEXT NOT NULL DEFAULT '',
    created_at       INTEGER NOT NULL,
    created_by       TEXT,
    used_by          TEXT,
    used_at          INTEGER
);
CREATE TABLE IF NOT EXISTS users (
    discord_id         TEXT PRIMARY KEY,
    username           TEXT NOT NULL DEFAULT '',
    expires_at         INTEGER NOT NULL,
    revoked            INTEGER NOT NULL DEFAULT 0,
    first_activated_at INTEGER NOT NULL,
    last_seen_at       INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    discord_id TEXT,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_event(conn, discord_id: str | None, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO events (ts, discord_id, action, detail) VALUES (?, ?, ?, ?)",
        (int(time.time()), discord_id, action, detail),
    )


KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # fără 0/O, 1/I, ca să nu se încurce


def generate_key() -> str:
    groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(4)]
    return f"{KEY_PREFIX}-" + "-".join(groups)


def normalize_key(raw: str) -> str:
    return "".join(raw.split()).upper()


def create_keys(duration_seconds: int, count: int, note: str, created_by: str) -> list[str]:
    created = []
    with db() as conn:
        for _ in range(count):
            key = generate_key()
            conn.execute(
                "INSERT INTO keys (key, duration_seconds, note, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                (key, duration_seconds, note, int(time.time()), created_by),
            )
            created.append(key)
        log_event(conn, created_by, "keys_created", f"{count} x {human_duration(duration_seconds)} {note}".strip())
    return created


def license_response(conn, user_row) -> dict:
    """Răspunsul standard despre starea licenței unui utilizator."""
    now = int(time.time())
    if user_row is None:
        return {"status": "no_license"}
    conn.execute("UPDATE users SET last_seen_at = ? WHERE discord_id = ?", (now, user_row["discord_id"]))
    if user_row["revoked"]:
        return {"status": "revoked"}
    if user_row["expires_at"] <= now:
        return {"status": "expired", "expires_at": user_row["expires_at"]}
    return {
        "status": "active",
        "expires_at": user_row["expires_at"],
        "license": sign_license(user_row["discord_id"], user_row["username"], user_row["expires_at"]),
    }


# ───────────────────────── Utilitare ─────────────────────────

def human_duration(seconds: int) -> str:
    if seconds >= PERMANENT_SECONDS:
        return "permanent"
    days, rem = divmod(seconds, 86400)
    hours = rem // 3600
    parts = []
    if days:
        parts.append(f"{days} {'zi' if days == 1 else 'zile'}")
    if hours:
        parts.append(f"{hours} {'oră' if hours == 1 else 'ore'}")
    return " ".join(parts) or "sub 1 oră"


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "—"
    if ts - time.time() > PERMANENT_SECONDS / 2:
        return "permanent"
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M")


def remaining(ts: int) -> str:
    left = ts - int(time.time())
    if left <= 0:
        return "expirat"
    return human_duration(left)


def parse_duration(amount: int, unit: str) -> int:
    if unit not in UNITS:
        raise HTTPException(400, "Unitate invalidă")
    if unit == "permanent":
        return PERMANENT_SECONDS
    if amount < 1 or amount > 10000:
        raise HTTPException(400, "Durata trebuie să fie între 1 și 10000")
    return amount * UNITS[unit]


# Limită simplă de încercări (anti-ghicire chei), în memorie
_attempts: dict[str, list[float]] = {}


def rate_limited(bucket: str, limit: int, window: int) -> bool:
    now = time.time()
    hits = [t for t in _attempts.get(bucket, []) if now - t < window]
    hits.append(now)
    _attempts[bucket] = hits
    return len(hits) > limit


async def discord_exchange_code(code: str, redirect_uri: str) -> dict:
    """Schimbă codul OAuth2 pe token și întoarce profilul Discord (/users/@me)."""
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            f"{DISCORD_API}/oauth2/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            auth=(DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(400, "Discord a refuzat codul (expirat sau redirect greșit)")
        access_token = token_resp.json()["access_token"]
        me = await client.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        if me.status_code != 200:
            raise HTTPException(502, "Nu am putut citi profilul Discord")
        return me.json()


def display_name(user: dict) -> str:
    return user.get("global_name") or user.get("username") or str(user.get("id"))


# ───────────────────────── Aplicația web ─────────────────────────

app = FastAPI(title="iDeaL License Server", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware, secret_key=SESSION_SECRET, https_only=COOKIE_SECURE, same_site="lax", max_age=8 * 3600
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(fmt_ts=fmt_ts, remaining=remaining, human_duration=human_duration)

init_db()


@app.get("/health")
def health():
    return {"ok": True}


# ---------- API pentru aplicația desktop ----------

class ExchangeIn(BaseModel):
    code: str
    redirect_uri: str


class TicketIn(BaseModel):
    ticket: str


class ActivateIn(BaseModel):
    ticket: str
    key: str


class RefreshIn(BaseModel):
    license: str


def read_ticket(ticket: str) -> dict:
    try:
        return ticket_signer.loads(ticket, max_age=TICKET_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(401, "Sesiunea de login a expirat, loghează-te din nou")
    except BadSignature:
        raise HTTPException(401, "Sesiune invalidă")


@app.post("/api/auth/exchange")
async def api_auth_exchange(body: ExchangeIn, request: Request):
    """Aplicația trimite codul primit de la Discord; serverul face schimbul cu secretul."""
    if body.redirect_uri not in APP_REDIRECT_URIS:
        raise HTTPException(400, "Redirect URI nepermis")
    if rate_limited(f"exchange:{request.client.host}", limit=20, window=600):
        raise HTTPException(429, "Prea multe încercări, mai așteaptă puțin")
    user = await discord_exchange_code(body.code, body.redirect_uri)
    info = {"id": str(user["id"]), "name": display_name(user)}
    return {
        "ticket": ticket_signer.dumps(info),
        "user": {
            "id": info["id"],
            "name": info["name"],
            "username": user.get("username"),
            "avatar": user.get("avatar"),
        },
    }


@app.post("/api/license/status")
def api_license_status(body: TicketIn):
    """După login: are utilizatorul deja o licență activă?"""
    info = read_ticket(body.ticket)
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (info["id"],)).fetchone()
        return license_response(conn, row)


@app.post("/api/license/activate")
def api_license_activate(body: ActivateIn, request: Request):
    """Leagă cheia de contul Discord. Dacă există deja timp rămas, se adaugă peste."""
    info = read_ticket(body.ticket)
    if rate_limited(f"activate:{request.client.host}", limit=10, window=600) or rate_limited(
        f"activate:{info['id']}", limit=10, window=600
    ):
        raise HTTPException(429, "Prea multe încercări, mai așteaptă 10 minute")

    key = normalize_key(body.key)
    now = int(time.time())
    with db() as conn:
        key_row = conn.execute("SELECT * FROM keys WHERE key = ?", (key,)).fetchone()
        if key_row is None:
            raise HTTPException(404, "Cheie invalidă")
        if key_row["used_by"]:
            raise HTTPException(409, "Cheia a fost deja folosită")

        user_row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (info["id"],)).fetchone()
        if user_row and user_row["revoked"]:
            raise HTTPException(403, "Licența acestui cont a fost revocată")

        base = max(now, user_row["expires_at"]) if user_row else now
        new_exp = base + key_row["duration_seconds"]

        if user_row:
            conn.execute(
                "UPDATE users SET expires_at = ?, username = ? WHERE discord_id = ?", (new_exp, info["name"], info["id"])
            )
        else:
            conn.execute(
                "INSERT INTO users (discord_id, username, expires_at, first_activated_at) VALUES (?, ?, ?, ?)",
                (info["id"], info["name"], new_exp, now),
            )
        conn.execute("UPDATE keys SET used_by = ?, used_at = ? WHERE key = ?", (info["id"], now, key))
        log_event(conn, info["id"], "activated", f"{key} (+{human_duration(key_row['duration_seconds'])})")

        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (info["id"],)).fetchone()
        return license_response(conn, row)


@app.post("/api/license/refresh")
def api_license_refresh(body: RefreshIn):
    """Verificare periodică fără login: aplicația trimite licența salvată, primește starea actuală
    (prelungiri sau revocări făcute între timp de admin)."""
    payload = verify_license_token(body.license)
    if payload is None:
        raise HTTPException(401, "Licență invalidă")
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (payload["sub"],)).fetchone()
        return license_response(conn, row)


# ---------- Operații de admin (folosite și de API-ul intern, și de pagina /admin) ----------

def op_delete_key(key: str, actor: str) -> bool:
    with db() as conn:
        cur = conn.execute("DELETE FROM keys WHERE key = ? AND used_by IS NULL", (key,))
        if cur.rowcount:
            log_event(conn, actor, "key_deleted", key)
        return bool(cur.rowcount)


def op_extend_user(discord_id: str, duration: int, actor: str) -> sqlite3.Row:
    now = int(time.time())
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Utilizator inexistent")
        new_exp = max(now, row["expires_at"]) + duration
        conn.execute("UPDATE users SET expires_at = ? WHERE discord_id = ?", (new_exp, discord_id))
        log_event(conn, discord_id, "extended", f"+{human_duration(duration)} de {actor}")
        return row


def op_set_revoked(discord_id: str, revoked: bool, actor: str) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Utilizator inexistent")
        conn.execute("UPDATE users SET revoked = ? WHERE discord_id = ?", (1 if revoked else 0, discord_id))
        log_event(conn, discord_id, "revoked" if revoked else "unrevoked", f"de {actor}")
        return row


def op_overview(key_filter: str = "toate") -> dict:
    now = int(time.time())
    key_query = {
        "nefolosite": "SELECT * FROM keys WHERE used_by IS NULL ORDER BY created_at DESC LIMIT 300",
        "folosite": "SELECT * FROM keys WHERE used_by IS NOT NULL ORDER BY used_at DESC LIMIT 300",
    }.get(key_filter, "SELECT * FROM keys ORDER BY created_at DESC LIMIT 300")
    with db() as conn:
        users = [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY expires_at DESC")]
        keys = [dict(r) for r in conn.execute(key_query)]
        events = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")]
        unused = conn.execute("SELECT COUNT(*) FROM keys WHERE used_by IS NULL").fetchone()[0]
    names = {u["discord_id"]: u["username"] for u in users}
    for u in users:
        u["state"] = "revoked" if u["revoked"] else ("expired" if u["expires_at"] <= now else "active")
        u["expires_fmt"] = fmt_ts(u["expires_at"])
        u["remaining"] = remaining(u["expires_at"])
        u["last_seen_fmt"] = fmt_ts(u["last_seen_at"])
    for k in keys:
        k["duration_fmt"] = human_duration(k["duration_seconds"])
        k["created_fmt"] = fmt_ts(k["created_at"])
        k["used_fmt"] = fmt_ts(k["used_at"])
        k["used_by_name"] = names.get(k["used_by"], k["used_by"]) if k["used_by"] else None
    labels = {"keys_created": "chei create", "activated": "activare", "extended": "prelungire",
              "revoked": "revocare", "unrevoked": "reactivare", "key_deleted": "cheie ștearsă"}
    for e in events:
        e["action"] = labels.get(e["action"], e["action"])
        e["ts_fmt"] = fmt_ts(e["ts"])
        e["who"] = names.get(e["discord_id"], e["discord_id"] or "—")
    return {
        "stats": {
            "active": sum(1 for u in users if u["state"] == "active"),
            "users": len(users),
            "unused": unused,
        },
        "users": users,
        "keys": keys,
        "events": events,
        "units": list(UNITS.keys()),
    }


# ---------- API intern pentru dashboard-ul Sir Penguin ----------
# Protejat cu INTERNAL_ADMIN_TOKEN. În Caddy, /internal/* e blocat oricum din exterior.

def require_internal(request: Request) -> None:
    token = request.headers.get("X-Admin-Token", "")
    if not token or not secrets.compare_digest(token, INTERNAL_ADMIN_TOKEN):
        raise HTTPException(401, "Token intern invalid")


class InternalCreateIn(BaseModel):
    amount: int = 30
    unit: str = "zile"
    count: int = 1
    note: str = ""
    actor: str = "dashboard"


class InternalUserIn(BaseModel):
    discord_id: str
    actor: str = "dashboard"
    amount: int = 7
    unit: str = "zile"
    revoked: bool = True


class InternalKeyIn(BaseModel):
    key: str
    actor: str = "dashboard"


@app.get("/internal/overview")
def internal_overview(request: Request, filtru: str = "toate"):
    require_internal(request)
    return op_overview(filtru)


@app.post("/internal/keys/create")
def internal_create_keys(body: InternalCreateIn, request: Request):
    require_internal(request)
    if body.count < 1 or body.count > 50:
        raise HTTPException(400, "Poți crea între 1 și 50 de chei odată")
    duration = parse_duration(body.amount, body.unit)
    keys = create_keys(duration, body.count, body.note.strip()[:200], body.actor)
    return {"keys": keys, "duration": human_duration(duration)}


@app.post("/internal/keys/delete")
def internal_delete_key(body: InternalKeyIn, request: Request):
    require_internal(request)
    return {"deleted": op_delete_key(body.key, body.actor)}


@app.post("/internal/users/extend")
def internal_extend_user(body: InternalUserIn, request: Request):
    require_internal(request)
    duration = parse_duration(body.amount, body.unit)
    row = op_extend_user(body.discord_id, duration, body.actor)
    return {"ok": True, "username": row["username"], "added": human_duration(duration)}


@app.post("/internal/users/revoke")
def internal_revoke_user(body: InternalUserIn, request: Request):
    require_internal(request)
    row = op_set_revoked(body.discord_id, body.revoked, body.actor)
    return {"ok": True, "username": row["username"], "revoked": body.revoked}


@app.get("/internal/public-key")
def internal_public_key(request: Request):
    require_internal(request)
    return {"public_key": public_key_b64()}


# ---------- Pagina de admin proprie (opțională, WEB_ADMIN_ENABLED=true) ----------

def web_admin_guard() -> None:
    if not WEB_ADMIN_ENABLED:
        raise HTTPException(404, "Pagina de admin e dezactivată. Licențele se administrează din dashboard-ul Sir Penguin.")


def current_admin(request: Request) -> dict | None:
    web_admin_guard()
    admin = request.session.get("admin")
    if admin and admin.get("id") in ADMIN_DISCORD_IDS:
        return admin
    return None


def check_csrf(request: Request, token: str) -> None:
    if not token or not secrets.compare_digest(token, request.session.get("csrf", "")):
        raise HTTPException(403, "Token CSRF invalid, reîncarcă pagina")


def require_admin(request: Request) -> dict:
    admin = current_admin(request)
    if not admin:
        raise HTTPException(401, "Nu ești logat ca admin")
    return admin


@app.get("/", include_in_schema=False)
def root():
    if WEB_ADMIN_ENABLED:
        return RedirectResponse("/admin")
    return {"service": "iDeaL License Server", "ok": True}


@app.get("/admin/login")
def admin_login(request: Request):
    web_admin_guard()
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": ADMIN_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "none",
    }
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{urlencode(params)}")


@app.get("/admin/callback")
async def admin_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    web_admin_guard()
    if error or not code:
        return RedirectResponse("/admin")
    expected = request.session.pop("oauth_state", None)
    if not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "State invalid, încearcă din nou")
    user = await discord_exchange_code(code, ADMIN_REDIRECT_URI)
    if str(user["id"]) not in ADMIN_DISCORD_IDS:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Contul tău nu are acces de admin."}, status_code=403
        )
    request.session["admin"] = {"id": str(user["id"]), "name": display_name(user)}
    request.session["csrf"] = secrets.token_urlsafe(24)
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/logout")
def admin_logout(request: Request):
    web_admin_guard()
    request.session.clear()
    return RedirectResponse("/admin")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, filtru: str = "toate"):
    admin = current_admin(request)
    if not admin:
        return templates.TemplateResponse(request, "login.html", {"error": None})
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    data = op_overview(filtru)
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            **data,
            "admin": admin,
            "csrf": request.session["csrf"],
            "names": {u["discord_id"]: u["username"] for u in data["users"]},
            "now": int(time.time()),
            "new_keys": request.session.pop("new_keys", None),
            "flash": request.session.pop("flash", None),
            "filtru": filtru,
        },
    )


@app.post("/admin/keys/create")
def admin_create_keys(
    request: Request,
    csrf: str = Form(""),
    amount: int = Form(30),
    unit: str = Form("zile"),
    count: int = Form(1),
    note: str = Form(""),
):
    admin = require_admin(request)
    check_csrf(request, csrf)
    if count < 1 or count > 50:
        raise HTTPException(400, "Poți crea între 1 și 50 de chei odată")
    duration = parse_duration(amount, unit)
    request.session["new_keys"] = {
        "items": create_keys(duration, count, note.strip()[:200], admin["name"]),
        "duration": human_duration(duration),
    }
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/keys/{key}/delete")
def admin_delete_key(request: Request, key: str, csrf: str = Form("")):
    admin = require_admin(request)
    check_csrf(request, csrf)
    if op_delete_key(key, admin["name"]):
        request.session["flash"] = f"Cheia {key} a fost ștearsă."
    else:
        request.session["flash"] = "Cheia nu există sau a fost deja folosită (nu se mai poate șterge)."
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{discord_id}/extend")
def admin_extend_user(
    request: Request, discord_id: str, csrf: str = Form(""), amount: int = Form(7), unit: str = Form("zile")
):
    admin = require_admin(request)
    check_csrf(request, csrf)
    duration = parse_duration(amount, unit)
    row = op_extend_user(discord_id, duration, admin["name"])
    request.session["flash"] = f"Licența lui {row['username']} a fost prelungită cu {human_duration(duration)}."
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{discord_id}/revoke")
def admin_toggle_revoke(request: Request, discord_id: str, csrf: str = Form("")):
    admin = require_admin(request)
    check_csrf(request, csrf)
    with db() as conn:
        row = conn.execute("SELECT revoked FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Utilizator inexistent")
    new_state = not row["revoked"]
    user = op_set_revoked(discord_id, new_state, admin["name"])
    verb = "revocată" if new_state else "reactivată"
    request.session["flash"] = f"Licența lui {user['username']} a fost {verb}."
    return RedirectResponse("/admin", status_code=303)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith(("/api/", "/internal/")):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(
        f"<body style='font-family:sans-serif;background:#0f1115;color:#e6e6e6;padding:40px'>"
        f"<h2>Eroare {exc.status_code}</h2><p>{exc.detail}</p><p><a style='color:#8ea1ff' href='/admin'>Înapoi</a></p></body>",
        status_code=exc.status_code,
    )
