"""
cogs/admin.py — comenzi de administrare, DOAR pentru owner-ul botului.

/serverlist  - listeaza toate serverele pe care e botul (nume, ID, membri)
/leaveserver - scoate botul de pe un server, dupa ID (util cand nu ai acces la el)
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sync_loop.start()
        self.leave_loop.start()

    def cog_unload(self):
        self.sync_loop.cancel()
        self.leave_loop.cancel()

    # scrie lista serverelor pentru dashboard
    @tasks.loop(seconds=20)
    async def sync_loop(self):
        # salvam o singura data ID-ul owner-ului botului (pt. verificari in dashboard)
        if not getattr(self, "_owner_saved", False):
            try:
                app = await self.bot.application_info()
                storage.set(0, "bot_owner_id", str(app.owner.id))
                self._owner_saved = True
            except discord.HTTPException:
                pass
        servers = []
        for g in sorted(self.bot.guilds, key=lambda x: x.member_count or 0, reverse=True):
            servers.append({"id": str(g.id), "name": g.name,
                            "members": g.member_count,
                            "icon": g.icon.url if g.icon else None,
                            "owner": str(g.owner) if g.owner else None})
        storage.set(0, "bot_servers", servers)

    @sync_loop.before_loop
    async def _bsync(self):
        await self.bot.wait_until_ready()

    # executa cererile de "leave" venite din dashboard
    @tasks.loop(seconds=8)
    async def leave_loop(self):
        job = storage.get(0, "leave_request", None)
        if not job or job.get("status") != "pending":
            return
        gid = job.get("guild_id")
        guild = self.bot.get_guild(int(gid)) if gid and str(gid).isdigit() else None
        if guild is None:
            job.update(status="error", result="Nu sunt pe niciun server cu acest ID.")
            storage.set(0, "leave_request", job)
            return
        name = guild.name
        try:
            await guild.leave()
            job.update(status="done", result=f"Am iesit de pe serverul {name}.")
        except discord.HTTPException:
            job.update(status="error", result=f"Nu am putut iesi de pe {name}.")
        storage.set(0, "leave_request", job)
        await self.sync_loop()  # reimprospatam lista

    @leave_loop.before_loop
    async def _lsync(self):
        await self.bot.wait_until_ready()

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
