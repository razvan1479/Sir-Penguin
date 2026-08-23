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
    """ID-urile serverelor in care e botul ACUM (lista live scrisa de bot)."""
    servers = storage.get(0, "bot_servers", None)
    if servers:
        return {str(s["id"]) for s in servers}
    # fallback (daca botul nu a scris inca lista live): pe baza datelor 'meta'
    return {gid for gid, d in storage.all_data().items()
            if not gid.startswith("_") and gid != "0" and d.get("meta")}


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


@app.route("/home/<guild_id>")
@guild_required
def home(guild_id):
    gid = int(guild_id)
    meta = storage.get(gid, "meta", {})
    invites = storage.get(gid, "invites", {}) or {}
    history = invites.get("history", [])
    inv_members = invites.get("members", {})

    # top invitator (real, fara conturi false/plecate)
    top = None
    ranked = sorted(
        [(uid, _invite_total(s)) for uid, s in inv_members.items()],
        key=lambda kv: kv[1], reverse=True)
    if ranked and ranked[0][1] > 0:
        top = {"name": resolve_name(gid, ranked[0][0]), "total": ranked[0][1]}

    # module active (cate au ceva configurat / pornit)
    active = 0
    for key in ["welcome", "goodbye", "giveaways", "tickets", "notifications", "rankup", "embeds"]:
        cfg = storage.get(gid, key, {})
        if cfg:
            active += 1

    stats = {
        "members": meta.get("members", 0),
        "invites_tracked": len(history),
        "inviters": sum(1 for s in inv_members.values() if _invite_total(s) > 0),
        "modules": active,
    }
    return render_template("home.html", guild_id=guild_id, meta=meta,
                           stats=stats, top=top, section="home")


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


@app.route("/goodbye/<guild_id>", methods=["GET", "POST"])
@guild_required
def goodbye_settings(guild_id):
    gid = int(guild_id)
    if request.method == "POST":
        cfg = {
            "enabled": request.form.get("enabled") == "on",
            "channel_id": _to_int(request.form.get("channel_id", "")),
            "message": request.form.get("message", "").strip(),
            "title": request.form.get("title", "").strip(),
            "show_avatar": request.form.get("show_avatar") == "on",
            "use_embed": request.form.get("use_embed") == "on",
            "color": request.form.get("color", "#ed4245"),
        }
        storage.set(gid, "goodbye", cfg)
        return redirect(url_for("goodbye_settings", guild_id=guild_id, saved=1))

    cfg = storage.get(gid, "goodbye", {})
    meta = storage.get(gid, "meta", {})
    channels = storage.get(gid, "channels", {}) or {}
    return render_template("goodbye.html", guild_id=guild_id, cfg=cfg, meta=meta,
                           channels=channels.get("texts", []),
                           section="goodbye", saved=request.args.get("saved"))


def _invite_total(s):
    return max(0, s.get("regular", 0) + s.get("bonus", 0) - s.get("left", 0) - s.get("fake", 0))


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

    # modul preferat: dashboard sau discord (salvat, schimbabil din query)
    mode = request.args.get("mode")
    if mode in ("dashboard", "discord"):
        data["mode"] = mode
        storage.set(int(guild_id), "giveaways", data)
    mode = data.get("mode", "dashboard")

    return render_template("giveaway.html", guild_id=guild_id,
                           cfg=data.get("config", {}),
                           active_count=len(data.get("active", {})),
                           mode=mode,
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
        if platform in ("youtube", "tiktok") and url and dch:
            # cuvinte cheie per creator (separate prin virgula) - gol = anunta tot
            raw_kw = request.form.get("keywords", "").strip()
            keywords = [k.strip() for k in raw_kw.split(",") if k.strip()]
            subs.append({
                "id": uuid.uuid4().hex[:8],
                "platform": platform,
                "url": url,
                "discord_channel_id": dch,
                "message": request.form.get("message", "").strip(),
                "role_id": _to_int(request.form.get("role_id", "")),
                "tiktok_mode": request.form.get("tiktok_mode", "both"),
                "keywords": keywords,
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
    gid = int(guild_id)
    if request.method == "POST":
        emojis = request.form.getlist("tier_emoji")
        days = request.form.getlist("tier_days")
        role_ids = request.form.getlist("tier_role")
        tiers = []
        for i in range(max(len(emojis), len(days), len(role_ids))):
            d = _to_int(days[i]) if i < len(days) else None
            if d is None:
                continue  # fara zile -> rand gol, il sarim
            tiers.append({
                "days": d,
                "emoji": (emojis[i].strip() if i < len(emojis) else ""),
                "role_id": (_to_int(role_ids[i]) if i < len(role_ids) else None),
            })
        tiers.sort(key=lambda x: x["days"])
        cfg = {
            "enabled": request.form.get("enabled") == "on",
            "log_channel_id": _to_int(request.form.get("log_channel_id", "")),
            "tiers": tiers,
        }
        storage.set(gid, "rankup", cfg)
        return redirect(url_for("rankup", guild_id=guild_id, saved=1))

    cfg = storage.get(gid, "rankup", {})
    roles = storage.get(gid, "roles", {}) or {}
    return render_template("rankup.html", guild_id=guild_id, cfg=cfg,
                           roles=roles.get("list", []),
                           meta=storage.get(gid, "meta", {}),
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


@app.route("/culori/<guild_id>", methods=["GET", "POST"])
@guild_required
def colors_settings(guild_id):
    if request.method == "POST":
        cfg = storage.get(int(guild_id), "colors", {}) or {}
        cfg["enabled"] = request.form.get("enabled") == "on"
        cfg["everyone"] = request.form.get("everyone") == "on"
        storage.set(int(guild_id), "colors", cfg)
        return redirect(url_for("colors_settings", guild_id=guild_id, saved=1))

    cfg = storage.get(int(guild_id), "colors", {})
    return render_template("colors.html", guild_id=guild_id, cfg=cfg,
                           meta=storage.get(int(guild_id), "meta", {}),
                           section="colors", saved=request.args.get("saved"))


@app.route("/conturinoi/<guild_id>", methods=["GET", "POST"])
@guild_required
def newacc_settings(guild_id):
    gid = int(guild_id)
    if request.method == "POST":
        if request.form.get("action") == "scan":
            # porneste scanarea intregului server (botul o executa in ~10s)
            storage.set(gid, "newacc_scan", {"status": "pending"})
            return redirect(url_for("newacc_settings", guild_id=guild_id))
        cfg = {
            "enabled": request.form.get("enabled") == "on",
            "days": _to_int(request.form.get("days", "30")) or 30,
            "role_id": _to_int(request.form.get("role_id", "")),
            "announce_enabled": request.form.get("announce_enabled") == "on",
            "announce_channel_id": _to_int(request.form.get("announce_channel_id", "")),
        }
        storage.set(gid, "newacc", cfg)
        return redirect(url_for("newacc_settings", guild_id=guild_id, saved=1))

    cfg = storage.get(gid, "newacc", {})
    roles = storage.get(gid, "roles", {}) or {}
    channels = storage.get(gid, "channels", {}) or {}
    scan = storage.get(gid, "newacc_scan", None)
    return render_template("newacc.html", guild_id=guild_id, cfg=cfg,
                           roles=[r for r in roles.get("list", []) if r.get("assignable")],
                           channels=channels.get("texts", []),
                           scan=scan,
                           meta=storage.get(gid, "meta", {}),
                           section="newacc", saved=request.args.get("saved"))


@app.route("/cleanup/<guild_id>", methods=["GET", "POST"])
@guild_required
def cleanup(guild_id):
    gid = int(guild_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "start":
            uid = _to_int(request.form.get("user_id", ""))
            scope = request.form.get("scope", "server")  # "server" sau "channel"
            job = {"status": "pending", "user_id": uid, "deleted": 0}
            if scope == "channel":
                job["channel_id"] = _to_int(request.form.get("channel_id", ""))
            storage.set(gid, "cleanup_job", job)
        elif action == "auto_add":
            uid = _to_int(request.form.get("user_id", ""))
            if uid:
                rules = storage.get(gid, "autodelete", {}) or {}
                if request.form.get("scope", "server") == "channel":
                    rules[str(uid)] = {"scope": "channel",
                                       "channel_id": _to_int(request.form.get("channel_id", ""))}
                else:
                    rules[str(uid)] = {"scope": "server"}
                storage.set(gid, "autodelete", rules)
        elif action == "auto_remove":
            uid = request.form.get("user_id", "")
            rules = storage.get(gid, "autodelete", {}) or {}
            rules.pop(str(uid), None)
            storage.set(gid, "autodelete", rules)
        return redirect(url_for("cleanup", guild_id=guild_id))

    channels = storage.get(gid, "channels", {}) or {}
    ch_list = channels.get("texts", [])
    ch_names = {str(c["id"]): c["name"] for c in ch_list}
    return render_template("cleanup.html", guild_id=guild_id,
                           channels=ch_list, ch_names=ch_names,
                           job=storage.get(gid, "cleanup_job", None),
                           autodelete=storage.get(gid, "autodelete", {}) or {},
                           meta=storage.get(gid, "meta", {}),
                           section="cleanup")


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


@app.route("/contest/<guild_id>", methods=["GET", "POST"])
@guild_required
def contest(guild_id):
    gid = int(guild_id)
    if request.method == "POST":
        action = request.form.get("action")
        c = storage.get(gid, "contest", {}) or {}
        if action == "start" and c.get("status") not in ("scheduled", "running"):
            now = _time.time()
            start_delay_h = float(request.form.get("start_delay", "0") or 0)
            dur_days = float(request.form.get("duration_days", "0") or 0)
            dur_hours = float(request.form.get("duration_hours", "0") or 0)
            start_ts = now + start_delay_h * 3600
            dur = dur_days * 86400 + dur_hours * 3600
            end_ts = (start_ts + dur) if dur > 0 else None
            storage.set(gid, "contest", {
                "status": "scheduled" if start_ts > now + 5 else "running",
                "name": request.form.get("name", "").strip() or "Concurs invitatii",
                "start_ts": start_ts,
                "end_ts": end_ts,
                "announce_channel_id": _to_int(request.form.get("announce_channel_id", "")),
                "winners_count": _to_int(request.form.get("winners_count", "1")) or 1,
                "live_enabled": request.form.get("live_enabled") == "on",
                "live_channel_id": _to_int(request.form.get("live_channel_id", "")),
                "live_interval_minutes": _to_int(request.form.get("live_interval_minutes", "30")) or 30,
                "board_count": _to_int(request.form.get("board_count", "10")) or 10,
                "live_message_id": None, "live_last_post": 0,
            })
        elif action == "live":
            # actualizeaza doar setarile de clasament live (merge si in timpul concursului)
            c["live_enabled"] = request.form.get("live_enabled") == "on"
            c["live_channel_id"] = _to_int(request.form.get("live_channel_id", ""))
            c["live_interval_minutes"] = _to_int(request.form.get("live_interval_minutes", "30")) or 30
            c["board_count"] = _to_int(request.form.get("board_count", "10")) or 10
            c["live_message_id"] = None  # forteaza un mesaj nou cu noile setari
            c["live_last_post"] = 0
            storage.set(gid, "contest", c)
        elif action == "exclude":
            # adauga o persoana la lista de exclusi (nu apare in clasament)
            uid = _to_int(request.form.get("exclude_id", ""))
            if uid:
                excluded = c.get("excluded", [])
                if uid not in excluded:
                    excluded.append(uid)
                c["excluded"] = excluded
                c["live_message_id"] = None  # actualizeaza clasamentul live
                storage.set(gid, "contest", c)
        elif action == "include":
            # scoate o persoana din lista de exclusi (revine in clasament)
            uid = _to_int(request.form.get("include_id", ""))
            excluded = [x for x in c.get("excluded", []) if x != uid]
            c["excluded"] = excluded
            c["live_message_id"] = None
            storage.set(gid, "contest", c)
        elif action == "stop" and c.get("status") in ("scheduled", "running"):
            c["status"] = "ended"
            c["end_ts"] = _time.time()
            storage.set(gid, "contest", c)
        return redirect(url_for("contest", guild_id=guild_id))

    c = storage.get(gid, "contest", {}) or {}
    standings = []
    if c.get("status") in ("running", "ended"):
        inv = storage.get(gid, "invites", {})
        since = c.get("start_ts", 0)
        counts = {}
        for e in inv.get("history", []):
            if e.get("ts", 0) < since or e.get("fake") or e.get("left"):
                continue
            iv = e.get("inviter")
            if not iv or iv in ("vanity", "unknown"):
                continue
            counts[iv] = counts.get(iv, 0) + 1
        ranked = sorted(((u, n) for u, n in counts.items() if n > 0),
                        key=lambda x: x[1], reverse=True)[:10]
        standings = [(resolve_name(gid, u) or f"User {u}", n) for u, n in ranked]

    # timp ramas / pana la start, pentru afisare
    remaining = None
    if c.get("status") == "running" and c.get("end_ts"):
        remaining = max(0, c["end_ts"] - _time.time())
    elif c.get("status") == "scheduled":
        remaining = max(0, c.get("start_ts", 0) - _time.time())

    excluded_named = [{"id": x, "name": resolve_name(gid, x)} for x in c.get("excluded", [])]
    return render_template("contest.html", guild_id=guild_id, contest=c,
                           standings=standings, remaining=remaining,
                           excluded=excluded_named,
                           channels=(storage.get(gid, "channels", {}) or {}).get("texts", []),
                           meta=storage.get(gid, "meta", {}), section="contest")


@app.route("/permissions/<guild_id>", methods=["GET", "POST"])
@guild_required
def permissions(guild_id):
    gid = int(guild_id)
    if request.method == "POST":
        chosen = request.form.getlist("roles")  # lista de ID-uri bifate
        storage.set(gid, "permissions", {"roles": chosen})
        return redirect(url_for("permissions", guild_id=guild_id, saved=1))

    roles = storage.get(gid, "roles", {}) or {}
    cfg = storage.get(gid, "permissions", {}) or {}
    return render_template("permissions.html", guild_id=guild_id,
                           roles=roles.get("list", []), selected=cfg.get("roles", []),
                           meta=storage.get(gid, "meta", {}),
                           section="permissions", saved=request.args.get("saved"))


@app.route("/massdm/<guild_id>", methods=["GET", "POST"])
@guild_required
def massdm(guild_id):
    gid = int(guild_id)
    cur = storage.get(gid, "massdm", {}).get("campaign", {}) or {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save":
            cur.update(
                message=request.form.get("message", ""),
                footer=request.form.get("footer", ""),
                role_id=_to_int(request.form.get("role_id", "")) or None,
                delay_seconds=max(10, _to_int(request.form.get("delay_seconds", "60")) or 60),
                daily_limit=max(1, _to_int(request.form.get("daily_limit", "50")) or 50),
            )
            cur.setdefault("status", "stopped")
            storage.set(gid, "massdm", {"campaign": cur})
        elif action == "start" and cur.get("status") != "running" and cur.get("message"):
            # botul construieste coada de destinatari la primul tick (are membrii
            # in memorie) - asa nu stocam toata lista in fisier
            cur.update(status="running", sent=0, failed=0, sent_today=0,
                       day=_time.strftime("%Y-%m-%d"), last_ts=0,
                       build_queue=True, queue=[], total=0)
            storage.set(gid, "massdm", {"campaign": cur})
        elif action == "stop":
            cur["status"] = "stopped"
            storage.set(gid, "massdm", {"campaign": cur})
        return redirect(url_for("massdm", guild_id=guild_id))

    roles = storage.get(gid, "roles", {}) or {}
    return render_template("massdm.html", guild_id=guild_id, camp=cur,
                           roles=roles.get("list", []),
                           meta=storage.get(gid, "meta", {}), section="massdm")


@app.route("/tickets/<guild_id>", methods=["GET", "POST"])
@guild_required
def tickets(guild_id):
    gid = int(guild_id)
    data = storage.get(gid, "tickets", {}) or {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "panel":
            data["panel"] = {
                "title": request.form.get("title", ""),
                "description": request.form.get("description", ""),
                "color": request.form.get("color", "#5865f2"),
                "image": request.form.get("image", ""),
                "thumbnail": request.form.get("thumbnail", ""),
            }
            data["log_channel_id"] = _to_int(request.form.get("log_channel_id", ""))
            storage.set(gid, "tickets", data)
        elif action == "add_type":
            data.setdefault("types", []).append({
                "id": uuid.uuid4().hex[:8],
                "label": request.form.get("label", "").strip() or "Ticket",
                "emoji": request.form.get("emoji", "").strip(),
                "button_color": request.form.get("button_color", "blurple"),
                "support_roles": request.form.getlist("support_roles"),
                "category_id": _to_int(request.form.get("category_id", "")),
                "open_message": request.form.get("open_message", ""),
                "ping_support": request.form.get("ping_support") == "on",
                "one_per_user": request.form.get("one_per_user") == "on",
                "btn_close": request.form.get("btn_close") == "on",
                "btn_close_reason": request.form.get("btn_close_reason") == "on",
                "btn_claim": request.form.get("btn_claim") == "on",
            })
            storage.set(gid, "tickets", data)
        elif action == "del_type":
            tid = request.form.get("type_id")
            data["types"] = [t for t in data.get("types", []) if t.get("id") != tid]
            storage.set(gid, "tickets", data)
        elif action == "edit_type":
            tid = request.form.get("type_id")
            for t in data.get("types", []):
                if t.get("id") == tid:
                    t["label"] = request.form.get("label", "").strip() or t.get("label", "Ticket")
                    t["emoji"] = request.form.get("emoji", "").strip()
                    t["button_color"] = request.form.get("button_color", "blurple")
                    t["support_roles"] = request.form.getlist("support_roles")
                    t["category_id"] = _to_int(request.form.get("category_id", ""))
                    t["open_message"] = request.form.get("open_message", "")
                    t["ping_support"] = request.form.get("ping_support") == "on"
                    t["one_per_user"] = request.form.get("one_per_user") == "on"
                    t["btn_close"] = request.form.get("btn_close") == "on"
                    t["btn_close_reason"] = request.form.get("btn_close_reason") == "on"
                    t["btn_claim"] = request.form.get("btn_claim") == "on"
                    break
            # cerem botului sa reaplice permisiunile pe ticketele DESCHISE de acest tip,
            # ca rolurile de suport nou adaugate sa vada si ticketele existente
            data["perm_sync"] = {"status": "pending"}
            storage.set(gid, "tickets", data)
        return redirect(url_for("tickets", guild_id=guild_id, saved=1))

    edit_id = request.args.get("edit", "")
    editing = next((t for t in data.get("types", []) if t.get("id") == edit_id), None)
    roles = storage.get(gid, "roles", {}) or {}
    channels = storage.get(gid, "channels", {}) or {}
    return render_template("tickets.html", guild_id=guild_id, data=data,
                           panel=data.get("panel", {}), types=data.get("types", []),
                           editing=editing,
                           roles=roles.get("list", []),
                           categories=channels.get("categories", []),
                           text_channels=channels.get("texts", []),
                           meta=storage.get(gid, "meta", {}),
                           section="tickets", saved=request.args.get("saved"))


@app.route("/backups/<guild_id>", methods=["GET", "POST"])
@guild_required
def backups(guild_id):
    gid = int(guild_id)
    index = storage.get(0, "backup_index", {}) or {}
    # doar backup-urile create de acest user (sau de pe acest server)
    mine = {bid: m for bid, m in index.items()
            if m and (m.get("owner_id") == session["user"]["id"] or m.get("source_guild_id") == str(gid))}

    if request.method == "POST":
        action = request.form.get("action")
        bid = request.form.get("backup_id")
        if action == "delete" and bid in index:
            index.pop(bid, None)
            storage.set(0, "backup_index", index)
            storage.set(0, f"backup:{bid}", None)
        elif action == "apply" and bid:
            # marcam o cerere de aplicare pe care botul o executa
            storage.set(gid, "backup_apply", {
                "backup_id": bid, "status": "pending",
                "confirm": request.form.get("confirm", ""),
                "requested_ts": _time.time(), "result": ""})
        return redirect(url_for("backups", guild_id=guild_id))

    preview = None
    pid = request.args.get("preview")
    if pid:
        preview = storage.get(0, f"backup:{pid}", None)

    apply_status = storage.get(gid, "backup_apply", None)
    return render_template("backups.html", guild_id=guild_id,
                           backups=sorted(mine.values(), key=lambda m: -m.get("created_ts", 0)),
                           preview=preview, apply_status=apply_status,
                           meta=storage.get(gid, "meta", {}), section="backups")


@app.route("/invitelog/<guild_id>", methods=["GET", "POST"])
@guild_required
def invitelog(guild_id):
    gid = int(guild_id)

    data = storage.get(gid, "invites", {}) or {}
    history = data.get("history", [])
    joined_by = data.get("joined_by", {})
    members = data.get("members", {})
    meta = storage.get(gid, "meta", {})

    def _total(key):
        st = members.get(str(key))
        if not st:
            return None
        return max(0, st.get("regular", 0) + st.get("bonus", 0)
                   - st.get("left", 0) - st.get("fake", 0))

    rows = []
    for h in reversed(history):  # cele mai noi primele
        mid = h.get("member")
        inviter = h.get("inviter")
        rows.append({
            "member_name": h.get("member_name") or f"ID {mid}",
            "member_id": mid,
            "inviter": inviter,
            "inviter_name": h.get("inviter_name"),
            "inviter_total": _total(inviter) if inviter not in ("vanity", "unknown") else None,
            "code": h.get("code"),
            "ts": h.get("ts"),
            "fake": h.get("fake", False),
            "left": h.get("left", False),
        })
    return render_template("invitelog.html", guild_id=guild_id, rows=rows,
                           total=len(history), meta=meta,
                           section="invitelog")


@app.route("/test/<guild_id>")
@guild_required
def test_page(guild_id):
    import subprocess
    root = os.path.join(os.path.dirname(__file__), "..")

    def _git(args, timeout=8):
        try:
            return subprocess.check_output(
                ["git", "-C", root] + args,
                stderr=subprocess.DEVNULL, timeout=timeout).decode().strip()
        except Exception:
            return None

    version = _git(["log", "-1", "--pretty=%s"])
    commit = _git(["rev-parse", "--short", "HEAD"])
    commit_date = _git(["log", "-1", "--pretty=%cd", "--date=format:%d.%m.%Y %H:%M"])
    local_full = _git(["rev-parse", "HEAD"])

    # verifica pe GitHub daca e ceva nou (fara sa traga nimic)
    update_available = None
    remote_msg = None
    if request.args.get("check"):
        remote_line = _git(["ls-remote", "origin", "refs/heads/main"], timeout=12)
        if remote_line and local_full:
            remote_hash = remote_line.split()[0]
            update_available = (remote_hash != local_full)

    return render_template("test.html", guild_id=guild_id,
                           meta=storage.get(int(guild_id), "meta", {}),
                           version=version, commit=commit, commit_date=commit_date,
                           update_available=update_available,
                           checked=bool(request.args.get("check")),
                           section="test")


@app.route("/appearance/<guild_id>", methods=["GET", "POST"])
@guild_required
def appearance(guild_id):
    gid = int(guild_id)
    if request.method == "POST":
        theme = {
            "accent": request.form.get("accent", "#8b5cf6"),
            "accent2": request.form.get("accent2", "#22d3ee"),
            "bg": request.form.get("bg", "#07070c"),
            "text": request.form.get("text", "#ecedff"),
            "no_anim": request.form.get("no_anim") == "on",
            "glass": request.form.get("glass", "16"),
        }
        storage.set(gid, "theme", theme)
        return redirect(url_for("appearance", guild_id=guild_id, saved=1))

    theme = storage.get(gid, "theme", {})
    meta = storage.get(gid, "meta", {})
    return render_template("appearance.html", guild_id=guild_id, theme=theme,
                           meta=meta, section="appearance",
                           saved=request.args.get("saved"))


@app.context_processor
def inject_theme():
    """Face tema disponibila automat in toate paginile (pentru base.html)."""
    gid = None
    if request.view_args:
        gid = request.view_args.get("guild_id")
    theme = {}
    if gid and str(gid).isdigit():
        theme = storage.get(int(gid), "theme", {}) or {}
    # esti owner-ul botului? (pentru a arata/ascunde actiunile globale in UI)
    owner_id = storage.get(0, "bot_owner_id", None)
    is_owner = bool(owner_id and "user" in session
                    and str(session["user"]["id"]) == str(owner_id))
    return {"theme": theme, "is_owner": is_owner}


@app.route("/metin2/<guild_id>", methods=["GET", "POST"])
@guild_required
def metin2(guild_id):
    gid = int(guild_id)
    if request.method == "POST":
        action = request.form.get("action", "settings")
        cfg = storage.get(gid, "metin2", {}) or {}
        cfg.setdefault("cat_map", [])
        if action == "settings":
            cfg["enabled"] = request.form.get("enabled") == "on"
            cfg["api_base"] = request.form.get("api_base", "").strip()
            cfg["api_token"] = request.form.get("api_token", "").strip()
            cfg["category_id"] = _to_int(request.form.get("category_id", "")) or None
            cfg["staff_role_ids"] = [i for i in (_to_int(x) for x in request.form.getlist("staff_role_ids")) if i]
            cfg.pop("staff_role_id", None)  # formatul vechi (un rol) nu mai e necesar
            ps = _to_int(request.form.get("poll_seconds", "")) or 10
            cfg["poll_seconds"] = max(5, ps)
        elif action == "add_map":
            gc = request.form.get("game_category", "").strip()[:100]
            if gc and len(cfg["cat_map"]) < 25:
                # nu dublam aceeasi categorie
                exists = any((m.get("game_category") or "").lower() == gc.lower()
                             for m in cfg["cat_map"])
                if not exists:
                    cfg["cat_map"].append({
                        "game_category": gc,
                        "category_id": _to_int(request.form.get("category_id", "")) or None,
                        "staff_role_ids": [i for i in (_to_int(x) for x in request.form.getlist("staff_role_ids")) if i],
                    })
        elif action == "del_map":
            idx = _to_int(request.form.get("idx", ""))
            if idx is not None and 0 <= idx < len(cfg["cat_map"]):
                cfg["cat_map"].pop(idx)
        elif action == "load_categories":
            # incarcam categoriile direct din API-ul jocului (GET /categories)
            base = (cfg.get("api_base") or "").rstrip("/")
            token = cfg.get("api_token") or ""
            fetched = None
            if base and token:
                try:
                    import requests as _rq
                    r = _rq.get(base + "/categories",
                                headers={"Authorization": f"Bearer {token}"}, timeout=8)
                    if r.status_code == 200:
                        fetched = r.json().get("categories")
                except Exception:
                    fetched = None
            if isinstance(fetched, list) and fetched:
                cfg["game_categories"] = [str(x)[:100] for x in fetched][:50]
                storage.set(gid, "metin2", cfg)
                return redirect(url_for("metin2", guild_id=guild_id, cats="ok"))
            storage.set(gid, "metin2", cfg)
            return redirect(url_for("metin2", guild_id=guild_id, cats="err"))
        storage.set(gid, "metin2", cfg)
        return redirect(url_for("metin2", guild_id=guild_id, saved=1))

    roles = storage.get(gid, "roles", {}) or {}
    channels = storage.get(gid, "channels", {}) or {}
    cfg = storage.get(gid, "metin2", {}) or {}
    # nume categorii/roluri pentru afisare in lista de mapari
    cat_name = {str(c["id"]): c["name"] for c in channels.get("categories", [])}
    role_name = {str(r["id"]): r["name"] for r in roles.get("list", [])}
    return render_template("metin2.html", guild_id=guild_id, cfg=cfg,
                           cat_map=cfg.get("cat_map", []),
                           game_categories=cfg.get("game_categories", []),
                           cat_name=cat_name, role_name=role_name,
                           roles=[r for r in roles.get("list", []) if not r.get("default")],
                           categories=channels.get("categories", []),
                           meta=storage.get(gid, "meta", {}),
                           section="metin2", saved=request.args.get("saved"),
                           cats=request.args.get("cats"))


@app.route("/kingdoms/<guild_id>", methods=["GET", "POST"])
@guild_required
def kingdoms(guild_id):
    gid = int(guild_id)
    if request.method == "POST":
        action = request.form.get("action")
        cfg = storage.get(gid, "kingdoms", {}) or {}
        cfg.setdefault("options", [])
        if action == "settings":
            cfg["title"] = request.form.get("title", "").strip() or "Alege regatul"
            cfg["description"] = request.form.get("description", "").strip()
        elif action == "add_option":
            if len(cfg["options"]) < 5:
                rid = _to_int(request.form.get("role_id", ""))
                if rid:
                    cfg["options"].append({
                        "label": request.form.get("label", "").strip() or "Regat",
                        "emoji": request.form.get("emoji", "").strip(),
                        "style": request.form.get("style", "grey"),
                        "role_id": rid,
                    })
        elif action == "del_option":
            idx = _to_int(request.form.get("idx", ""))
            if idx is not None and 0 <= idx < len(cfg["options"]):
                cfg["options"].pop(idx)
        storage.set(gid, "kingdoms", cfg)
        return redirect(url_for("kingdoms", guild_id=guild_id, saved=1))

    roles = storage.get(gid, "roles", {}) or {}
    cfg = storage.get(gid, "kingdoms", {}) or {}
    # atasam numele rolului la fiecare optiune (pentru afisare)
    rname = {str(r["id"]): r["name"] for r in roles.get("list", [])}
    return render_template("kingdoms.html", guild_id=guild_id, cfg=cfg,
                           options=cfg.get("options", []), rname=rname,
                           roles=[r for r in roles.get("list", []) if not r.get("default")],
                           meta=storage.get(gid, "meta", {}),
                           section="kingdoms", saved=request.args.get("saved"))


@app.route("/comenzi/<guild_id>")
@guild_required
def commands_list(guild_id):
    gid = int(guild_id)
    # lista comenzilor, grupate pe categorii (descrieri scurte, prietenoase)
    groups = [
        ("👋 Bun venit & plecări", [
            ("/welcome channel", "Setează canalul de bun venit"),
            ("/welcome test", "Trimite un mesaj de test de bun venit"),
        ]),
        ("📨 Invitații & concurs", [
            ("/invites", "Vezi câte invitații ai (tu sau alt membru)"),
            ("/inviter", "Cine a invitat un membru"),
            ("/invitedlist", "Lista celor invitați de cineva"),
            ("/invitecodes", "Codurile de invitație ale cuiva"),
            ("/findlink", "Unul dintre linkurile tale de invitație"),
            ("/leaderboard", "Clasamentul invitatorilor"),
            ("/concurs start", "Pornește un concurs de invitații"),
            ("/concurs stop", "Oprește concursul și anunță câștigătorul"),
            ("/concurs status", "Vezi starea concursului"),
            ("/concurs clasament", "Clasamentul concursului curent"),
            ("/addinvites", "Adaugă invitații bonus unui membru (admin)"),
            ("/removeinvites", "Scade invitații bonus unui membru (admin)"),
            ("/resetinvites", "Resetează invitațiile (admin)"),
            ("/recalcinvite", "Recalculează invitațiile după cine e pe server (admin)"),
            ("/inviteaudit", "Detaliu pe cine a adus cineva, cu status (admin)"),
        ]),
        ("🎨 Culori", [
            ("/culori panou", "Postează panoul cu butoane de culoare (admin)"),
        ]),
        ("🎫 Tickete", [
            ("/ticket_panel", "Postează panoul de tickete în canal (admin)"),
            ("/add", "Adaugă un membru/rol în ticketul curent"),
            ("/remove", "Scoate un membru/rol din ticketul curent"),
        ]),
        ("🎁 Giveaway", [
            ("/giveaway", "Deschide formularul și pornește un giveaway"),
            ("/giveaway_start", "Postează un giveaway configurat în dashboard"),
            ("/giveaway_end", "Încheie acum un giveaway"),
            ("/giveaway_reroll", "Alege alt câștigător"),
        ]),
        ("🔔 Notificări", [
            ("/notify list", "Creatorii urmăriți (YouTube/TikTok)"),
            ("/notify test", "Trimite o notificare de test"),
        ]),
        ("🆕 Conturi noi", [
            ("/conturinoi lista", "Membrii cu cont creat sub pragul setat (admin)"),
            ("/conturinoi verifica", "Verifică vechimea contului unui membru (admin)"),
        ]),
        ("🧹 Curățare mesaje", [
            ("/clean", "Șterge mesajele unui membru de pe un canal (admin)"),
            ("/clean_all", "Șterge mesajele unui membru de pe tot serverul (admin)"),
            ("/autodelete", "Șterge automat tot ce scrie cineva (admin)"),
        ]),
        ("🎭 Roluri în masă", [
            ("/massrole give_all", "Dă un rol tuturor membrilor (admin)"),
            ("/massrole give_to", "Dă un rol celor care au deja un rol (admin)"),
            ("/massrole remove_all", "Scoate un rol de la toți (admin)"),
            ("/massrole remove_from", "Scoate un rol de la cei cu un rol (admin)"),
        ]),
        ("🏆 Ranguri", [
            ("/rankup run", "Aplică rangurile pe tot serverul (admin)"),
            ("/rankup status", "Vezi configurația rangurilor (admin)"),
        ]),
        ("💬 Embed-uri & DM", [
            ("/embed send", "Postează un embed salvat (admin)"),
            ("/embed preview", "Vezi un embed fără să-l postezi (admin)"),
            ("/embed list", "Lista embed-urilor salvate (admin)"),
            ("/embed delete", "Șterge un embed salvat (admin)"),
            ("/dm_masa", "Pornește o campanie de DM (admin)"),
            ("/dm_stop", "Oprește campania de DM (admin)"),
        ]),
        ("🖼️ Avatar & imagini", [
            ("/avatar", "Arată avatarul unui user"),
            ("/banner", "Arată bannerul unui user"),
            ("/serveravatar", "Arată iconița serverului"),
            ("/serverbanner", "Arată bannerul serverului"),
        ]),
        ("🎮 Distracție", [
            ("/rps", "Piatră / Foarfece / Hârtie"),
            ("/randome", "Pregătește o rundă nouă de joc"),
            ("/alege", "Alege un număr (privat)"),
        ]),
        ("💾 Backup", [
            ("/backup", "Salvează structura serverului ca backup (admin)"),
        ]),
        ("👑 Owner bot", [
            ("/serverlist", "Lista serverelor pe care e botul (owner)"),
            ("/leaveserver", "Scoate botul de pe un server (owner)"),
        ]),
    ]
    total = sum(len(cmds) for _, cmds in groups)
    return render_template("commands.html", guild_id=guild_id, groups=groups,
                           total=total, meta=storage.get(gid, "meta", {}),
                           section="comenzi")


@app.route("/restart", methods=["POST"])
def restart_bot():
    if "user" not in session:
        return redirect(url_for("index"))
    # DOAR owner-ul botului poate reporni. Fail-safe: daca nu stim sigur cine e
    # owner (nesetat) SAU nu esti tu -> refuzam.
    owner_id = storage.get(0, "bot_owner_id", None)
    if not owner_id or str(session["user"]["id"]) != str(owner_id):
        return "Doar owner-ul botului poate reporni.", 403

    # botul si dashboardul ruleaza in acelasi proces; ne inchidem singuri,
    # iar systemd (Restart=always) porneste botul la loc in cateva secunde cu codul nou.
    import threading, time, os

    def _bye():
        time.sleep(2)  # lasam raspunsul HTTP sa ajunga la utilizator inainte sa iesim
        os._exit(0)

    threading.Thread(target=_bye, daemon=True).start()
    return render_template("restart.html", section=None)


@app.route("/servers", methods=["GET", "POST"])
def servers():
    if "user" not in session:
        return redirect(url_for("index"))

    # DOAR owner-ul botului poate vedea/folosi aceasta pagina. Fail-safe: daca nu
    # stim sigur cine e owner SAU nu esti tu -> blocat.
    owner_id = storage.get(0, "bot_owner_id", None)
    if not owner_id or str(session["user"]["id"]) != str(owner_id):
        return render_template("servers.html", servers=[], leave_status=None,
                               section="servers", denied=True)

    if request.method == "POST":
        gid = request.form.get("guild_id", "")
        if request.form.get("action") == "leave" and gid.isdigit():
            storage.set(0, "leave_request", {
                "guild_id": gid, "status": "pending", "result": ""})
        return redirect(url_for("servers"))

    bot_servers = storage.get(0, "bot_servers", []) or []
    my_ids = {str(g["id"]) for g in session.get("guilds", [])}
    for s in bot_servers:
        s["i_manage"] = str(s["id"]) in my_ids
    leave_status = storage.get(0, "leave_request", None)
    return render_template("servers.html", servers=bot_servers,
                           leave_status=leave_status, section="servers", denied=False)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
