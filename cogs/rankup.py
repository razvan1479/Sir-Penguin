"""
cogs/rankup.py — RANK-URI AUTOMATE dupa vechimea pe server (complet configurabil).

Definesti CATE trepte vrei. Fiecare treapta are:
  - days   : de la cate zile pe server se obtine
  - emoji  : ce se pune la finalul nickname-ului (optional)
  - role_id: ce rol primeste (optional)

Un membru primeste rolul + emoji-ul celei mai inalte trepte la care a ajuns, si
pierde rolul treptei anterioare (promovare). La fiecare 24h si la pornire.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone

from utils import storage
from utils.perms import bot_access


def _cfg(guild_id) -> dict:
    return storage.get(guild_id, "rankup", {}) or {}


def _tiers(cfg) -> list:
    """Treptele, sortate crescator dupa zile. Doar cele valide."""
    tiers = []
    for t in cfg.get("tiers", []) or []:
        try:
            days = int(t.get("days"))
        except (TypeError, ValueError):
            continue
        tiers.append({"days": days, "emoji": (t.get("emoji") or "").strip(),
                      "role_id": t.get("role_id")})
    return sorted(tiers, key=lambda x: x["days"])


def _emojis(tiers) -> list:
    return [t["emoji"] for t in tiers if t["emoji"]]


def base_nick(nick: str, emojis: list) -> str:
    """Numele fara emoji-ul de rank de la final (verifica cel mai lung intai)."""
    for e in sorted(emojis, key=len, reverse=True):
        if e and nick.endswith(" " + e):
            return nick[:-(len(e) + 1)].rstrip()
    return nick


def current_tier(nick: str, tiers: list) -> int:
    """Indexul treptei dupa emoji-ul din nickname, sau -1."""
    order = sorted(range(len(tiers)), key=lambda k: -len(tiers[k]["emoji"]))
    for i in order:
        e = tiers[i]["emoji"]
        if e and nick.endswith(" " + e):
            return i
    return -1


def correct_tier(days: int, tiers: list) -> int:
    """Cea mai inalta treapta la care ajunge cu `days` zile, sau -1."""
    idx = -1
    for i, t in enumerate(tiers):  # sortate crescator
        if days >= t["days"]:
            idx = i
        else:
            break
    return idx


class RankUp(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rank_loop.start()

    def cog_unload(self):
        self.rank_loop.cancel()

    async def process_guild(self, guild: discord.Guild) -> dict:
        cfg = _cfg(guild.id)
        if not cfg.get("enabled"):
            return {"skipped": True}

        tiers = _tiers(cfg)
        if not tiers:
            return {"nick_changes": 0, "role_changes": 0, "members": len(guild.members)}
        emojis = _emojis(tiers)
        log_channel = guild.get_channel(int(cfg["log_channel_id"])) if cfg.get("log_channel_id") else None

        tier_role_ids = {str(t["role_id"]) for t in tiers if t.get("role_id")}

        now = datetime.now(timezone.utc)
        nick_changes = role_changes = 0

        for member in guild.members:
            try:
                if member.bot or member.joined_at is None:
                    continue
                days = (now - member.joined_at).days
                nick = member.nick or member.name
                base = base_nick(nick, emojis)
                cur = current_tier(nick, tiers)
                cor = correct_tier(days, tiers)

                # --- nickname (emoji treptei) ---
                if cur != cor:
                    emoji = tiers[cor]["emoji"] if cor != -1 else ""
                    new_nick = f"{base} {emoji}".strip() if emoji else base
                    if new_nick != nick:
                        try:
                            await member.edit(nick=new_nick)
                            nick_changes += 1
                            if log_channel:
                                old_s = tiers[cur]["emoji"] if cur != -1 else "fara rank"
                                new_s = tiers[cor]["emoji"] if cor != -1 else "fara rank"
                                await log_channel.send(
                                    f"🔹 {member.mention} a avansat: **{old_s or '-'} -> {new_s or '-'}** "
                                    f"(vechime: {days} zile)")
                        except discord.Forbidden:
                            pass

                # --- roluri: pastram DOAR rolul treptei curente ---
                target = str(tiers[cor]["role_id"]) if (cor != -1 and tiers[cor].get("role_id")) else None
                changed = False
                for rid in tier_role_ids:
                    if rid == target:
                        continue
                    role = guild.get_role(int(rid))
                    if role and role in member.roles:
                        await member.remove_roles(role)
                        changed = True
                if target:
                    role = guild.get_role(int(target))
                    if role and role not in member.roles:
                        await member.add_roles(role)
                        changed = True
                        if log_channel:
                            await log_channel.send(f"🔸 {member.mention} a primit rolul **{role.name}**")

                if changed:
                    role_changes += 1

            except discord.Forbidden:
                continue
            except Exception as e:
                print(f"[rankup] Eroare la {member}: {e}")

        return {"nick_changes": nick_changes, "role_changes": role_changes,
                "members": len(guild.members)}

    @tasks.loop(hours=24)
    async def rank_loop(self):
        for guild in self.bot.guilds:
            if _cfg(guild.id).get("enabled"):
                await self.process_guild(guild)

    @rank_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    rankup = app_commands.Group(name="rankup", description="Rank-uri automate dupa vechime")

    @rankup.command(name="run", description="Aplica acum rangurile pe tot serverul")
    @bot_access()
    async def run_now(self, interaction: discord.Interaction):
        cfg = _cfg(interaction.guild_id)
        if not cfg.get("enabled"):
            return await interaction.response.send_message(
                "Modulul de rank-uri nu e activat. Activeaza-l din dashboard.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        report = await self.process_guild(interaction.guild)
        await interaction.followup.send(
            f"✓ Gata! Verificat {report.get('members', 0)} membri - "
            f"{report.get('nick_changes', 0)} nickname-uri si "
            f"{report.get('role_changes', 0)} roluri actualizate.", ephemeral=True)

    @rankup.command(name="status", description="Vezi configuratia rank-urilor")
    async def status(self, interaction: discord.Interaction):
        cfg = _cfg(interaction.guild_id)
        tiers = _tiers(cfg)
        if not tiers:
            return await interaction.response.send_message(
                "Nu sunt configurate trepte de rank. Mergi pe dashboard.", ephemeral=True)
        e = discord.Embed(title="⏫ Rank-uri automate", color=discord.Color.blurple())
        e.add_field(name="Status", value="✅ Activ" if cfg.get("enabled") else "⛔ Inactiv", inline=False)
        lines = []
        for t in tiers:
            role = interaction.guild.get_role(int(t["role_id"])) if t.get("role_id") else None
            role_txt = f" -> {role.mention}" if role else ""
            lines.append(f"{t['emoji'] or '-'} de la **{t['days']}** zile{role_txt}")
        e.add_field(name=f"Trepte ({len(tiers)})", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RankUp(bot))
