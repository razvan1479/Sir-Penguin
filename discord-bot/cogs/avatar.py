"""
cogs/avatar.py — comenzi /avatar si /banner.

Arata avatarul sau bannerul unui user, cu linkuri de descarcare in mai multe
formate (PNG, JPG, WEBP si GIF daca e animat).

Comenzi:
  /avatar [user]   - avatarul (al tau daca nu specifici pe cineva)
  /banner [user]   - bannerul
"""

import discord
from discord import app_commands
from discord.ext import commands


def _download_links(asset: discord.Asset) -> str:
    """Construieste linkuri de descarcare in toate formatele disponibile."""
    formats = ["png", "jpg", "webp"]
    if asset.is_animated():
        formats.append("gif")
    links = []
    for fmt in formats:
        try:
            url = asset.replace(format=fmt, size=1024).url
            links.append(f"[{fmt.upper()}]({url})")
        except (ValueError, TypeError):
            pass
    return " · ".join(links)


class AvatarCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="avatar", description="Arata avatarul unui user (cu linkuri de descarcare)")
    @app_commands.describe(user="Userul (gol = tu)")
    async def avatar(self, interaction: discord.Interaction, user: discord.User = None):
        user = user or interaction.user
        asset = user.display_avatar  # avatarul de server daca exista, altfel cel global

        embed = discord.Embed(
            title=f"🖼️ Avatarul lui {user.display_name}",
            description="📥 **Descarca:** " + _download_links(asset),
            color=discord.Color.blurple(),
        )
        embed.set_image(url=asset.replace(size=1024).url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="banner", description="Arata bannerul unui user (cu linkuri de descarcare)")
    @app_commands.describe(user="Userul (gol = tu)")
    async def banner(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        # bannerul nu vine in cache -> trebuie cerut separat
        try:
            full = await self.bot.fetch_user(target.id)
        except discord.HTTPException:
            return await interaction.response.send_message(
                "Nu am putut prelua userul. Incearca din nou.", ephemeral=True)

        if not full.banner:
            return await interaction.response.send_message(
                f"**{target.display_name}** nu are banner. 🤷", ephemeral=True)

        asset = full.banner
        embed = discord.Embed(
            title=f"🎨 Bannerul lui {target.display_name}",
            description="📥 **Descarca:** " + _download_links(asset),
            color=discord.Color.blurple(),
        )
        embed.set_image(url=asset.replace(size=1024).url)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(AvatarCog(bot))
