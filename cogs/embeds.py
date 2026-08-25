"""
cogs/embeds.py — EMBED BUILDER (mesaje custom, ca regulamentul).

Creezi embed-uri in dashboard (titlu, text, imagine/gif, footer, culoare),
li se da un nume, apoi le postezi pe server cu o comanda.

Date salvate (cheia "embeds"):
{
  "regulament": {
    "title": "📜 Regulament General",
    "description": "**1. Dispozitii Generale**\n• Regula 1\n• Regula 2 ...",
    "color": "#1abc9c",
    "image": "https://...png|gif",   # imaginea mare de jos
    "thumbnail": "https://...",       # imagine mica dreapta sus
    "footer": "Prin participare confirmi ca accepti regulamentul."
  }
}

Comenzi (admin):
  /embed send <nume> [canal]   - posteaza embed-ul pe server
  /embed preview <nume>        - il vezi doar tu, fara sa-l postezi
  /embed list                  - lista embed-urilor salvate
  /embed delete <nume>         - sterge un embed
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import bot_access


def _linkify_channels(text: str, guild) -> str:
    """Transforma #nume-canal in <#ID> ca Discord sa-l faca link clicabil.
    Cauta canalul dupa nume pe server; daca nu-l gaseste, lasa textul asa cum e."""
    if not text or guild is None:
        return text or ""
    # numele de canale pe Discord: litere mici, cifre, cratime, underscore
    import re

    def repl(m):
        raw = m.group(1)
        name = raw.lower().replace(" ", "-")
        ch = discord.utils.get(guild.channels, name=name) \
            or discord.utils.get(guild.channels, name=raw)
        return f"<#{ch.id}>" if ch else m.group(0)

    # prinde #ceva (dar nu daca e deja <#123> — acelea incep cu <)
    return re.sub(r"(?<!<)#([A-Za-z0-9_\- ]{1,90}?)(?=\s|$|[.,;:!?)\]])", repl, text)


def _color_from_hex(value: str) -> discord.Color:
    try:
        return discord.Color(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Color.blurple()


def build_embed(data: dict, guild=None) -> discord.Embed:
    """Construieste un discord.Embed dintr-un dict salvat in dashboard.
    Daca primeste guild, transforma #nume-canal in linkuri clicabile."""
    desc = _linkify_channels(data.get("description") or "", guild) or None
    embed = discord.Embed(
        title=(data.get("title") or None),
        description=desc,
        color=_color_from_hex(data.get("color", "#5865f2")),
    )
    if data.get("thumbnail"):
        embed.set_thumbnail(url=data["thumbnail"])
    if data.get("image"):
        embed.set_image(url=data["image"])
    if data.get("footer"):
        embed.set_footer(text=data["footer"])
    if data.get("author"):
        embed.set_author(name=data["author"])
    for f in data.get("fields", []):
        embed.add_field(
            name=_linkify_channels(f.get("name", "\u200b"), guild) or "\u200b",
            value=_linkify_channels(f.get("value", "\u200b"), guild) or "\u200b",
            inline=bool(f.get("inline", False)))
    return embed


class Embeds(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="embed", description="Posteaza embed-uri custom")

    @group.command(name="send", description="Posteaza un embed salvat pe server")
    @app_commands.describe(nume="Numele embed-ului din dashboard", canal="Unde sa-l postez")
    @bot_access()
    async def send(self, interaction: discord.Interaction, nume: str,
                   canal: discord.TextChannel = None):
        embeds = storage.get(interaction.guild_id, "embeds", {})
        data = embeds.get(nume)
        if not data:
            return await interaction.response.send_message(
                f"Nu exista un embed numit `{nume}`. Vezi `/embed list`.", ephemeral=True)

        target = canal or interaction.channel
        try:
            await target.send(embed=build_embed(data, interaction.guild))
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"Nu am putut posta embed-ul: `{e}` (verifica linkurile imaginilor).", ephemeral=True)
        await interaction.response.send_message(f"✅ Embed `{nume}` postat in {target.mention}.", ephemeral=True)

    @group.command(name="preview", description="Vezi un embed fara sa-l postezi")
    @bot_access()
    async def preview(self, interaction: discord.Interaction, nume: str):
        embeds = storage.get(interaction.guild_id, "embeds", {})
        data = embeds.get(nume)
        if not data:
            return await interaction.response.send_message(
                f"Nu exista un embed numit `{nume}`.", ephemeral=True)
        await interaction.response.send_message(embed=build_embed(data, interaction.guild), ephemeral=True)

    @group.command(name="list", description="Lista embed-urilor salvate")
    async def list_embeds(self, interaction: discord.Interaction):
        embeds = storage.get(interaction.guild_id, "embeds", {})
        if not embeds:
            return await interaction.response.send_message(
                "Niciun embed salvat. Creeaza unul in dashboard.", ephemeral=True)
        names = "\n".join(f"• `{n}`" for n in embeds)
        await interaction.response.send_message(f"**Embed-uri salvate:**\n{names}", ephemeral=True)

    @group.command(name="delete", description="Sterge un embed salvat")
    @bot_access()
    async def delete(self, interaction: discord.Interaction, nume: str):
        embeds = storage.get(interaction.guild_id, "embeds", {})
        if nume not in embeds:
            return await interaction.response.send_message(
                f"Nu exista `{nume}`.", ephemeral=True)
        embeds.pop(nume)
        storage.set(interaction.guild_id, "embeds", embeds)
        await interaction.response.send_message(f"🗑️ Embed `{nume}` sters.", ephemeral=True)

    # autocomplete pentru numele embed-urilor la send/preview/delete
    @send.autocomplete("nume")
    @preview.autocomplete("nume")
    @delete.autocomplete("nume")
    async def _embed_names(self, interaction: discord.Interaction, current: str):
        embeds = storage.get(interaction.guild_id, "embeds", {})
        return [app_commands.Choice(name=n, value=n)
                for n in embeds if current.lower() in n.lower()][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(Embeds(bot))
