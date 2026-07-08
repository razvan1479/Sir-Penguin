"""
cogs/goodbye.py — modulul de RAMAS BUN (cand cineva iese de pe server).

Trimite un mesaj configurabil pe un canal ales, de fiecare data cand pleaca
cineva. Totul se personalizeaza din dashboard.

Setari (cheia "goodbye"):
{
  "enabled": true, "channel_id": 123,
  "message": "{user} a parasit serverul. Ramanem {count} membri.",
  "title": "👋 La revedere",
  "show_avatar": true,
  "use_embed": true,
  "color": "#ed4245"
}

Placeholdere in mesaj/titlu: {user} {username} {server} {count}
"""

import discord
from discord.ext import commands

from utils import storage


def _color_from_hex(value: str) -> discord.Color:
    try:
        return discord.Color(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Color.red()


class Goodbye(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _inviter_str(self, member):
        """Cine a invitat persoana (din datele de invitatii), formatat ca pe jurnal."""
        data = storage.get(member.guild.id, "invites", {}) or {}
        inviter_key = None
        inviter_name = None
        # cautam in joined_by sau in istoric
        rec = data.get("joined_by", {}).get(str(member.id))
        if rec:
            inviter_key = rec.get("inviter")
        for e in reversed(data.get("history", [])):
            if e.get("member") == str(member.id):
                inviter_key = inviter_key or e.get("inviter")
                inviter_name = e.get("inviter_name")
                break
        if inviter_key == "vanity":
            return "🔗 link vanity"
        if not inviter_key or inviter_key == "unknown" or not inviter_name:
            return "❓ necunoscut"
        return inviter_name

    def _fill(self, text, member, as_link=False):
        if as_link and hasattr(member, "id"):
            # link clickabil spre profil (merge in embed, pe orice dispozitiv, chiar daca a plecat)
            user_str = f"[{member}](https://discord.com/users/{member.id})"
        else:
            user_str = str(member)
        return (str(text or "")
                .replace("{user}", user_str)
                .replace("{username}", str(member))
                .replace("{server}", member.guild.name)
                .replace("{count}", str(member.guild.member_count))
                .replace("{inviter}", self._inviter_str(member)))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        cfg = storage.get(member.guild.id, "goodbye", {}) or {}
        if not cfg.get("enabled") or not cfg.get("channel_id"):
            return
        channel = member.guild.get_channel(int(cfg["channel_id"]))
        if channel is None:
            return

        use_embed = cfg.get("use_embed", True)
        message = self._fill(cfg.get("message") or "{user} a plecat · invitat de {inviter}",
                             member, as_link=use_embed)

        try:
            if cfg.get("use_embed", True):
                embed = discord.Embed(
                    description=message,
                    color=_color_from_hex(cfg.get("color", "#ed4245")))
                title = self._fill(cfg.get("title") or "👋 La revedere", member)
                if title:
                    embed.set_author(name=title,
                                     icon_url=member.display_avatar.url if cfg.get("show_avatar", True) else None)
                if cfg.get("show_avatar", True):
                    embed.set_thumbnail(url=member.display_avatar.url)
                embed.timestamp = discord.utils.utcnow()
                await channel.send(embed=embed)
            else:
                await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Goodbye(bot))
