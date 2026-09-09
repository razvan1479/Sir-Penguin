"""
cogs/reminders.py — remindere complet configurabile din dashboard.

Nu are nicio comanda Discord — totul se face din dashboard: alegi canalul,
scrii detaliile, si alegi CAND sa se trimita:
  - relativ: peste X zile / ore / minute de acum
  - exact: la o data si ora anume (fus Europe/Bucharest)
  - repetitiv: la fiecare X ore, cu o ora fixa de start (ex. din 6 in 6 ore,
    incepand de la 06:00 — deci 06:00, 12:00, 18:00, 00:00...)

Poti avea oricate remindere active deodata. Un ceas in fundal (tasks.loop)
verifica la fiecare 20s daca a venit vremea vreunuia si il trimite. Cele
relative/exacte se sterg dupa ce sunt trimise (o singura data). Cele
repetitive NU se sterg — isi avanseaza ora de declansare cu exact intervalul
ales, ca sa nu se acumuleze intarziere in timp.

Storage (cheia "reminders", per guild): lista de
  {id, channel_id, message, trigger_ts, role_id, repeat_hours, created_ts}
trigger_ts e mereu un timestamp UTC (unix), indiferent cum a fost introdus
(relativ, data exacta sau repetitiv) — conversia se face in dashboard/app.py.
repeat_hours e None pentru remindere normale (o singura data), sau un numar
de ore pentru cele recurente.
"""
import discord
from discord.ext import commands, tasks

from utils import storage


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_loop.start()

    def cog_unload(self):
        self.check_loop.cancel()

    @tasks.loop(seconds=20)
    async def check_loop(self):
        await self._tick()

    async def _tick(self):
        """Un tur de verificare — separat de tasks.loop ca sa poata fi testat
        direct, fara sa depinda de mecanismul de scheduling al discord.py."""
        import time
        now = time.time()
        for guild in list(self.bot.guilds):
            reminders = storage.get(guild.id, "reminders", []) or []
            if not reminders:
                continue
            due = [r for r in reminders if r.get("trigger_ts", 0) <= now]
            if not due:
                continue
            remaining = [r for r in reminders if r.get("trigger_ts", 0) > now]
            for r in due:
                await self._send(guild, r)
                repeat_hours = r.get("repeat_hours")
                if repeat_hours:
                    # recurent: NU se sterge — avansam ora de declansare cu exact
                    # intervalul, ca sa nu se acumuleze intarziere (drift) in timp
                    r["trigger_ts"] = r.get("trigger_ts", now) + repeat_hours * 3600
                    remaining.append(r)
            # scoatem/pastram reminderele DUPA ce am incercat sa le trimitem pe
            # toate — daca botul repornise chiar in acest interval, mai bine
            # sa trimitem un reminder o data in plus decat sa-l pierdem
            storage.set(guild.id, "reminders", remaining)

    @check_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    async def _send(self, guild, r):
        channel = guild.get_channel(int(r.get("channel_id", 0)))
        if channel is None:
            return  # canalul a fost sters intre timp -> nu avem unde trimite
        content = r.get("message", "").strip() or "⏰ Reminder!"
        role_id = r.get("role_id")
        # mereu @everyone, plus rolul optional pe langa (daca a fost ales)
        ping = "@everyone" + (f" <@&{role_id}>" if role_id else "")
        embed = discord.Embed(title="⏰ Reminder", description=content,
                              color=discord.Color(0xF0B232))
        try:
            await channel.send(
                content=ping, embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=True, roles=True))
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminders(bot))
