"""
cogs/cleanup.py — sterge mesajele unui membru/bot dintr-un canal sau de pe tot serverul.

- Comanda:  /clean membru:<@cine> [canal:<#unde>]   (gol = canalul curent)
            /clean_all membru:<@cine>                (tot serverul)
- Dashboard: pagina /cleanup trimite un "job" pe care botul il executa in fundal.

Sterge TOATE mesajele persoanei, oricat de vechi:
  - mesajele < 14 zile: rapid (bulk delete)
  - mesajele mai vechi: unul cate unul (mai lent, limita Discord)
"""

import asyncio
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage
from utils.perms import has_bot_access

CUTOFF_DAYS = 14  # sub asta se poate bulk-delete; peste, se sterge individual


async def _purge_channel(channel: discord.TextChannel, user_id: int,
                         progress=None) -> int:
    """Sterge toate mesajele lui user_id dintr-un canal. Intoarce cate a sters."""
    deleted = 0
    now = datetime.datetime.now(datetime.timezone.utc)
    recent = []  # mesaje < 14 zile (pentru bulk)
    old = []     # mesaje mai vechi (individual)
    try:
        async for m in channel.history(limit=None):
            try:
                if m.author.id != user_id:
                    continue
                if (now - m.created_at).days < CUTOFF_DAYS:
                    recent.append(m)
                else:
                    old.append(m)
            except Exception:
                continue  # un mesaj ciudat nu opreste parcurgerea
    except Exception:
        return deleted  # nu putem citi istoricul (permisiuni etc.) -> sarim peste canal

    # bulk delete pentru cele recente (in transe de 100)
    for i in range(0, len(recent), 100):
        batch = recent[i:i + 100]
        try:
            await channel.delete_messages(batch)
            deleted += len(batch)
        except Exception:
            # daca bulk esueaza, incercam individual — si sarim peste ce nu merge
            for msg in batch:
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(0.7)
                except Exception:
                    continue  # nu putem sterge mesajul asta -> trecem la urmatorul
        if progress:
            try:
                await progress(deleted)
            except Exception:
                pass

    # individual pentru cele vechi (mai lent, cu pauza ca sa nu fim rate-limited)
    for msg in old:
        try:
            await msg.delete()
            deleted += 1
            await asyncio.sleep(0.8)
        except Exception:
            continue  # sarim peste mesajul care nu poate fi sters
        if progress and deleted % 10 == 0:
            try:
                await progress(deleted)
            except Exception:
                pass

    return deleted


class Cleanup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.job_loop.start()

    def cog_unload(self):
        self.job_loop.cancel()

    # -------------------------------------------------- comenzi
    @app_commands.command(
        name="clean",
        description="Sterge toate mesajele unui membru/bot de pe un canal")
    @app_commands.describe(
        membru="Cui ii stergem mesajele (membru sau bot)",
        canal="De pe ce canal (gol = canalul curent)")
    async def clean(self, interaction: discord.Interaction,
                    membru: discord.User,
                    canal: discord.TextChannel = None):
        if not has_bot_access(interaction):
            return await interaction.response.send_message(
                "Nu ai voie sa folosesti asta.", ephemeral=True)
        channel = canal or interaction.channel
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.manage_messages:
            return await interaction.response.send_message(
                f"Nu am permisiunea „Gestioneaza mesajele” pe {channel.mention}.",
                ephemeral=True)
        await interaction.response.send_message(
            f"🧹 Sterg mesajele lui {membru.mention} din {channel.mention}… "
            "(poate dura la mesaje vechi)", ephemeral=True)
        n = await _purge_channel(channel, membru.id)
        await interaction.followup.send(
            f"✅ Gata! Am sters **{n}** mesaje ale lui {membru.mention} din {channel.mention}.",
            ephemeral=True)

    @app_commands.command(
        name="clean_all",
        description="Sterge toate mesajele unui membru/bot de pe TOT serverul")
    @app_commands.describe(membru="Cui ii stergem mesajele (de pe tot serverul)")
    async def clean_all(self, interaction: discord.Interaction,
                        membru: discord.User):
        if not has_bot_access(interaction):
            return await interaction.response.send_message(
                "Nu ai voie sa folosesti asta.", ephemeral=True)
        await interaction.response.send_message(
            f"🧹 Am pornit stergerea mesajelor lui {membru.mention} de pe **tot serverul**. "
            "Lucrez in fundal — poate dura. Iti scriu cand termin.", ephemeral=True)
        total = 0
        for channel in interaction.guild.text_channels:
            if not channel.permissions_for(interaction.guild.me).manage_messages:
                continue
            total += await _purge_channel(channel, membru.id)
        await interaction.followup.send(
            f"✅ Gata! Am sters **{total}** mesaje ale lui {membru.mention} de pe tot serverul.",
            ephemeral=True)

    # -------------------------------------------------- job din dashboard
    @tasks.loop(seconds=10)
    async def job_loop(self):
        for guild in list(self.bot.guilds):
            try:
                job = storage.get(guild.id, "cleanup_job", None)
                if not job or job.get("status") != "pending":
                    continue
                job["status"] = "running"
                job["deleted"] = 0
                storage.set(guild.id, "cleanup_job", job)

                uid = int(job.get("user_id", 0))
                if not uid:
                    job.update(status="error", result="Lipseste ID-ul membrului.")
                    storage.set(guild.id, "cleanup_job", job)
                    continue

                async def progress(n, gid=guild.id):
                    try:
                        j = storage.get(gid, "cleanup_job", {}) or {}
                        j["deleted"] = n
                        storage.set(gid, "cleanup_job", j)
                    except Exception:
                        pass

                total = 0
                if job.get("channel_id"):  # un singur canal
                    ch = guild.get_channel(int(job["channel_id"]))
                    if ch and ch.permissions_for(guild.me).manage_messages:
                        total = await _purge_channel(ch, uid, progress)
                else:  # tot serverul — un canal problematic nu opreste restul
                    for ch in guild.text_channels:
                        try:
                            if ch.permissions_for(guild.me).manage_messages:
                                total += await _purge_channel(ch, uid, progress)
                        except Exception:
                            continue  # sarim peste canalul cu probleme

                job = storage.get(guild.id, "cleanup_job", {}) or {}
                job.update(status="done", deleted=total,
                           result=f"Am sters {total} mesaje.")
                storage.set(guild.id, "cleanup_job", job)
            except Exception:
                # orice eroare neasteptata: marcam jobul si mergem mai departe,
                # bucla NU se opreste (botul nu crapa)
                try:
                    j = storage.get(guild.id, "cleanup_job", {}) or {}
                    j.update(status="error", result="A aparut o eroare, dar botul merge mai departe.")
                    storage.set(guild.id, "cleanup_job", j)
                except Exception:
                    pass
                continue

    @job_loop.before_loop
    async def _before_job(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------- AUTO-STERGERE
    # structura in storage, cheia "autodelete":
    #   { "<user_id>": {"scope": "server"}  sau  {"scope": "channel", "channel_id": <id>} }
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if not message.guild or message.author.id == self.bot.user.id:
                return
            rules = storage.get(message.guild.id, "autodelete", {}) or {}
            rule = rules.get(str(message.author.id))
            if not rule:
                return
            # daca e setat pe un canal anume, stergem doar acolo
            if rule.get("scope") == "channel" and str(rule.get("channel_id")) != str(message.channel.id):
                return
            await message.delete()
        except Exception:
            pass  # orice eroare (fara permisiune, mesaj deja sters, etc.) -> ignoram, botul merge

    @app_commands.command(
        name="autodelete",
        description="Sterge automat orice mesaj scrie cineva (mut dur)")
    @app_commands.describe(
        membru="Cui ii stergem automat mesajele",
        stare="Pornit sau oprit",
        canal="Doar pe acest canal (gol = tot serverul)")
    @app_commands.choices(stare=[
        app_commands.Choice(name="pornit", value="on"),
        app_commands.Choice(name="oprit", value="off"),
    ])
    async def autodelete(self, interaction: discord.Interaction,
                         membru: discord.User,
                         stare: app_commands.Choice[str],
                         canal: discord.TextChannel = None):
        if not has_bot_access(interaction):
            return await interaction.response.send_message(
                "Nu ai voie sa folosesti asta.", ephemeral=True)
        rules = storage.get(interaction.guild_id, "autodelete", {}) or {}
        if stare.value == "off":
            rules.pop(str(membru.id), None)
            storage.set(interaction.guild_id, "autodelete", rules)
            return await interaction.response.send_message(
                f"✅ Am oprit auto-stergerea pentru {membru.mention}.", ephemeral=True)
        if canal:
            rules[str(membru.id)] = {"scope": "channel", "channel_id": canal.id}
            where = f"pe {canal.mention}"
        else:
            rules[str(membru.id)] = {"scope": "server"}
            where = "pe tot serverul"
        storage.set(interaction.guild_id, "autodelete", rules)
        await interaction.response.send_message(
            f"🧹 Gata — orice scrie {membru.mention} {where} se va sterge automat. "
            "Opreste cu `/autodelete` → oprit.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Cleanup(bot))
