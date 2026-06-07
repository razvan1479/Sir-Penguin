"""
cogs/dashboard_sync.py — tine la zi informatiile despre servere, pentru dashboard.

Botul stie numele, iconita si numarul de membri ale fiecarui server in care e.
Le scrie in store.json (cheia "meta"), iar dashboardul le citeste si le afiseaza
ca niste carduri vizuale (poza serverului + membri).

Se actualizeaza la pornire, cand intra/iese cineva si cand se schimba serverul.
"""

import time

import discord
from discord.ext import commands

from utils import storage


class DashboardSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _save(self, guild: discord.Guild):
        if guild is None:
            return
        storage.set(guild.id, "meta", {
            "name": guild.name,
            "icon": guild.icon.url if guild.icon else None,
            "members": guild.member_count,
            "updated": time.time(),
        })

    def _save_channels(self, guild: discord.Guild):
        """Scrie categoriile si canalele text, pentru dropdown-urile din dashboard."""
        if guild is None:
            return
        cats, texts = [], []
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel):
                cats.append({"id": str(ch.id), "name": ch.name})
            elif isinstance(ch, discord.TextChannel):
                texts.append({"id": str(ch.id), "name": ch.name,
                              "category": str(ch.category_id) if ch.category_id else None})
        storage.set(guild.id, "channels", {"categories": cats, "texts": texts})

    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.bot.guilds:
            self._save(g)
            self._save_channels(g)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        self._save(guild)
        self._save_channels(guild)

    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        self._save(after)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        self._save_channels(channel.guild)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        self._save_channels(channel.guild)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        self._save_channels(after.guild)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        self._save(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self._save(member.guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(DashboardSync(bot))
