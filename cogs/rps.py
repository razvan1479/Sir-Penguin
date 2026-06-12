"""
cogs/rps.py — joc Piatra / Foarfeca / Hartie (1v1).

/rps <adversar> porneste un meci pe canal. Fiecare jucator primeste un meniu
privat de unde alege. Cand ambii au ales, se afiseaza rezultatul si un buton
de Rematch.

Configurabil din dashboard (cheia "rps"):
{
  "enabled": false,
  "channel_id": null   # daca e setat, doar pe acel canal; gol = orice canal
}

Starea jocurilor e per-CANAL (in memorie) — un meci se pierde daca botul
reporneste, la fel ca la jocul original.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage

CHOICES = ["Piatra", "Foarfeca", "Hartie"]
EMOJI = {"Piatra": "🪨", "Foarfeca": "✂️", "Hartie": "📄"}


def _cfg(gid) -> dict:
    return storage.get(gid, "rps", {}) or {}


def _winner(c1, c2):
    """Returneaza 0=egal, 1=jucator1, 2=jucator2."""
    if c1 == c2:
        return 0
    beats = {"Piatra": "Foarfeca", "Foarfeca": "Hartie", "Hartie": "Piatra"}
    return 1 if beats[c1] == c2 else 2


class Rps(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games = {}  # channel_id -> {players:[{id,choice}], channel}

    # ----------------------------------------------------- comanda
    @app_commands.command(name="rps", description="Piatra / Foarfeca / Hartie cu un adversar")
    @app_commands.describe(adversar="Cu cine joci")
    async def rps(self, interaction: discord.Interaction, adversar: discord.Member):
        cfg = _cfg(interaction.guild_id)
        if not cfg.get("enabled"):
            return await interaction.response.send_message(
                "Jocul nu e activat. Activeaza-l din dashboard.", ephemeral=True)
        if cfg.get("channel_id") and interaction.channel_id != int(cfg["channel_id"]):
            return await interaction.response.send_message(
                f"Jocul se poate juca doar pe <#{cfg['channel_id']}>.", ephemeral=True)
        if adversar.bot or adversar.id == interaction.user.id:
            return await interaction.response.send_message(
                "Alege un adversar real (nu un bot si nu pe tine).", ephemeral=True)
        if interaction.channel_id in self.games:
            return await interaction.response.send_message(
                "Exista deja un joc activ pe acest canal.", ephemeral=True)

        cid = interaction.channel_id
        self.games[cid] = {
            "players": [{"id": interaction.user.id, "choice": None},
                        {"id": adversar.id, "choice": None}],
            "channel": interaction.channel,
        }

        await interaction.response.send_message(
            f"🎮 Meci **Piatra-Foarfeca-Hartie** intre {interaction.user.mention} si {adversar.mention}!\n"
            f"💡 Vrei si tu un meci? Scrie **/rps @cineva**")

        colors = ["🔴", "🔵"]
        for index, pid in enumerate([interaction.user.id, adversar.id]):
            await interaction.channel.send(
                f"{colors[index]} <@{pid}>, alege din meniul de mai jos 👇",
                view=RPSView(self, cid, index))

    # ----------------------------------------------------- final
    async def finish(self, channel_id):
        game = self.games.get(channel_id)
        if not game:
            return
        p1, p2 = game["players"]
        c1, c2 = p1["choice"], p2["choice"]
        w = _winner(c1, c2)
        if w == 0:
            rez = "🤝 **Egalitate!**"
        elif w == 1:
            rez = f"🏆 <@{p1['id']}> a castigat!"
        else:
            rez = f"🏆 <@{p2['id']}> a castigat!"

        try:
            await game["channel"].send(
                f"<@{p1['id']}> a ales **{EMOJI[c1]} {c1}**, "
                f"<@{p2['id']}> a ales **{EMOJI[c2]} {c2}**\n{rez}",
                view=RematchView(self, channel_id, [p1["id"], p2["id"]]))
        except (discord.NotFound, discord.Forbidden):
            pass
        self.games.pop(channel_id, None)

    # ----------------------------------------------------- protectii
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        self.games.pop(channel.id, None)


# ============================================================== UI
class RPSSelect(discord.ui.Select):
    def __init__(self, cog: Rps, channel_id, player_index):
        self.cog = cog
        self.channel_id = channel_id
        self.player_index = player_index
        super().__init__(
            placeholder="Alege Piatra, Foarfeca sau Hartie...",
            options=[discord.SelectOption(label=c, emoji=EMOJI[c]) for c in CHOICES])

    async def callback(self, interaction: discord.Interaction):
        game = self.cog.games.get(self.channel_id)
        if not game:
            return await interaction.response.send_message("Jocul nu mai exista.", ephemeral=True)
        player = game["players"][self.player_index]
        if interaction.user.id != player["id"]:
            return await interaction.response.send_message("Nu e randul tau sa alegi!", ephemeral=True)
        player["choice"] = self.values[0]
        await interaction.response.send_message(
            f"Alegerea ta (**{EMOJI[self.values[0]]} {self.values[0]}**) a fost inregistrata!", ephemeral=True)
        if all(p["choice"] for p in game["players"]):
            await self.cog.finish(self.channel_id)


class RPSView(discord.ui.View):
    def __init__(self, cog: Rps, channel_id, player_index):
        super().__init__(timeout=None)
        self.add_item(RPSSelect(cog, channel_id, player_index))


class RematchView(discord.ui.View):
    def __init__(self, cog: Rps, channel_id, players):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.players = players

    @discord.ui.button(label="🔁 Rematch", style=discord.ButtonStyle.green)
    async def rematch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.players:
            return await interaction.response.send_message(
                "Doar jucatorii din meci pot cere rematch.", ephemeral=True)
        if self.channel_id in self.cog.games:
            return await interaction.response.send_message("Exista deja un joc activ.", ephemeral=True)

        self.cog.games[self.channel_id] = {
            "players": [{"id": self.players[0], "choice": None},
                        {"id": self.players[1], "choice": None}],
            "channel": interaction.channel,
        }
        await interaction.response.send_message("🔁 **Rematch inceput!**")
        colors = ["🔴", "🔵"]
        for index, pid in enumerate(self.players):
            await interaction.channel.send(
                f"{colors[index]} <@{pid}>, alege din meniul de mai jos 👇",
                view=RPSView(self.cog, self.channel_id, index))


async def setup(bot: commands.Bot):
    await bot.add_cog(Rps(bot))
