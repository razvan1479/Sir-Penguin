"""
cogs/notifications.py — NOTIFICARI pentru continut nou (stil NotifyMe).

Urmareste creatori pe YouTube / Twitch / Kick / TikTok si anunta pe Discord
cand apare un videoclip nou sau cand intra LIVE.

Configurezi totul din dashboard (platforma, URL canal, canal Discord, mesaj, rol).

CUM functioneaza fiecare platforma (important):
  - YouTube : RSS-ul canalului. Merge fara cheie API.
  - Twitch  : API oficial (Helix). NECESITA TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET in .env
              (gratuit de pe https://dev.twitch.tv/console).
  - Kick    : API neoficial (kick.com/api). Merge, dar se poate strica daca il schimba ei.
  - TikTok  : NU are API public -> detectare "best-effort", NESIGURA. Pentru fiabilitate
              real ai nevoie de un serviciu third-party platit.

Verificarea se face periodic (la 5 minute), deci notificarile NU sunt instant.

Date salvate (cheia "notifications"):
{ "subscriptions": [ {id, platform, url, discord_channel_id, message, role_id,
                      identifier, initialized, last_video_id, was_live} ] }

Comenzi (admin):
  /notify list            - lista creatorilor urmariti
  /notify test <id>       - trimite o notificare de test
"""

import os
import re
import json
import time
import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage
from utils.perms import bot_access

log = logging.getLogger("bot")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

PLATFORMS = ("youtube", "tiktok")
LIVE_PLATFORMS = ()  # doar YouTube (video) si TikTok (video/live, tratat separat)


def _matches_keywords(text: str, keywords) -> bool:
    """True daca textul contine cel putin un cuvant cheie. Fara cuvinte cheie -> mereu True."""
    if not keywords:
        return True
    if not text:
        return False
    low = text.lower()
    return any(k.lower() in low for k in keywords if k.strip())


def parse_target(platform, url):
    """Extrage identificatorul (handle/username/channel_id) din URL-ul creatorului."""
    url = (url or "").strip()
    if platform == "youtube":
        m = re.search(r"youtube\.com/channel/(UC[\w-]{20,})", url)
        if m:
            return ("id", m.group(1))
        m = re.search(r"youtube\.com/@([\w.\-]+)", url)
        if m:
            return ("handle", m.group(1))
        m = re.search(r"youtube\.com/(?:c|user)/([\w.\-]+)", url)
        if m:
            return ("name", m.group(1))
        return (None, url)
    m = re.search(r"(?:twitch\.tv|kick\.com|tiktok\.com)/@?([\w.\-]+)", url)
    if m:
        return ("user", m.group(1))
    return (None, url)


class Notifications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self._tw_token = None
        self._tw_token_exp = 0
        self._providers = {
            "youtube": self.check_youtube,
            "tiktok": self.check_tiktok,
        }

    async def cog_load(self):
        self.session = aiohttp.ClientSession(headers=UA)
        self.poller.start()

    async def cog_unload(self):
        self.poller.cancel()
        if self.session:
            await self.session.close()

    # =============================================================== PROVIDERI
    async def _yt_channel_id(self, sub):
        if sub.get("identifier"):
            return sub["identifier"]
        kind, val = parse_target("youtube", sub["url"])
        cid = None
        if kind == "id":
            cid = val
        else:
            try:
                async with self.session.get(sub["url"]) as r:
                    html = await r.text()
                m = (re.search(r'"channelId":"(UC[\w-]+)"', html)
                     or re.search(r'"externalId":"(UC[\w-]+)"', html)
                     or re.search(r'channel/(UC[\w-]+)', html))
                cid = m.group(1) if m else None
            except aiohttp.ClientError:
                cid = None
        if cid:
            sub["identifier"] = cid
        return cid

    async def check_youtube(self, sub):
        import feedparser
        cid = await self._yt_channel_id(sub)
        if not cid:
            return None
        try:
            async with self.session.get(
                    f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}") as r:
                if r.status != 200:
                    return None
                text = await r.text()
        except aiohttp.ClientError:
            return None
        feed = feedparser.parse(text)
        if not feed.entries:
            return None
        e = feed.entries[0]
        vid = getattr(e, "yt_videoid", None) or getattr(e, "id", "")
        title = getattr(e, "title", "Videoclip nou")
        desc = getattr(e, "summary", "") or getattr(e, "media_description", "") or ""
        return {
            "id": vid,
            "title": title,
            "desc": desc,
            "url": getattr(e, "link", ""),
            "author": feed.feed.get("title", "Creator"),
            "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None,
        }

    async def _twitch_token(self):
        cid, cs = os.getenv("TWITCH_CLIENT_ID"), os.getenv("TWITCH_CLIENT_SECRET")
        if not cid or not cs:
            return None
        if self._tw_token and self._tw_token_exp > time.time():
            return self._tw_token
        try:
            async with self.session.post("https://id.twitch.tv/oauth2/token", params={
                    "client_id": cid, "client_secret": cs,
                    "grant_type": "client_credentials"}) as r:
                d = await r.json()
            self._tw_token = d["access_token"]
            self._tw_token_exp = time.time() + d.get("expires_in", 3600) - 60
            return self._tw_token
        except (aiohttp.ClientError, KeyError):
            return None

    async def check_twitch(self, sub):
        token = await self._twitch_token()
        if not token:
            return None  # lipsesc credentialele Twitch in .env
        kind, user = parse_target("twitch", sub["url"])
        headers = {"Client-Id": os.getenv("TWITCH_CLIENT_ID"),
                   "Authorization": f"Bearer {token}"}
        try:
            async with self.session.get("https://api.twitch.tv/helix/streams",
                                        params={"user_login": user}, headers=headers) as r:
                d = await r.json()
        except aiohttp.ClientError:
            return None
        if d.get("data"):
            s = d["data"][0]
            thumb = (s.get("thumbnail_url", "")
                     .replace("{width}", "640").replace("{height}", "360"))
            return {"is_live": True, "title": s.get("title", "Live"),
                    "url": f"https://twitch.tv/{user}",
                    "author": s.get("user_name", user), "thumb": thumb or None}
        return {"is_live": False}

    async def check_kick(self, sub):
        kind, user = parse_target("kick", sub["url"])
        try:
            async with self.session.get(f"https://kick.com/api/v2/channels/{user}") as r:
                if r.status != 200:
                    return None
                d = await r.json()
        except (aiohttp.ClientError, aiohttp.ContentTypeError):
            return None
        ls = d.get("livestream")
        if ls:
            thumb = (ls.get("thumbnail") or {}).get("url")
            return {"is_live": True, "title": ls.get("session_title", "Live"),
                    "url": f"https://kick.com/{user}", "author": user, "thumb": thumb}
        return {"is_live": False}

    async def check_tiktok(self, sub):
        # TikTok nu are API public. Folosim mai multe metode cu rezerva:
        #  1) tikwm.com  (API tert, fiabil, da descriere + thumbnail)  <- principala
        #  2) pagina TikTok (JSON structurat, apoi regex) <- rezerva + prinde LIVE
        kind, user = parse_target("tiktok", sub["url"])
        if not user:
            return None
        info = await self._tiktok_tikwm(user)
        page = await self._tiktok_page(user)
        if info is None and page is None:
            return None
        if info is None:
            return page
        # avem video fiabil de la tikwm; luam statusul LIVE din pagina (tikwm nu-l da)
        if page:
            info["is_live"] = page.get("is_live", False)
            if page.get("live_desc"):
                info["live_desc"] = page.get("live_desc")
        return info

    async def _tiktok_tikwm(self, user):
        """Metoda 1 (principala): API-ul tikwm.com — fiabil, da descriere + thumbnail."""
        try:
            async with self.session.get(
                    "https://www.tikwm.com/api/user/posts",
                    params={"unique_id": user, "count": "1"},
                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                d = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None
        if not isinstance(d, dict) or d.get("code") != 0:
            return None
        vids = ((d.get("data") or {}).get("videos")) or []
        if not vids:
            return None
        v = vids[0]
        vid = str(v.get("video_id") or v.get("id") or "")
        if not vid:
            return None
        author = v.get("author") or {}
        return {"id": vid, "title": "Videoclip nou pe TikTok",
                "desc": v.get("title") or "",
                "live_desc": "",
                "url": f"https://www.tiktok.com/@{user}/video/{vid}",
                "author": author.get("nickname") or ("@" + user),
                "thumb": v.get("cover") or v.get("origin_cover"),
                "is_live": False,
                "live_title": "🔴 Live pe TikTok",
                "live_url": f"https://www.tiktok.com/@{user}/live"}

    async def _tiktok_page(self, user):
        """Metoda 2 (rezerva): pagina TikTok — JSON structurat, apoi regex. Da si LIVE."""
        try:
            async with self.session.get(
                    f"https://www.tiktok.com/@{user}",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                                           "Chrome/120.0 Safari/537.36"},
                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                html = await r.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        vid, desc, is_live, live_txt, nick = None, "", False, "", "@" + user
        # JSON structurat din pagina (mai robust decat regex)
        m = re.search(r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                      html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                scope = data.get("__DEFAULT_SCOPE__", {})
                ud = (scope.get("webapp.user-detail", {}) or {}).get("userInfo", {})
                if ud:
                    nick = (ud.get("user") or {}).get("nickname") or nick
                    post = ud.get("post") or {}
                    items = post.get("data") or post.get("itemList") or []
                    if items:
                        vid = str(items[0].get("id") or "") or None
                        desc = items[0].get("desc") or ""
                live = scope.get("webapp.live-detail") or scope.get("liveRoom")
                if live:
                    is_live = True
                    if isinstance(live, dict):
                        live_txt = live.get("title") or live.get("description") or ""
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

        # rezerva pe regex daca JSON n-a mers
        if not vid:
            mm = re.search(r'/video/(\d+)', html)
            if mm:
                vid = mm.group(1)
        if not desc:
            dm = re.search(r'"desc":"([^"]*)"', html)
            if dm:
                desc = dm.group(1)
        if not is_live:
            rm = re.search(r'"roomId":"(\d+)"', html)
            if (rm and rm.group(1) not in ("0", "")) or '"isLive":true' in html.replace(" ", ""):
                is_live = True

        if not vid and not is_live:
            return None
        return {"id": vid, "title": "Videoclip nou pe TikTok", "desc": desc,
                "live_desc": live_txt,
                "url": (f"https://www.tiktok.com/@{user}/video/{vid}" if vid
                        else f"https://www.tiktok.com/@{user}"),
                "author": nick, "thumb": None,
                "is_live": is_live,
                "live_title": "🔴 Live pe TikTok",
                "live_url": f"https://www.tiktok.com/@{user}/live"}

    # =============================================================== POLLER
    @tasks.loop(minutes=5)
    async def poller(self):
        for guild in self.bot.guilds:
            data = storage.get(guild.id, "notifications", {})
            subs = data.get("subscriptions", [])
            if not subs:
                continue
            for sub in subs:
                try:
                    await self._process(guild, sub)
                except Exception as e:
                    log.warning("Notificari: eroare la %s (%s): %s",
                                sub.get("platform"), sub.get("url"), e)
            data["subscriptions"] = subs
            storage.set(guild.id, "notifications", data)

    @poller.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    async def _process(self, guild, sub):
        provider = self._providers.get(sub["platform"])
        if not provider:
            return
        info = await provider(sub)
        if info is None:
            return

        kws = sub.get("keywords") or []

        if sub["platform"] == "tiktok":
            # TikTok: respectam ce a ales userul (live / video / ambele)
            mode = sub.get("tiktok_mode", "both")
            if not sub.get("initialized"):
                sub["was_live"] = info.get("is_live", False)
                sub["last_video_id"] = info.get("id")
                sub["initialized"] = True
                return
            # tranzitie live (doar la trecerea din offline in live)
            live = info.get("is_live", False)
            if mode in ("both", "live") and live and not sub.get("was_live"):
                if _matches_keywords(info.get("live_desc", ""), kws):
                    live_info = {"author": info.get("author"),
                                 "title": info.get("live_title", "🔴 Live pe TikTok"),
                                 "url": info.get("live_url", info.get("url")),
                                 "thumb": info.get("thumb")}
                    await self._notify(guild, sub, live_info, is_live=True)
            sub["was_live"] = live
            # video nou
            vid = info.get("id")
            if mode in ("both", "video") and vid and vid != sub.get("last_video_id"):
                sub["last_video_id"] = vid
                if _matches_keywords(info.get("desc", ""), kws):
                    await self._notify(guild, sub, info, is_live=False)
            elif vid:
                sub["last_video_id"] = vid  # tinem evidenta chiar daca nu anuntam
            return

        # video nou (youtube)
        vid = info.get("id")
        if not vid:
            return
        if not sub.get("initialized"):
            sub["last_video_id"] = vid
            sub["initialized"] = True
            return
        if vid != sub.get("last_video_id"):
            sub["last_video_id"] = vid
            text = f"{info.get('title','')} {info.get('desc','')}"
            if _matches_keywords(text, kws):
                await self._notify(guild, sub, info, is_live=False)

    async def _notify(self, guild, sub, info, is_live):
        channel = guild.get_channel(int(sub["discord_channel_id"]))
        if channel is None:
            return
        creator = info.get("author", "Creator")
        title = info.get("title", "")
        url = info.get("url", "")

        default = ("🔴 {creator} este LIVE acum!\n{url}" if is_live
                   else "📺 {creator} a postat ceva nou!\n{url}")
        template = sub.get("message") or default
        text = (template.replace("{creator}", creator)
                        .replace("{title}", title or "")
                        .replace("{url}", url))

        role = sub.get("role_id")
        content = (f"<@&{role}> " if role else "") + text

        embed = discord.Embed(
            title=title or creator, url=url or None,
            description="🔴 **LIVE acum!**" if is_live else None,
            color=discord.Color.red() if is_live else discord.Color.blurple(),
        )
        embed.set_author(name=creator)
        if info.get("thumb"):
            embed.set_image(url=info["thumb"])

        await channel.send(content=content, embed=embed,
                           allowed_mentions=discord.AllowedMentions(roles=True))

    # =============================================================== COMENZI
    group = app_commands.Group(name="notify", description="Notificari de continut nou")

    @group.command(name="list", description="Creatorii urmariti pe acest server")
    async def list_subs(self, interaction: discord.Interaction):
        subs = storage.get(interaction.guild_id, "notifications", {}).get("subscriptions", [])
        if not subs:
            return await interaction.response.send_message(
                "Niciun creator urmarit. Adauga din dashboard.", ephemeral=True)
        lines = [f"• `{s['id']}` — **{s['platform']}** · {s['url']} → <#{s['discord_channel_id']}>"
                 for s in subs]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="test", description="Trimite o notificare de test")
    @bot_access()
    async def test(self, interaction: discord.Interaction, id: str):
        subs = storage.get(interaction.guild_id, "notifications", {}).get("subscriptions", [])
        sub = next((s for s in subs if s.get("id") == id), None)
        if not sub:
            return await interaction.response.send_message(
                "Nu exista o urmarire cu acest ID (vezi `/notify list`).", ephemeral=True)
        info = {"author": "Creator Test", "title": "Acesta este un test",
                "url": sub["url"], "thumb": None}
        await self._notify(interaction.guild, sub, info,
                           is_live=sub["platform"] in LIVE_PLATFORMS)
        await interaction.response.send_message("✅ Notificare de test trimisa.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Notifications(bot))
