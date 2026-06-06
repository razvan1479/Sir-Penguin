"""
cogs/rankup.py — RANK-URI AUTOMATE dupa vechimea pe server.

La fiecare 24h (si la pornire), verifica fiecare membru si:
  - ii actualizeaza nickname-ul cu emoji-ul de rank potrivit vechimii
  - ii da/scoate rolurile (rol "primar" pentru ranguri mici, rol "veteran"
    de la un anumit rang in sus)
  - logheaza schimbarile intr-un canal

Logica e identica cu botul original, dar TOT e configurabil din dashboard.

Setari (cheia "rankup" in storage):
{
  "enabled": false,
  "log_channel_id": 123,
  "ranks": ["🫡","⭐","⭐⭐","⭐⭐⭐","⚡","⚡⚡","⚡⚡⚡","✨"],
  "first_role_id": 111,        # rolul pt ranguri sub "ultimate" (ex: Rich Soul)
  "ultimate_role_id": 222,     # rolul de la "ultimate_rank_index" in sus (Veteran)
  "ultimate_rank_index": 4,    # de la al N-lea rang -> rol veteran
  "min_days": 30,              # sub atatea zile -> niciun rang
  "first_star_days": 180,      # sub atat (dar >= min_days) -> primul rang
  "interval_months": 6         # dupa primul rang, la fiecare X luni -> +1 rang
}
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone

from utils import storage
from utils.perms import bot_access

DEFAULT_RANKS = ["🫡", "⭐", "⭐⭐", "⭐⭐⭐", "⚡", "⚡⚡", "⚡⚡⚡", "✨"]


def _cfg(guild_id) -> dict:
    return storage.get(guild_id, "rankup", {}) or {}


def _ranks(cfg) -> list:
    r = cfg.get("ranks") or DEFAULT_RANKS
    return [x for x in r if x]


def base_nick(nick: str, ranks: list) -> str:
    """Numele fara emoji-ul de rank de la final."""
    for r in ranks:
        if nick.endswith(" " + r):
            return nick[:-(len(r) + 1)].rstrip()
    return nick


def current_rank(nick: str, ranks: list) -> int:
    """Indexul rangului curent din nickname, sau -1 daca nu are."""
    # verificam de la cel mai lung la cel mai scurt ca sa nu confundam ⚡ cu ⚡⚡
    for i in sorted(range(len(ranks)), key=lambda k: -len(ranks[k])):
        if nick.endswith(" " + ranks[i]):
            return i
    return -1


def correct_rank(days: int, ranks: list, cfg: dict) -> int:
    """Ce rang ar trebui sa aiba, in functie de zilele pe server."""
    min_days = int(cfg.get("min_days", 30))
    first_star_days = int(cfg.get("first_star_days", 180))
    interval_months = max(1, int(cfg.get("interval_months", 6)))
    months = days // 30

    if days < min_days:
        return -1
    if days < first_star_days:
        return 0
    base_months = first_star_days // 30
    additional = (months - base_months) // interval_months + 1
    return min(additional, len(ranks) - 1)


class RankUp(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rank_loop.start()

    def cog_unload(self):
        self.rank_loop.cancel()

    # ----------------------------------------------------- procesare guild
    async def process_guild(self, guild: discord.Guild) -> dict:
        """Aplica rangurile pe un server. Returneaza un mic raport."""
        cfg = _cfg(guild.id)
        if not cfg.get("enabled"):
            return {"skipped": True}

        ranks = _ranks(cfg)
        ult_index = int(cfg.get("ultimate_rank_index", 4))
        log_channel = guild.get_channel(int(cfg["log_channel_id"])) if cfg.get("log_channel_id") else None
        first_role = guild.get_role(int(cfg["first_role_id"])) if cfg.get("first_role_id") else None
        ultimate_role = guild.get_role(int(cfg["ultimate_role_id"])) if cfg.get("ultimate_role_id") else None

        now = datetime.now(timezone.utc)
        nick_changes = 0
        role_changes = 0

        for member in guild.members:
            try:
                if member.bot or member.joined_at is None:
                    continue

                days = (now - member.joined_at).days
                months = days // 30
                nick = member.nick or member.name
                base = base_nick(nick, ranks)
                cur = current_rank(nick, ranks)
                cor = correct_rank(days, ranks, cfg)

                # --- nickname ---
                if cur != cor:
                    new_nick = base if cor == -1 else f"{base} {ranks[cor]}"
                    if new_nick != nick:
                        try:
                            await member.edit(nick=new_nick)
                            nick_changes += 1
                            if log_channel:
                                old_s = ranks[cur] if cur != -1 else "fără rank"
                                new_s = ranks[cor] if cor != -1 else "fără rank"
                                await log_channel.send(
                                    f"🔹 {member.mention} a primit un nou rank: "
                                    f"**{old_s} → {new_s}** (vechime: {months} luni)")
                        except discord.Forbidden:
                            pass  # botul nu poate redenumi (owner / rol prea sus)

                # --- roluri ---
                if not (first_role or ultimate_role):
                    continue
                has_first = first_role in member.roles if first_role else False
                has_ult = ultimate_role in member.roles if ultimate_role else False
                changed = False
                msg = ""

                if 0 <= cor < ult_index:
                    if first_role and not has_first:
                        await member.add_roles(first_role); changed = True
                        msg = f"{member.mention} a primit rolul **{first_role.name}**"
                    if ultimate_role and has_ult:
                        await member.remove_roles(ultimate_role); changed = True
                        msg += f"\n{member.mention} i s-a scos rolul **{ultimate_role.name}**"
                elif cor >= ult_index:
                    if ultimate_role and not has_ult:
                        await member.add_roles(ultimate_role); changed = True
                        msg += f"{member.mention} a primit rolul **{ultimate_role.name}**"
                    if first_role and has_first:
                        await member.remove_roles(first_role); changed = True
                        msg += f"\n{member.mention} nu mai indeplineste conditiile pentru **{first_role.name}**"
                else:  # cor == -1
                    if first_role and has_first:
                        await member.remove_roles(first_role); changed = True
                        msg = f"{member.mention} avea **{first_role.name}** dar l-a pierdut"
                    if ultimate_role and has_ult:
                        await member.remove_roles(ultimate_role); changed = True
                        msg += f"\n{member.mention} avea **{ultimate_role.name}** dar l-a pierdut"

                if changed:
                    role_changes += 1
                    if log_channel and msg:
                        await log_channel.send(f"🔸 {msg}")

            except discord.Forbidden:
                continue
            except Exception as e:
                print(f"[rankup] Eroare la {member}: {e}")

        return {"nick_changes": nick_changes, "role_changes": role_changes,
                "members": len(guild.members)}

    # ----------------------------------------------------- loop 24h
    @tasks.loop(hours=24)
    async def rank_loop(self):
        for guild in self.bot.guilds:
            if _cfg(guild.id).get("enabled"):
                await self.process_guild(guild)

    @rank_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------------- comenzi
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
            f"✓ Gata! Verificat {report.get('members', 0)} membri — "
            f"{report.get('nick_changes', 0)} nickname-uri si "
            f"{report.get('role_changes', 0)} roluri actualizate.", ephemeral=True)

    @rankup.command(name="status", description="Vezi configuratia rank-urilor")
    async def status(self, interaction: discord.Interaction):
        cfg = _cfg(interaction.guild_id)
        if not cfg:
            return await interaction.response.send_message(
                "Rank-urile nu sunt configurate inca. Mergi pe dashboard.", ephemeral=True)
        ranks = _ranks(cfg)
        e = discord.Embed(title="⏫ Rank-uri automate",
                          color=discord.Color.blurple())
        e.add_field(name="Status", value="✅ Activ" if cfg.get("enabled") else "⛔ Inactiv", inline=False)
        e.add_field(name="Ranguri", value=" ".join(ranks), inline=False)
        e.add_field(name="Primul rang dupa", value=f"{cfg.get('min_days', 30)} zile", inline=True)
        e.add_field(name="Prima stea dupa", value=f"{cfg.get('first_star_days', 180)} zile", inline=True)
        e.add_field(name="Interval avansare", value=f"{cfg.get('interval_months', 6)} luni", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RankUp(bot))
