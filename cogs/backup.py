"""
cogs/backup.py — backup si clonare a STRUCTURII unui server Discord.

Salveaza structura (roluri, categorii, canale, permisiuni pe canale, emoji-uri)
ca un "backup" cu nume + data. Din dashboard vezi lista, faci preview si alegi
pe ce server o aplici.

LIMITE Discord (niciun bot nu le poate trece): nu se copiaza mesajele, membrii,
istoricul. Botul aplica doar pe servere unde e deja invitat cu Administrator.

Date (cheia "backups" la nivel de utilizator-server sursa NU; le tinem global
intr-o cheie pe guild-ul SURSA): de fapt le tinem ca lista in storage sub
guild-ul pe care s-a dat /backup, plus un index global ca dashboardul sa le vada.

Schema unui backup:
{
  "id": "...", "name": "...", "source_guild_id": "...", "source_name": "...",
  "created_ts": 0,
  "roles": [{name,color,permissions,hoist,mentionable,position}],
  "categories": [{name,position,overwrites:[...]}],
  "channels": [{name,type,category,position,topic,nsfw,slowmode,user_limit,bitrate,overwrites:[...]}],
  "emojis": [{name,url}]
}
overwrites: [{role_name, allow, deny}]  (doar pe roluri, mapate dupa nume)
"""

import io
import time
import secrets

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage
from utils.perms import has_bot_access


def _index():
    """Index global cu toate backup-urile (id -> meta), tinut sub guild 0."""
    return storage.get(0, "backup_index", {}) or {}


def _save_index(idx):
    storage.set(0, "backup_index", idx)


def _get_backup(bid):
    return storage.get(0, f"backup:{bid}", None)


def _put_backup(bid, data):
    storage.set(0, f"backup:{bid}", data)


def _del_backup(bid):
    # marcam sters punand None (storage nu are delete dedicat)
    storage.set(0, f"backup:{bid}", None)


def _serialize_overwrites(channel):
    out = []
    for target, ow in channel.overwrites.items():
        if isinstance(target, discord.Role):
            allow, deny = ow.pair()
            out.append({"role_name": target.name, "allow": allow.value, "deny": deny.value,
                        "is_default": target.is_default()})
    return out


class Backup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.apply_loop.start()

    def cog_unload(self):
        self.apply_loop.cancel()

    @tasks.loop(seconds=10)
    async def apply_loop(self):
        for guild in self.bot.guilds:
            job = storage.get(guild.id, "backup_apply", None)
            if not job or job.get("status") != "pending":
                continue
            job["status"] = "running"
            storage.set(guild.id, "backup_apply", job)

            snap = _get_backup(job.get("backup_id"))
            if not snap:
                job.update(status="error", result="Backup-ul nu mai exista.")
                storage.set(guild.id, "backup_apply", job)
                continue

            empty = await self.is_empty_guild(guild)
            if not empty:
                # serverul are deja continut -> cerem confirmare (numele serverului)
                if (job.get("confirm") or "").strip() != guild.name:
                    job.update(status="needs_confirm",
                               result=f"Serverul are deja canale/roluri. Scrie exact numele serverului ({guild.name}) ca sa confirmi stergerea si suprascrierea.")
                    storage.set(guild.id, "backup_apply", job)
                    continue

            report = await self.apply_backup(guild, snap, wipe=not empty)
            job.update(status="done",
                       result=f"Gata! {report['roles']} roluri, {report['categories']} categorii, "
                              f"{report['channels']} canale create ({report['errors']} erori).")
            storage.set(guild.id, "backup_apply", job)

    @apply_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------------- creare backup
    def snapshot(self, guild: discord.Guild, name: str) -> dict:
        roles = []
        for r in sorted(guild.roles, key=lambda x: x.position):
            if r.is_default() or r.managed:
                continue
            roles.append({"name": r.name, "color": r.color.value, "permissions": r.permissions.value,
                          "hoist": r.hoist, "mentionable": r.mentionable, "position": r.position})

        categories = []
        for c in sorted(guild.categories, key=lambda x: x.position):
            categories.append({"name": c.name, "position": c.position,
                               "overwrites": _serialize_overwrites(c)})

        channels = []
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel):
                continue
            entry = {"name": ch.name, "category": ch.category.name if ch.category else None,
                     "position": ch.position, "overwrites": _serialize_overwrites(ch)}
            if isinstance(ch, discord.TextChannel):
                # canalele de anunturi (news) sunt TextChannel cu is_news()=True
                is_news = ch.is_news() if hasattr(ch, "is_news") else False
                entry.update(type="news" if is_news else "text", topic=ch.topic,
                             nsfw=ch.nsfw, slowmode=ch.slowmode_delay)
            elif isinstance(ch, discord.VoiceChannel):
                entry.update(type="voice", user_limit=ch.user_limit, bitrate=ch.bitrate)
            elif isinstance(ch, getattr(discord, "StageChannel", ())):
                entry.update(type="stage", bitrate=ch.bitrate, user_limit=ch.user_limit)
            elif isinstance(ch, getattr(discord, "ForumChannel", ())):
                tags = []
                for tag in getattr(ch, "available_tags", []) or []:
                    tags.append({"name": tag.name,
                                 "emoji": str(tag.emoji) if getattr(tag, "emoji", None) else None,
                                 "moderated": getattr(tag, "moderated", False)})
                entry.update(type="forum", topic=ch.topic, nsfw=ch.nsfw, tags=tags)
            else:
                continue
            channels.append(entry)

        emojis = [{"name": e.name, "url": str(e.url), "animated": e.animated} for e in guild.emojis]
        stickers = [{"name": s.name, "description": s.description or "",
                     "emoji": getattr(s, "emoji", "") or "⭐", "url": str(s.url)}
                    for s in getattr(guild, "stickers", [])]

        def _cname(ch):
            return ch.name if ch else None

        settings = {
            "verification_level": guild.verification_level.value,
            "default_notifications": guild.default_notifications.value,
            "explicit_content_filter": guild.explicit_content_filter.value,
            "afk_timeout": guild.afk_timeout,
            "afk_channel": _cname(guild.afk_channel),
            "system_channel": _cname(guild.system_channel),
            "system_channel_flags": guild.system_channel_flags.value,
            "rules_channel": _cname(guild.rules_channel),
            "public_updates_channel": _cname(guild.public_updates_channel),
            "icon": str(guild.icon.url) if guild.icon else None,
            "banner": str(guild.banner.url) if guild.banner else None,
            "splash": str(guild.splash.url) if guild.splash else None,
        }

        return {
            "id": secrets.token_hex(5), "name": name,
            "source_guild_id": str(guild.id), "source_name": guild.name,
            "created_ts": time.time(),
            "roles": roles, "categories": categories, "channels": channels,
            "emojis": emojis, "stickers": stickers, "settings": settings,
        }

    @app_commands.command(name="backup", description="Salveaza structura acestui server ca backup")
    @app_commands.describe(nume="Un nume pentru backup (optional)")
    async def backup_cmd(self, interaction: discord.Interaction, nume: str = None):
        if not has_bot_access(interaction):
            return await interaction.response.send_message("Nu ai acces.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        name = nume or f"{interaction.guild.name} · {time.strftime('%d.%m.%Y %H:%M')}"
        snap = self.snapshot(interaction.guild, name)
        _put_backup(snap["id"], snap)
        idx = _index()
        idx[snap["id"]] = {"id": snap["id"], "name": name, "source_name": snap["source_name"],
                           "source_guild_id": snap["source_guild_id"], "created_ts": snap["created_ts"],
                           "owner_id": str(interaction.user.id),
                           "n_roles": len(snap["roles"]), "n_channels": len(snap["channels"]),
                           "n_categories": len(snap["categories"]),
                           "n_emojis": len(snap["emojis"]), "n_stickers": len(snap["stickers"])}
        _save_index(idx)
        await interaction.followup.send(
            f"✅ Backup salvat: **{name}**\n"
            f"📦 {len(snap['roles'])} roluri · {len(snap['categories'])} categorii · "
            f"{len(snap['channels'])} canale · {len(snap['emojis'])} emoji-uri · "
            f"{len(snap['stickers'])} stickere · setarile serverului\n"
            f"Vezi-le si aplica-le din dashboard → Backup-uri.", ephemeral=True)

    # ----------------------------------------------------- aplicare
    async def is_empty_guild(self, guild: discord.Guild) -> bool:
        """Server 'gol/proaspat': fara roluri custom si <=2 canale implicite."""
        custom_roles = [r for r in guild.roles if not r.is_default() and not r.managed]
        return len(custom_roles) == 0 and len(guild.channels) <= 2

    async def apply_backup(self, guild: discord.Guild, snap: dict, wipe: bool) -> dict:
        report = {"roles": 0, "categories": 0, "channels": 0, "errors": 0}

        # 1) stergem ce se poate (daca wipe)
        if wipe:
            for ch in list(guild.channels):
                try:
                    await ch.delete(reason="Backup apply")
                except discord.HTTPException:
                    report["errors"] += 1
            for r in list(guild.roles):
                if r.is_default() or r.managed or r >= guild.me.top_role:
                    continue
                try:
                    await r.delete(reason="Backup apply")
                except discord.HTTPException:
                    report["errors"] += 1

        # roluri: le cream de la cel mai INALT la cel mai jos. Discord pune fiecare
        # rol nou jos (deasupra lui @everyone) si le impinge pe cele dinainte in sus,
        # deci primul creat ajunge cel mai sus -> ordinea iese corecta.
        role_map = {}  # name -> Role
        for rd in sorted(snap.get("roles", []), key=lambda x: x["position"], reverse=True):
            try:
                role = await guild.create_role(
                    name=rd["name"], colour=discord.Colour(rd["color"]),
                    permissions=discord.Permissions(rd["permissions"]),
                    hoist=rd["hoist"], mentionable=rd["mentionable"],
                    reason="Backup apply")
                role_map[rd["name"]] = role
                report["roles"] += 1
            except discord.HTTPException:
                report["errors"] += 1

        def build_overwrites(ows):
            res = {}
            for o in ows:
                if o.get("is_default"):
                    target = guild.default_role
                else:
                    target = role_map.get(o["role_name"])
                if not target:
                    continue
                res[target] = discord.PermissionOverwrite.from_pair(
                    discord.Permissions(o["allow"]), discord.Permissions(o["deny"]))
            return res

        # 3) categorii
        cat_map = {}
        for cd in sorted(snap.get("categories", []), key=lambda x: x["position"]):
            try:
                cat = await guild.create_category(
                    name=cd["name"], overwrites=build_overwrites(cd.get("overwrites", [])),
                    reason="Backup apply")
                cat_map[cd["name"]] = cat
                report["categories"] += 1
            except discord.HTTPException:
                report["errors"] += 1

        # 4) canale
        chan_map = {}  # name -> obiect canal creat (pentru setarile serverului)
        for chd in sorted(snap.get("channels", []), key=lambda x: x["position"]):
            try:
                cat = cat_map.get(chd.get("category"))
                ow = build_overwrites(chd.get("overwrites", []))
                new_ch = None
                if chd["type"] in ("text", "news"):
                    new_ch = await guild.create_text_channel(
                        name=chd["name"], category=cat, overwrites=ow,
                        topic=chd.get("topic"), nsfw=chd.get("nsfw", False),
                        slowmode_delay=chd.get("slowmode", 0), reason="Backup apply")
                    if chd["type"] == "news":
                        try:
                            await new_ch.edit(type=discord.ChannelType.news)
                        except (discord.HTTPException, TypeError, AttributeError):
                            pass
                elif chd["type"] == "voice":
                    new_ch = await guild.create_voice_channel(
                        name=chd["name"], category=cat, overwrites=ow,
                        user_limit=chd.get("user_limit", 0),
                        bitrate=min(chd.get("bitrate", 64000), guild.bitrate_limit),
                        reason="Backup apply")
                elif chd["type"] == "stage":
                    new_ch = await guild.create_stage_channel(
                        name=chd["name"], category=cat, overwrites=ow, reason="Backup apply")
                elif chd["type"] == "forum":
                    new_ch = await self._create_forum(guild, chd, cat, ow)
                else:
                    continue
                if new_ch is not None:
                    chan_map[chd["name"]] = new_ch
                report["channels"] += 1
            except (discord.HTTPException, AttributeError, TypeError):
                report["errors"] += 1

        # 5) emoji-uri (descarcate de pe CDN si re-incarcate)
        for ed in snap.get("emojis", []):
            data = await self._download(ed.get("url"))
            if not data:
                report["errors"] += 1
                continue
            try:
                await guild.create_custom_emoji(name=ed["name"], image=data, reason="Backup apply")
                report["emojis"] = report.get("emojis", 0) + 1
            except discord.HTTPException:
                report["errors"] += 1  # depasit limita serverului / nume invalid

        # 6) stickere
        for sd in snap.get("stickers", []):
            data = await self._download(sd.get("url"))
            if not data:
                report["errors"] += 1
                continue
            try:
                file = discord.File(io.BytesIO(data), filename="sticker.png")
                await guild.create_sticker(name=sd["name"], description=sd.get("description", ""),
                                           emoji=sd.get("emoji", "⭐"), file=file, reason="Backup apply")
                report["stickers"] = report.get("stickers", 0) + 1
            except discord.HTTPException:
                report["errors"] += 1

        # 7) setarile serverului
        await self._apply_settings(guild, snap.get("settings", {}), chan_map)

        return report

    async def _download(self, url):
        if not url:
            return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as r:
                    if r.status == 200:
                        return await r.read()
        except Exception:
            return None
        return None

    async def _apply_settings(self, guild, s, chan_map):
        if not s:
            return
        kw = {}
        try:
            kw["verification_level"] = discord.VerificationLevel(s["verification_level"])
            kw["default_notifications"] = discord.NotificationLevel(s["default_notifications"])
            kw["explicit_content_filter"] = discord.ContentFilter(s["explicit_content_filter"])
        except (ValueError, KeyError):
            pass
        if s.get("afk_timeout") is not None:
            kw["afk_timeout"] = s["afk_timeout"]
        if s.get("afk_channel") and chan_map.get(s["afk_channel"]):
            kw["afk_channel"] = chan_map[s["afk_channel"]]
        if s.get("system_channel") and chan_map.get(s["system_channel"]):
            kw["system_channel"] = chan_map[s["system_channel"]]
        try:
            if s.get("system_channel_flags") is not None:
                kw["system_channel_flags"] = discord.SystemChannelFlags._from_value(s["system_channel_flags"])
        except Exception:
            pass
        # iconita / banner / splash (descarcate)
        for key in ("icon", "banner", "splash"):
            if s.get(key):
                data = await self._download(s[key])
                if data:
                    kw[key] = data
        try:
            await guild.edit(reason="Backup apply", **kw)
        except (discord.HTTPException, TypeError):
            pass
        # canale community (doar daca serverul tinta e Community)
        comm = {}
        if s.get("rules_channel") and chan_map.get(s["rules_channel"]):
            comm["rules_channel"] = chan_map[s["rules_channel"]]
        if s.get("public_updates_channel") and chan_map.get(s["public_updates_channel"]):
            comm["public_updates_channel"] = chan_map[s["public_updates_channel"]]
        if comm:
            try:
                await guild.edit(reason="Backup apply", **comm)
            except (discord.HTTPException, TypeError):
                pass

    async def _create_forum(self, guild, chd, cat, ow):
        # construim tag-urile forumului (daca versiunea de discord.py le suporta)
        tags = []
        for td in chd.get("tags", []) or []:
            try:
                emoji = td.get("emoji") or None
                tags.append(discord.ForumTag(name=td["name"], emoji=emoji,
                                             moderated=td.get("moderated", False)))
            except Exception:
                tags.append(discord.ForumTag(name=td["name"]))
        kwargs = dict(name=chd["name"], category=cat, overwrites=ow,
                      topic=chd.get("topic"), nsfw=chd.get("nsfw", False), reason="Backup apply")
        try:
            return await guild.create_forum(available_tags=tags, **kwargs)
        except TypeError:
            # versiune mai veche fara available_tags -> cream forumul fara tag-uri
            return await guild.create_forum(**kwargs)


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))
