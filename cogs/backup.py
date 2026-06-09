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

import time
import secrets

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
                entry.update(type="text", topic=ch.topic, nsfw=ch.nsfw,
                             slowmode=ch.slowmode_delay)
            elif isinstance(ch, discord.VoiceChannel):
                entry.update(type="voice", user_limit=ch.user_limit, bitrate=ch.bitrate)
            else:
                continue
            channels.append(entry)

        emojis = [{"name": e.name, "url": str(e.url)} for e in guild.emojis]

        return {
            "id": secrets.token_hex(5), "name": name,
            "source_guild_id": str(guild.id), "source_name": guild.name,
            "created_ts": time.time(),
            "roles": roles, "categories": categories, "channels": channels, "emojis": emojis,
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
                           "n_categories": len(snap["categories"])}
        _save_index(idx)
        await interaction.followup.send(
            f"✅ Backup salvat: **{name}**\n"
            f"📦 {len(snap['roles'])} roluri · {len(snap['categories'])} categorii · "
            f"{len(snap['channels'])} canale · {len(snap['emojis'])} emoji-uri\n"
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

        # 2) roluri (de jos in sus, ca pozitia sa iasa corect)
        role_map = {}  # name -> Role
        for rd in sorted(snap.get("roles", []), key=lambda x: x["position"]):
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
        for chd in sorted(snap.get("channels", []), key=lambda x: x["position"]):
            try:
                cat = cat_map.get(chd.get("category"))
                ow = build_overwrites(chd.get("overwrites", []))
                if chd["type"] == "text":
                    await guild.create_text_channel(
                        name=chd["name"], category=cat, overwrites=ow,
                        topic=chd.get("topic"), nsfw=chd.get("nsfw", False),
                        slowmode_delay=chd.get("slowmode", 0), reason="Backup apply")
                else:
                    await guild.create_voice_channel(
                        name=chd["name"], category=cat, overwrites=ow,
                        user_limit=chd.get("user_limit", 0),
                        bitrate=min(chd.get("bitrate", 64000), guild.bitrate_limit),
                        reason="Backup apply")
                report["channels"] += 1
            except discord.HTTPException:
                report["errors"] += 1

        return report


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))
