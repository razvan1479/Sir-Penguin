"""
cogs/game.py — JOC „ghiceste numarul".

Un admin porneste o runda (panou cu butoane Start/Stop/Reset). Dupa Start,
incepe un countdown. Cat timp ruleaza, jucatorii aleg PRIVAT un numar cu
/alege. La final, botul genereaza un numar random si castiga cine s-a apropiat
cel mai mult (egalitatile sunt permise).

Configurabil din dashboard (cheia "game"):
{
  "enabled": false,
  "channel_id": 123,     # canalul unde se joaca
  "countdown": 60,       # secunde
  "min": 0, "max": 100   # intervalul de numere
}

Starea e PER server si tinuta in memorie (o runda se pierde daca botul
reporneste — la fel ca la jocul original).
"""

import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage


def _cfg(gid) -> dict:
    return storage.get(gid, "game", {}) or {}


class _State:
    def __init__(self):
        self.guesses = {}        # user_id -> numar
        self.active = False
        self.task = None
        self.countdown_message = None
        self.lock = asyncio.Lock()


def _is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


class Game(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states = {}  # guild_id -> _State

    def _state(self, gid) -> _State:
        return self.states.setdefault(gid, _State())

    # ----------------------------------------------------- embeds
    def _start_embed(self, cfg):
        lo, hi = cfg.get("min", 0), cfg.get("max", 100)
        return discord.Embed(
            title="🎮 RUNDĂ NOUĂ",
            description=(f"Apasă **▶️ Start rundă**, apoi alege cu **/alege <număr>**\n"
                        f"Interval: **{lo}–{hi}** · 🔒 Alegerea e privată"),
            color=discord.Color.green())

    def _countdown_embed(self, sec, lo=0, hi=100):
        return discord.Embed(
            title="⏳ Runda e activă!",
            description=(f"📝 Scrie **/alege <număr>** (între **{lo}** și **{hi}**) ca să participi!\n"
                        f"⏳ **{sec} secunde** rămase"),
            color=discord.Color.orange())

    def _result_embed(self, rnd, winners, diff, total):
        return discord.Embed(
            title="🏁 REZULTAT FINAL",
            description=(f"🎯 Număr random: **{rnd}**\n"
                        f"📏 Diferență: **{diff}**\n"
                        f"👥 Participanți: **{total}**\n"
                        f"🏆 Câștigător(i): {winners}"),
            color=discord.Color.gold())

    # ----------------------------------------------------- /randome
    @app_commands.command(name="randome", description="Pregateste o runda noua de joc")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def randome(self, interaction: discord.Interaction):
        cfg = _cfg(interaction.guild_id)
        if not cfg.get("enabled"):
            return await interaction.response.send_message(
                "Jocul nu e activat. Activeaza-l din dashboard.", ephemeral=True)
        if cfg.get("channel_id") and interaction.channel_id != int(cfg["channel_id"]):
            return await interaction.response.send_message(
                f"Jocul se joaca doar pe <#{cfg['channel_id']}>.", ephemeral=True)

        st = self._state(interaction.guild_id)
        if st.active:
            return await interaction.response.send_message(
                "Exista deja o runda activa.", ephemeral=True)
        st.guesses = {}
        await interaction.response.send_message(
            embed=self._start_embed(cfg), view=GameView(self, interaction.guild_id))

    # ----------------------------------------------------- /alege
    @app_commands.command(name="alege", description="Alege un numar (privat)")
    @app_commands.describe(numar="Numarul ales")
    async def alege(self, interaction: discord.Interaction, numar: int):
        cfg = _cfg(interaction.guild_id)
        if not cfg.get("enabled"):
            return await interaction.response.send_message("Jocul nu e activat.", ephemeral=True)
        if cfg.get("channel_id") and interaction.channel_id != int(cfg["channel_id"]):
            return await interaction.response.send_message(
                f"Doar pe canalul jocului (<#{cfg['channel_id']}>).", ephemeral=True)

        st = self._state(interaction.guild_id)
        if not st.active:
            return await interaction.response.send_message(
                "Nu exista o runda activa acum.", ephemeral=True)

        lo, hi = int(cfg.get("min", 0)), int(cfg.get("max", 100))
        if not lo <= numar <= hi:
            return await interaction.response.send_message(
                f"⚠️ Alege un numar intre {lo} si {hi}.", ephemeral=True)

        async with st.lock:
            if interaction.user.id in st.guesses:
                return await interaction.response.send_message(
                    f"🔐 Ai ales deja **{st.guesses[interaction.user.id]}**.", ephemeral=True)
            st.guesses[interaction.user.id] = numar

        await interaction.response.send_message(
            f"🔒 Alegerea ta (**{numar}**) a fost salvata.", ephemeral=True)

    @randome.error
    async def _randome_err(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "Ai nevoie de permisiunea **Manage Server** ca sa pornesti jocul.", ephemeral=True)

    # ----------------------------------------------------- timer + final
    async def round_timer(self, guild, channel):
        cfg = _cfg(guild.id)
        st = self._state(guild.id)
        countdown = int(cfg.get("countdown", 60))
        lo, hi = int(cfg.get("min", 0)), int(cfg.get("max", 100))
        st.countdown_message = await channel.send(embed=self._countdown_embed(countdown, lo, hi))
        try:
            while countdown > 0 and st.active:
                await asyncio.sleep(1)
                countdown -= 1
                if countdown % 10 == 0 or countdown <= 5:
                    await st.countdown_message.edit(embed=self._countdown_embed(countdown, lo, hi))
            if st.active:
                await self.finalize(guild, channel)
        except asyncio.CancelledError:
            pass

    async def finalize(self, guild, channel):
        cfg = _cfg(guild.id)
        st = self._state(guild.id)
        if st.task:
            st.task.cancel()
            try:
                await st.task
            except (asyncio.CancelledError, Exception):
                pass
            st.task = None
        st.active = False

        if not st.guesses:
            await channel.send("❌ Nimeni nu a ales un numar.")
            return

        lo, hi = int(cfg.get("min", 0)), int(cfg.get("max", 100))
        rnd = random.randint(lo, hi)
        min_diff = min(abs(n - rnd) for n in st.guesses.values())
        winners = [f"<@{uid}> (**{n}**)" for uid, n in st.guesses.items()
                   if abs(n - rnd) == min_diff]
        await channel.send(embed=self._result_embed(rnd, ", ".join(winners), min_diff, len(st.guesses)))


# ============================================================== butoane
class GameView(discord.ui.View):
    def __init__(self, cog: Game, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    async def _guard(self, interaction) -> bool:
        if not _is_admin(interaction):
            await interaction.response.send_message(
                "Doar un admin poate controla jocul.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="▶️ Start rundă", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        st = self.cog._state(self.guild_id)
        if st.active:
            return await interaction.response.send_message("Runda e deja pornita.", ephemeral=True)
        st.active = True
        button.disabled = True
        await interaction.message.edit(view=self)
        st.task = asyncio.create_task(self.cog.round_timer(interaction.guild, interaction.channel))
        await interaction.response.send_message("▶️ Runda a inceput!", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop rundă", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        st = self.cog._state(self.guild_id)
        if not st.active:
            return await interaction.response.send_message("Nu exista o runda activa.", ephemeral=True)
        await interaction.response.send_message("🛑 Runda a fost oprita.", ephemeral=True)
        await self.cog.finalize(interaction.guild, interaction.channel)

    @discord.ui.button(label="🔄 Reset joc", style=discord.ButtonStyle.secondary)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        st = self.cog._state(self.guild_id)
        st.guesses = {}
        st.active = False
        if st.task:
            st.task.cancel()
            try:
                await st.task
            except (asyncio.CancelledError, Exception):
                pass
            st.task = None
        st.countdown_message = None
        for b in self.children:
            b.disabled = False
        await interaction.message.edit(view=self)
        await interaction.response.send_message("🔄 Jocul a fost resetat.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Game(bot))
