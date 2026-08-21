"""
cogs/kingdoms.py — panou "Alege regatul": butoane personalizabile, fiecare da un rol.

- Un singur regat odata: daca alegi altul, se schimba (scoate vechiul rol, pune noul).
- Daca apesi regatul pe care il ai deja -> il parasesti (scoate rolul).
- Totul se configureaza din dashboard: titlu, descriere, si 1-5 optiuni
  (nume + emoji + culoare buton + rol).

Butoanele sunt persistente (merg si dupa restart) prin custom_id `kingdom:<role_id>`,
tratate in on_interaction (nu depind de un View static, fiindcă sunt dinamice).
"""

import re

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import bot_access

# cele 4 culori de buton permise de Discord (galben nu exista - se pune prin emoji)
STYLE_MAP = {
    "blurple": discord.ButtonStyle.primary,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "grey": discord.ButtonStyle.secondary,
}

# emoji custom de server: <:nume:123> sau <a:nume:123> (animat)
_CUSTOM_RE = re.compile(r"<(a?):([A-Za-z0-9_]+):(\d+)>")


def _parse_emoji(raw):
    """Accepta si emoji standard (🔴), si custom de server (<:nume:123>).
    Intoarce ceva ce merge pus pe un buton, sau None daca e gol."""
    if not raw:
        return None
    raw = raw.strip()
    m = _CUSTOM_RE.fullmatch(raw)
    if m:
        animated = bool(m.group(1))
        return discord.PartialEmoji(name=m.group(2), id=int(m.group(3)),
                                    animated=animated)
    return raw  # emoji standard (caracter unicode)


def _cfg(gid):
    return storage.get(gid, "kingdoms", {}) or {}


def _build_view(options):
    """Construieste panoul din optiunile configurate (dinamic)."""
    view = discord.ui.View(timeout=None)
    for opt in options:
        rid = opt.get("role_id")
        if not rid:
            continue
        style = STYLE_MAP.get(opt.get("style", "grey"), discord.ButtonStyle.secondary)
        try:
            emoji = _parse_emoji(opt.get("emoji"))
        except Exception:
            emoji = None  # emoji invalid -> butonul apare fara emoji, nu crapa
        btn = discord.ui.Button(
            label=opt.get("label", "Regat"),
            emoji=emoji,
            style=style,
            custom_id=f"kingdom:{rid}",
        )
        view.add_item(btn)
    return view


class Kingdoms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # butoanele sunt dinamice -> le tratam dupa custom_id (merge si dupa restart)
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("kingdom:"):
            return
        await self._choose(interaction, cid.split(":", 1)[1])

    async def _choose(self, interaction: discord.Interaction, role_id_str):
        guild = interaction.guild
        member = interaction.user
        cfg = _cfg(guild.id)
        options = cfg.get("options", [])
        # toate rolurile de regat configurate
        kingdom_roles = {str(o.get("role_id")) for o in options if o.get("role_id")}
        if role_id_str not in kingdom_roles:
            return await interaction.response.send_message(
                "Acest regat nu mai există în configurare.", ephemeral=True)

        chosen = guild.get_role(int(role_id_str))
        if chosen is None:
            return await interaction.response.send_message(
                "Rolul acestui regat nu mai există pe server. Anunță un admin.",
                ephemeral=True)

        # verificam ca botul poate da rolul (rolul lui trebuie sa fie mai sus)
        if chosen >= guild.me.top_role:
            return await interaction.response.send_message(
                "Nu pot atribui acest rol (e mai sus decât rolul meu). Anunță un admin.",
                ephemeral=True)

        # rolurile de regat pe care le are deja membrul
        current = [r for r in member.roles if str(r.id) in kingdom_roles]

        # daca apasa regatul pe care il ARE deja -> il paraseste
        if chosen in current:
            try:
                await member.remove_roles(chosen, reason="Alege regatul: parasit")
                return await interaction.response.send_message(
                    f"Ai părăsit regatul **{chosen.name}**.", ephemeral=True)
            except discord.HTTPException:
                return await interaction.response.send_message(
                    "Ceva n-a mers. Mai încearcă.", ephemeral=True)

        # altfel: scoate orice alt regat (unul singur odata) si pune-l pe cel ales
        try:
            to_remove = [r for r in current if r != chosen]
            if to_remove:
                await member.remove_roles(*to_remove, reason="Alege regatul: schimbare")
            await member.add_roles(chosen, reason="Alege regatul")
            await interaction.response.send_message(
                f"Ai intrat în regatul **{chosen.name}**!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "Nu am voie să-ți dau rolul. Anunță un admin (permisiuni).", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message(
                "Ceva n-a mers. Mai încearcă.", ephemeral=True)

    @app_commands.command(name="kingdom_panel",
                          description="Postează panoul „Alege regatul” în canalul curent")
    @bot_access()
    async def kingdom_panel(self, interaction: discord.Interaction):
        cfg = _cfg(interaction.guild_id)
        options = [o for o in cfg.get("options", []) if o.get("role_id")]
        if not options:
            return await interaction.response.send_message(
                "Întâi configurează regatele din dashboard (Distracție → Alege regatul).",
                ephemeral=True)
        embed = discord.Embed(
            title=cfg.get("title", "Alege regatul"),
            description=cfg.get("description",
                                "Apasă pe un buton ca să-ți alegi regatul. "
                                "Poți schimba oricând — dacă alegi altul, se schimbă."),
            color=discord.Color(0x8B5CF6))
        await interaction.response.send_message(embed=embed, view=_build_view(options))


async def setup(bot: commands.Bot):
    await bot.add_cog(Kingdoms(bot))
