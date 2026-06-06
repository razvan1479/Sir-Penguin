"""
dashboard/app.py — panoul web de configurare, cu login prin Discord (OAuth2).

Te loghezi cu contul tau de Discord -> vezi DOAR serverele tale (unde esti
admin/owner) in care e si botul. Numele si pozele sunt reale, iar in leaderboard
apar numele reale ale userilor in loc de ID-uri.

Necesita in .env: DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI,
FLASK_SECRET (si DISCORD_TOKEN, deja existent, pentru numele din leaderboard).

Ruleaza separat de bot:  python dashboard/app.py  -> http://localhost:5000
"""

import os
import sys
import ssl
import time
from functools import wraps
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, session, abort)

from utils import storage

load_dotenv(os.path.join(ROOT, ".env"))

# --- config OAuth ---
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:5000/callback")
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
API = "https://discord.com/api"
OAUTH_OK = bool(CLIENT_ID and CLIENT_SECRET)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "schimba-acest-secret")


# --- sesiune HTTP cu fix SSL (acelasi ca la bot, pt retele corporate) ---
class _LaxAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


http = requests.Session()
http.mount("https://", _LaxAdapter())


# ============================================================== helperi
def _bot_guilds():
    """ID-urile serverelor in care e botul (au 'meta' scris de bot)."""
    return {gid for gid, d in storage.all_data().items()
            if not gid.startswith("_") and d.get("meta")}


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "user" not in session:
            return redirect(url_for("index"))
        return f(*a, **k)
    return w


def guild_required(f):
    """Acces doar la serverele pe care le administreaza userul logat."""
    @wraps(f)
    def w(guild_id, *a, **k):
        if "user" not in session:
            return redirect(url_for("index"))
        managed = {g["id"] for g in session.get("guilds", [])}
        if str(guild_id) not in managed:
            abort(403)
        return f(guild_id, *a, **k)
    return w


_name_cache = {}  # (gid,uid) -> (name, ts)


def resolve_name(guild_id, user_id):
    """Numele real al unui membru (prin tokenul botului), cu cache 1h."""
    if not BOT_TOKEN:
        return None
    key = (str(guild_id), str(user_id))
    now = time.time()
    cached = _name_cache.get(key)
    if cached and now - cached[1] < 3600:
        return cached[0]
    name = None
    try:
        r = http.get(f"{API}/guilds/{guild_id}/members/{user_id}",
                     headers={"Authorization": f"Bot {BOT_TOKEN}"}, timeout=10)
        if r.status_code == 200:
            d = r.json()
            u = d.get("user", {})
            name = d.get("nick") or u.get("global_name") or u.get("username")
    except requests.RequestException:
        name = None
    _name_cache[key] = (name, now)
    return name


# ============================================================== OAuth
@app.route("/login")
def login():
    if not OAUTH_OK:
        return redirect(url_for("index"))
    q = urlencode({"client_id": CLIENT_ID, "response_type": "code",
                   "scope": "identify guilds", "redirect_uri": REDIRECT_URI})
    return redirect(f"https://discord.com/oauth2/authorize?{q}")


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))
    try:
        tok = http.post(f"{API}/oauth2/token", data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT_URI,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15).json()
        access = tok.get("access_token")
        if not access:
            return "Autentificare esuata. Verifica datele OAuth din .env.", 400
        h = {"Authorization": f"Bearer {access}"}
        user = http.get(f"{API}/users/@me", headers=h, timeout=15).json()
        guilds = http.get(f"{API}/users/@me/guilds", headers=h, timeout=15).json()
    except requests.RequestException as e:
        return f"Eroare de retea la Discord: {e}", 502

    if not isinstance(guilds, list):
        return "Nu am putut prelua serverele (probabil rate limit). Reincearca.", 429

    managed = []
    for g in guilds:
        perms = int(g.get("permissions", 0))
        if g.get("owner") or (perms & 0x8) or (perms & 0x20):  # admin sau manage server
            icon = g.get("icon")
            managed.append({
                "id": g["id"], "name": g["name"],
                "icon": f"https://cdn.discordapp.com/icons/{g['id']}/{icon}.png" if icon else None,
            })

    avatar = user.get("avatar")
    session["user"] = {
        "id": user["id"],
        "name": user.get("global_name") or user.get("username"),
        "avatar": f"https://cdn.discordapp.com/avatars/{user['id']}/{avatar}.png" if avatar else None,
    }
    session["guilds"] = managed
    session.permanent = True
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ============================================================== pagini
@app.route("/")
def index():
    if "user" not in session:
        return render_template("login.html", configured=OAUTH_OK)

    bot_g = _bot_guilds()
    mine = session.get("guilds", [])
    active, not_added = [], []
    for g in mine:
        if g["id"] in bot_g:
            meta = storage.get(int(g["id"]), "meta", {})
            g = dict(g)
            g["members"] = meta.get("members")
            if not g.get("icon"):
                g["icon"] = meta.get("icon")
            active.append(g)
        else:
            not_added.append(g)

    invite = None
    if CLIENT_ID:
        invite = (f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
                  f"&permissions=268659761&scope=bot%20applications.commands")
    return render_template("index.html", guilds=active, not_added=not_added,
                           user=session["user"], invite_url=invite)


@app.route("/api/guild/<guild_id>")
@guild_required
def api_guild(guild_id):
    try:
        meta = storage.get(int(guild_id), "meta", {})
    except ValueError:
        meta = {}
    return jsonify(meta or {})


@app.route("/guild/<guild_id>", methods=["GET", "POST"])
@guild_required
def guild_settings(guild_id):
    if request.method == "POST":
        cfg = {
            "enabled": request.form.get("enabled") == "on",
            "channel_id": request.form.get("channel_id", "").strip(),
            "message": request.form.get("message", "").strip(),
            "show_avatar": request.form.get("show_avatar") == "on",
            "show_banner": request.form.get("show_banner") == "on",
            "show_inviter": request.form.get("show_inviter") == "on",
            "color": request.form.get("color", "#5865f2"),
        }
        if cfg["channel_id"]:
            try:
                cfg["channel_id"] = int(cfg["channel_id"])
            except ValueError:
                cfg["channel_id"] = ""
        storage.set(int(guild_id), "welcome", cfg)
        return redirect(url_for("guild_settings", guild_id=guild_id, saved=1))

    cfg = storage.get(int(guild_id), "welcome", {})
    meta = storage.get(int(guild_id), "meta", {})
    return render_template("guild.html", guild_id=guild_id, cfg=cfg, meta=meta,
                           section="welcome", saved=request.args.get("saved"))


def _invite_total(s):
    return s.get("regular", 0) + s.get("bonus", 0) - s.get("left", 0) - s.get("fake", 0)


def _period_counts(invdata, days):
    cutoff = time.time() - days * 86400
    counts = {}
    for e in invdata.get("history", []):
        if e.get("ts", 0) < cutoff or e.get("fake") or e.get("left"):
            continue
        inv = e.get("inviter")
        if not inv or inv in ("vanity", "unknown"):
            continue
        counts[inv] = counts.get(inv, 0) + 1
    return counts


@app.route("/leaderboard/<guild_id>")
@guild_required
def leaderboard(guild_id):
    invdata = storage.get(int(guild_id), "invites", {})
    members = invdata.get("members", {})

    all_time = sorted(
        [(uid, _invite_total(s)) for uid, s in members.items() if _invite_total(s) > 0],
        key=lambda x: x[1], reverse=True)[:10]
    week = sorted(_period_counts(invdata, 7).items(), key=lambda x: x[1], reverse=True)[:10]
    month = sorted(_period_counts(invdata, 30).items(), key=lambda x: x[1], reverse=True)[:10]

    # rezolvam ID-urile in nume reale (doar pentru cei afisati)
    ids = {uid for uid, _ in all_time + week + month}
    names = {uid: (resolve_name(guild_id, uid) or f"User {uid}") for uid in ids}

    return render_template("leaderboard.html", guild_id=guild_id,
                           all_time=all_time, week=week, month=month, names=names,
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="leaderboard",
                           vanity=invdata.get("vanity_count", 0))


@app.route("/embeds/<guild_id>", methods=["GET", "POST"])
@guild_required
def embeds(guild_id):
    data = storage.get(int(guild_id), "embeds", {})

    if request.method == "POST":
        action = request.form.get("action")
        name = request.form.get("name", "").strip()

        if action == "delete":
            data.pop(name, None)
            storage.set(int(guild_id), "embeds", data)
            return redirect(url_for("embeds", guild_id=guild_id))

        if name:
            data[name] = {
                "title": request.form.get("title", "").strip(),
                "description": request.form.get("description", "").strip(),
                "color": request.form.get("color", "#5865f2"),
                "image": request.form.get("image", "").strip(),
                "thumbnail": request.form.get("thumbnail", "").strip(),
                "footer": request.form.get("footer", "").strip(),
            }
            storage.set(int(guild_id), "embeds", data)
            return redirect(url_for("embeds", guild_id=guild_id, edit=name, saved=1))

    edit = request.args.get("edit", "")
    current = data.get(edit, {}) if edit else {}
    return render_template("embeds.html", guild_id=guild_id, embeds=data,
                           current=current, edit_name=edit,
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="embeds", saved=request.args.get("saved"))


def _to_int(v):
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


@app.route("/giveaway/<guild_id>", methods=["GET", "POST"])
@guild_required
def giveaway(guild_id):
    data = storage.get(int(guild_id), "giveaways", {})

    if request.method == "POST":
        cfg = {
            "channel_id": _to_int(request.form.get("channel_id", "")),
            "prize": request.form.get("prize", "").strip(),
            "duration_minutes": _to_int(request.form.get("duration_minutes", "60")) or 60,
            "winners": _to_int(request.form.get("winners", "1")) or 1,
            "button_label": request.form.get("button_label", "").strip() or "🎉 Particip",
            "title": request.form.get("title", "").strip() or "🎉 GIVEAWAY 🎉",
            "color": request.form.get("color", "#5865f2"),
            "recurring": request.form.get("recurring") == "on",
            "interval_hours": _to_int(request.form.get("interval_hours", "24")) or 24,
            "ping_everyone": request.form.get("ping_everyone") == "on",
            "required_role_id": _to_int(request.form.get("required_role_id", "")),
        }
        data["config"] = cfg
        if not cfg["recurring"]:
            data["next_post_ts"] = None
        else:
            data.pop("next_post_ts", None)
        storage.set(int(guild_id), "giveaways", data)
        return redirect(url_for("giveaway", guild_id=guild_id, saved=1))

    return render_template("giveaway.html", guild_id=guild_id,
                           cfg=data.get("config", {}),
                           active_count=len(data.get("active", {})),
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="giveaway", saved=request.args.get("saved"))


import uuid


@app.route("/notifications/<guild_id>", methods=["GET", "POST"])
@guild_required
def notifications(guild_id):
    data = storage.get(int(guild_id), "notifications", {})
    subs = data.get("subscriptions", [])

    if request.method == "POST":
        if request.form.get("action") == "delete":
            sid = request.form.get("id")
            subs = [s for s in subs if s.get("id") != sid]
            data["subscriptions"] = subs
            storage.set(int(guild_id), "notifications", data)
            return redirect(url_for("notifications", guild_id=guild_id))

        platform = request.form.get("platform", "")
        url = request.form.get("url", "").strip()
        dch = _to_int(request.form.get("discord_channel_id", ""))
        if platform in ("youtube", "twitch", "kick", "tiktok") and url and dch:
            subs.append({
                "id": uuid.uuid4().hex[:8],
                "platform": platform,
                "url": url,
                "discord_channel_id": dch,
                "message": request.form.get("message", "").strip(),
                "role_id": _to_int(request.form.get("role_id", "")),
                "identifier": None,
                "initialized": False,
            })
            data["subscriptions"] = subs
            storage.set(int(guild_id), "notifications", data)
        return redirect(url_for("notifications", guild_id=guild_id, saved=1))

    return render_template("notifications.html", guild_id=guild_id, subs=subs,
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="notifications", saved=request.args.get("saved"))


@app.route("/rankup/<guild_id>", methods=["GET", "POST"])
@guild_required
def rankup(guild_id):
    if request.method == "POST":
        ranks_raw = request.form.get("ranks", "")
        ranks = [r.strip() for r in ranks_raw.split(",") if r.strip()]
        cfg = {
            "enabled": request.form.get("enabled") == "on",
            "log_channel_id": _to_int(request.form.get("log_channel_id", "")),
            "ranks": ranks or ["🫡", "⭐", "⭐⭐", "⭐⭐⭐", "⚡", "⚡⚡", "⚡⚡⚡", "✨"],
            "first_role_id": _to_int(request.form.get("first_role_id", "")),
            "ultimate_role_id": _to_int(request.form.get("ultimate_role_id", "")),
            "ultimate_rank_index": _to_int(request.form.get("ultimate_rank_index", "4")) or 4,
            "min_days": _to_int(request.form.get("min_days", "30")) or 30,
            "first_star_days": _to_int(request.form.get("first_star_days", "180")) or 180,
            "interval_months": _to_int(request.form.get("interval_months", "6")) or 6,
        }
        storage.set(int(guild_id), "rankup", cfg)
        return redirect(url_for("rankup", guild_id=guild_id, saved=1))

    cfg = storage.get(int(guild_id), "rankup", {})
    return render_template("rankup.html", guild_id=guild_id, cfg=cfg,
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="rankup", saved=request.args.get("saved"))


@app.route("/game/<guild_id>", methods=["GET", "POST"])
@guild_required
def game(guild_id):
    if request.method == "POST":
        cfg = {
            "enabled": request.form.get("enabled") == "on",
            "channel_id": _to_int(request.form.get("channel_id", "")),
            "countdown": _to_int(request.form.get("countdown", "60")) or 60,
            "min": _to_int(request.form.get("min", "0")) or 0,
            "max": _to_int(request.form.get("max", "100")) or 100,
        }
        storage.set(int(guild_id), "game", cfg)
        return redirect(url_for("game", guild_id=guild_id, saved=1))

    cfg = storage.get(int(guild_id), "game", {})
    return render_template("game.html", guild_id=guild_id, cfg=cfg,
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="game", saved=request.args.get("saved"))


@app.route("/rps/<guild_id>", methods=["GET", "POST"])
@guild_required
def rps(guild_id):
    if request.method == "POST":
        cfg = {
            "enabled": request.form.get("enabled") == "on",
            "channel_id": _to_int(request.form.get("channel_id", "")),
        }
        storage.set(int(guild_id), "rps", cfg)
        return redirect(url_for("rps", guild_id=guild_id, saved=1))

    cfg = storage.get(int(guild_id), "rps", {})
    return render_template("rps.html", guild_id=guild_id, cfg=cfg,
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="rps", saved=request.args.get("saved"))


import time as _time


@app.route("/massrole/<guild_id>", methods=["GET", "POST"])
@guild_required
def massrole(guild_id):
    if request.method == "POST":
        action = request.form.get("action", "")
        if action in ("give_all", "remove_all", "give_to", "remove_from"):
            job = {
                "id": uuid.uuid4().hex[:8],
                "action": action,
                "role_id": _to_int(request.form.get("role_id", "")),
                "condition_role_id": _to_int(request.form.get("condition_role_id", "")),
                "include_bots": request.form.get("include_bots") == "on",
                "status": "pending",
                "result": "",
                "created_ts": _time.time(),
            }
            if job["role_id"]:
                storage.set(int(guild_id), "massrole_job", job)
        return redirect(url_for("massrole", guild_id=guild_id, sent=1))

    roles = storage.get(int(guild_id), "roles", {}) or {}
    job = storage.get(int(guild_id), "massrole_job", None)
    return render_template("massrole.html", guild_id=guild_id,
                           roles=roles.get("list", []), job=job,
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="massrole", sent=request.args.get("sent"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
