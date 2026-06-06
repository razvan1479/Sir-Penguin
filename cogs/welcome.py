"""
cogs/welcome.py — modulul de BUN VENIT.

Cand intra cineva pe server, trimite un embed cu:
  - mesaj configurabil (placeholdere: {user}, {username}, {server}, {count})
  - poza de profil (avatar) ca thumbnail
  - bannerul userului ca imagine mare
  - OPTIONAL: cine l-a invitat (daca modulul "invites" e activ)

CUM SE LEAGA DE INVITES (ca sa nu trimita mesajul de doua ori):
  - Daca modulul "invites" e incarcat, ACELA detecteaza cine a invitat si
    trimite un eveniment custom "invite_join". Welcome asculta evenimentul
    si trimite mesajul CU sursa invitatiei.
  - Daca "invites" NU e incarcat, welcome reactioneaza direct la on_member_join
    si trimite mesajul simplu (fara info de invitator).
Asa, exact UN singur mesaj e trimis, indiferent de configuratie.

Setari (cheia "welcome"):
{
  "enabled": true, "channel_id": 123, "message": "...",
  "show_avatar": true, "show_banner": true, "show_inviter": true,
  "color": "#5865f2"
}
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import bot_access


def _color_from_hex(value: str) -> discord.Color:
    try:
        return discord.Color(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Color.blurple()


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _welcome_text(self, member, cfg):
        raw = cfg.get("message") or "Bun venit {user} pe {server}!"
        return (raw
                .replace("{user}", member.mention)
                .replace("{username}", member.display_name)
                .replace("{server}", member.guild.name)
                .replace("{count}", str(member.guild.member_count)))

    async def _build_embed(self, member, cfg, inviter_info=None):
        # Textul de bun venit (cu tagul) sta IN embed. Nu mai trimitem o
        # mentiune separata deasupra -> tagul apare o singura data, in mesaj.
        text = self._welcome_text(member, cfg)
        embed = discord.Embed(description=text, color=_color_from_hex(cfg.get("color", "#5865f2")))
        embed.set_author(name=f"{member.display_name} a intrat!", icon_url=member.display_avatar.url)

        if cfg.get("show_avatar", True):
            embed.set_thumbnail(url=member.display_avatar.url)

        if cfg.get("show_banner", True):
            try:
                full_user = await self.bot.fetch_user(member.id)
                if full_user.banner:
                    embed.set_image(url=full_user.banner.url)
            except discord.HTTPException:
                pass

        # linia cu sursa invitatiei
        if cfg.get("show_inviter", True) and inviter_info:
            t = inviter_info.get("type")
            if t == "personal" and inviter_info.get("inviter_id"):
                from cogs.invites import invite_total
                members = storage.get(member.guild.id, "invites", {}).get("members", {})
                total = invite_total(members.get(str(inviter_info["inviter_id"]), {}))
                embed.add_field(
                    name="📨 Invitat de",
                    value=f"<@{inviter_info['inviter_id']}> (acum are **{total}** invitatii)",
                    inline=False)
            elif t == "vanity":
                embed.add_field(name="🔗 A intrat prin",
                                value="linkul personalizat al serverului", inline=False)
            else:
                embed.add_field(name="❔ Sursa invitatiei",
                                value="nu a putut fi determinata", inline=False)

        return embed

    async def _send_welcome(self, member, inviter_info=None):
        cfg = storage.get(member.guild.id, "welcome", {})
        if not cfg.get("enabled") or not cfg.get("channel_id"):
            return
        channel = member.guild.get_channel(int(cfg["channel_id"]))
        if channel is None:
            return
        embed = await self._build_embed(member, cfg, inviter_info)
        # fara "content" -> nu mai apare tagul separat deasupra embedului
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # daca modulul invites e activ, el va trimite welcome-ul (cu invitator)
        if self.bot.get_cog("Invites"):
            return
        await self._send_welcome(member, None)

    @commands.Cog.listener()
    async def on_invite_join(self, member, inviter_info):
        # eveniment custom trimis de cogs/invites.py
        await self._send_welcome(member, inviter_info)

    # --- comenzi ---
    group = app_commands.Group(name="welcome", description="Setari mesaj de bun venit")

    @group.command(name="test", description="Trimite un mesaj de test")
    @bot_access()
    async def test(self, interaction):
        cfg = storage.get(interaction.guild_id, "welcome", {})
        if not cfg.get("enabled"):
            return await interaction.response.send_message(
                "Welcome e dezactivat. Activeaza-l din dashboard.", ephemeral=True)
        fake = {"type": "personal", "inviter_id": interaction.user.id, "inviter_name": str(interaction.user)}
        embed = await self._build_embed(interaction.user, cfg, fake)
        await interaction.response.send_message(embed=embed)

    @group.command(name="channel", description="Seteaza rapid canalul de bun venit")
    @bot_access()
    async def channel(self, interaction, canal: discord.TextChannel):
        cfg = storage.get(interaction.guild_id, "welcome", {})
        cfg["channel_id"] = canal.id
        cfg.setdefault("enabled", True)
        storage.set(interaction.guild_id, "welcome", cfg)
        await interaction.response.send_message(f"Canal setat: {canal.mention}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Welcome(bot))
