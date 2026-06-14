"""
cogs/admin.py — comenzi de administrare, DOAR pentru owner-ul botului.

/serverlist  - listeaza toate serverele pe care e botul (nume, ID, membri)
/leaveserver - scoate botul de pe un server, dupa ID (util cand nu ai acces la el)
"""

import discord
from discord import app_commands
from discord.ext import commands


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="serverlist", description="(owner) Lista serverelor pe care e botul")
    async def serverlist(self, interaction: discord.Interaction):
        app = await self.bot.application_info()
        if interaction.user.id != app.owner.id:
            return await interaction.response.send_message("Doar owner-ul botului poate folosi asta.", ephemeral=True)

        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        lines = []
        for g in guilds:
            owner = f" · owner: {g.owner}" if g.owner else ""
            lines.append(f"**{g.name}**\n`{g.id}` · {g.member_count} membri{owner}")
        text = f"Botul e pe **{len(guilds)} servere**:\n\n" + "\n\n".join(lines)
        # in caz ca e prea lung pentru un mesaj
        if len(text) > 1900:
            text = text[:1900] + "\n…"
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="leaveserver", description="(owner) Scoate botul de pe un server, dupa ID")
    @app_commands.describe(server_id="ID-ul serverului de pe care iese botul")
    async def leaveserver(self, interaction: discord.Interaction, server_id: str):
        app = await self.bot.application_info()
        if interaction.user.id != app.owner.id:
            return await interaction.response.send_message("Doar owner-ul botului poate folosi asta.", ephemeral=True)

        if not server_id.isdigit():
            return await interaction.response.send_message("ID invalid. Da un ID numeric (din /serverlist).", ephemeral=True)

        guild = self.bot.get_guild(int(server_id))
        if guild is None:
            return await interaction.response.send_message(
                "Nu sunt pe niciun server cu acest ID. Verifica in /serverlist.", ephemeral=True)

        name = guild.name
        try:
            await guild.leave()
        except discord.HTTPException:
            return await interaction.response.send_message(
                f"Nu am putut iesi de pe **{name}**. Incearca din nou.", ephemeral=True)
        await interaction.response.send_message(f"✅ Am iesit de pe serverul **{name}** (`{server_id}`).", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
