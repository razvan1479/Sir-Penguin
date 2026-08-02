"""
cogs/newaccounts.py — depistarea conturilor NOI de Discord.

Varsta contului se calculeaza din ID-ul de Discord (cand a fost creat contul).
Configurare completa din dashboard:
  - prag (sub cate zile = cont nou)
  - rol automat (ales de tine) pus la conturile noi cand intra
  - anunt pe un canal cand intra un cont nou
  - comanda /conturinoi care listeaza toate conturile noi de pe server

Cheia de config "newacc":
{
  "enabled": true,
  "days": 30,
  "role_id": 123,          # rolul pe care il pui (ales din dashboard)
  "announce_channel_id": 456,
  "announce_enabled": true
}
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import bot_access


def _cfg(gid):
    return storage.get(gid, "newacc", {}) or {}


def _account_age_days(member: discord.abc.Snowflake) -> float:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - member.created_at).total_seconds() / 86400.0


class NewAccounts(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = _cfg(member.guild.id)
        if not cfg.get("enabled"):
            return
        days = int(cfg.get("days", 30))
        if _account_age_days(member) >= days:
            return  # contul nu e nou

        # 1) rol automat (daca e ales unul din dashboard)
        role_id = cfg.get("role_id")
        if role_id:
            role = member.guild.get_role(int(role_id))
            if role:
                try:
                    await member.add_roles(role, reason="Cont nou (sub pragul setat)")
                except discord.Forbidden:
                    pass

        # 2) anunt pe canal
        if cfg.get("announce_enabled") and cfg.get("announce_channel_id"):
            ch = member.guild.get_channel(int(cfg["announce_channel_id"]))
            if ch:
                age = _account_age_days(member)
                created = discord.utils.format_dt(member.created_at, style="R")
                embed = discord.Embed(
                    description=(f"🆕 **Cont nou** a intrat pe server\n"
                                 f"[{member}](https://discord.com/users/{member.id})\n"
                                 f"Cont creat {created} (acum ~{int(age)} zile)"),
                    color=discord.Color(0xF1C40F))
                try:
                    await ch.send(embed=embed)
                except discord.HTTPException:
                    pass

    group = app_commands.Group(name="conturinoi",
                               description="Conturi de Discord create recent")

    @group.command(name="lista",
                   description="Arata membrii cu cont creat sub pragul setat")
    @app_commands.describe(zile="Optional: alt prag doar pentru aceasta cautare (in zile)")
    @bot_access()
    async def lista(self, interaction: discord.Interaction, zile: int = None):
        await interaction.response.defer(ephemeral=True)
        cfg = _cfg(interaction.guild_id)
        days = int(zile if zile else cfg.get("days", 30))

        noi = []
        for m in interaction.guild.members:
            if m.bot:
                continue
            age = _account_age_days(m)
            if age < days:
                noi.append((m, age))
        noi.sort(key=lambda x: x[1])  # cele mai noi primele

        if not noi:
            return await interaction.followup.send(
                f"Niciun membru cu cont sub {days} zile. ✅", ephemeral=True)

        lines = []
        for m, age in noi[:40]:
            created = discord.utils.format_dt(m.created_at, style="R")
            lines.append(f"• {m.mention} — creat {created} (~{int(age)} zile)")
        extra = f"\n\n…și încă {len(noi) - 40}." if len(noi) > 40 else ""
        embed = discord.Embed(
            title=f"🆕 Conturi noi (sub {days} zile): {len(noi)}",
            description="\n".join(lines) + extra,
            color=discord.Color(0xF1C40F))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="verifica",
                   description="Verifica vechimea contului unui membru")
    @app_commands.describe(membru="Membrul de verificat")
    @bot_access()
    async def verifica(self, interaction: discord.Interaction, membru: discord.Member):
        age = _account_age_days(membru)
        days = int(_cfg(interaction.guild_id).get("days", 30))
        nou = "🆕 DA, e cont nou" if age < days else "✅ Nu, e cont vechi"
        created = discord.utils.format_dt(membru.created_at, style="F")
        rel = discord.utils.format_dt(membru.created_at, style="R")
        await interaction.response.send_message(
            f"**{membru}**\nCont creat: {created} ({rel})\n"
            f"Vechime: ~{int(age)} zile\nSub pragul de {days} zile? {nou}",
            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(NewAccounts(bot))
