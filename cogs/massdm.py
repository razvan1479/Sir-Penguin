"""
cogs/massdm.py — mesaje DM esalonate catre membrii serverului.

ATENTIE / RISC: DM-urile in masa nesolicitate sunt considerate spam de Discord
si pot duce la ban-ul botului. De aceea trimitem RAR (implicit 1/minut), sarim
botii si pe cei cu DM inchise, si exista limita zilnica + buton de oprire.

Campania se porneste din dashboard (sau cu /dm_masa pe server). Botul trimite in
fundal, unul cate unul, si scrie progresul in storage ca dashboardul sa-l arate.

Date salvate (cheia "massdm"):
{
  "campaign": {
    "status": "running"|"stopped"|"done",
    "message": "...", "footer": "",
    "role_id": None,            # None = toti membrii (fara boti)
    "delay_seconds": 60,        # pauza intre mesaje
    "daily_limit": 50,          # max pe zi
    "sent": 0, "failed": 0, "total": 0,
    "sent_today": 0, "day": "2026-06-07",
    "queue": [user_id, ...],    # cine a mai ramas
    "started_by": uid, "last_ts": 0
  }
}
"""

import time
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage
from utils.perms import bot_access


def _today():
    return datetime.date.today().isoformat()


class MassDM(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sender_loop.start()

    def cog_unload(self):
        self.sender_loop.cancel()

    def _camp(self, gid):
        return storage.get(gid, "massdm", {}).get("campaign", {}) or {}

    def _save(self, gid, camp):
        storage.set(gid, "massdm", {"campaign": camp})

    # ---------------------------------------------------- bucla de trimitere
    @tasks.loop(seconds=10)
    async def sender_loop(self):
        now = time.time()
        for guild in self.bot.guilds:
            camp = self._camp(guild.id)
            if camp.get("status") != "running":
                continue

            # resetam contorul zilnic la schimbarea zilei
            if camp.get("day") != _today():
                camp["day"] = _today()
                camp["sent_today"] = 0

            # daca a fost pornita din dashboard, construim coada de destinatari acum
            if camp.get("build_queue"):
                camp["queue"] = self._recipients(guild, camp.get("role_id"))
                camp["total"] = len(camp["queue"])
                camp["build_queue"] = False
                self._save(guild.id, camp)

            # limita zilnica atinsa -> asteptam ziua urmatoare
            if camp.get("sent_today", 0) >= camp.get("daily_limit", 50):
                self._save(guild.id, camp)
                continue

            # respectam pauza dintre mesaje
            if now - camp.get("last_ts", 0) < camp.get("delay_seconds", 60):
                continue

            queue = camp.get("queue", [])
            if not queue:
                camp["status"] = "done"
                self._save(guild.id, camp)
                continue

            uid = queue.pop(0)
            member = guild.get_member(int(uid))
            if member and not member.bot:
                text = camp.get("message", "")
                footer = camp.get("footer", "")
                if footer:
                    text = f"{text}\n\n*{footer}*"
                try:
                    await member.send(text)
                    camp["sent"] = camp.get("sent", 0) + 1
                    camp["sent_today"] = camp.get("sent_today", 0) + 1
                except (discord.Forbidden, discord.HTTPException):
                    # DM inchise / blocat -> il sarim, fara reincercare
                    camp["failed"] = camp.get("failed", 0) + 1
            else:
                camp["failed"] = camp.get("failed", 0) + 1

            camp["queue"] = queue
            camp["last_ts"] = now
            if not queue:
                camp["status"] = "done"
            self._save(guild.id, camp)

    @sender_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------- comenzi
    @app_commands.command(name="dm_masa", description="Porneste o campanie de DM esalonat (din dashboard se configureaza)")
    @bot_access()
    async def dm_masa(self, interaction: discord.Interaction):
        camp = self._camp(interaction.guild_id)
        if camp.get("status") == "running":
            return await interaction.response.send_message(
                "Exista deja o campanie activa. Opreste-o din dashboard.", ephemeral=True)
        if not camp.get("message"):
            return await interaction.response.send_message(
                "Scrie intai mesajul in dashboard, pagina Mesaje DM.", ephemeral=True)
        # (re)construim coada de destinatari
        members = self._recipients(interaction.guild, camp.get("role_id"))
        camp.update(status="running", total=len(members), sent=0, failed=0,
                    sent_today=0, day=_today(), queue=members, last_ts=0,
                    started_by=interaction.user.id)
        self._save(interaction.guild_id, camp)
        await interaction.response.send_message(
            f"📤 Campanie pornita catre **{len(members)}** membri, "
            f"cate 1 la {camp.get('delay_seconds',60)} sec. Vezi progresul in dashboard.",
            ephemeral=True)

    @app_commands.command(name="dm_stop", description="Opreste campania de DM in masa")
    @bot_access()
    async def dm_stop(self, interaction: discord.Interaction):
        camp = self._camp(interaction.guild_id)
        if camp.get("status") != "running":
            return await interaction.response.send_message("Nu e nicio campanie activa.", ephemeral=True)
        camp["status"] = "stopped"
        self._save(interaction.guild_id, camp)
        await interaction.response.send_message(
            f"🛑 Oprit. Trimise: {camp.get('sent',0)}, esuate: {camp.get('failed',0)}.", ephemeral=True)

    def _recipients(self, guild, role_id):
        out = []
        for m in guild.members:
            if m.bot:
                continue
            if role_id and not any(str(r.id) == str(role_id) for r in m.roles):
                continue
            out.append(str(m.id))
        return out


async def setup(bot: commands.Bot):
    await bot.add_cog(MassDM(bot))
