"""
cogs/giveaway.py — GIVEAWAY cu buton de inscriere.

Posteaza un embed cu un buton; cine apasa intra in tragere. La final, botul
alege castigatorii automat. Totul e configurabil din dashboard.

TIPURI DE PROGRAMARE (cfg["sched_mode"]):
  - "duration" : postezi acum (/giveaway_start sau /giveaway) -> tine X minute.
  - "exact"    : postezi acum -> se termina la DATA + ORA exacta aleasa.
  - "interval" : RECURENT la fiecare N ore, ancorat la o ora; fiecare tine X minute.
  - "weekly"   : RECURENT saptamanal -> incepe [zi] la [ora] si se termina
                 [zi] la [ora], apoi se repeta in fiecare saptamana
                 (ex: Joi 20:00 -> Duminica 22:00).

Retrocompatibil: daca lipseste "sched_mode" dar exista vechiul cfg["recurring"]=True,
se comporta ca "interval".

CUM functioneaza tehnic:
  - butonul e "persistent" (custom_id fix) -> merge si dupa restart
  - un ceas in fundal (tasks.loop) verifica la fiecare 30s ce giveaway-uri
    expira (le incheie + alege castigatori) si daca e cazul posteaza unul nou.

Date salvate (cheia "giveaways"):
{
  "config": {channel_id, prize, winners, button_label, title, color,
             sched_mode, duration_minutes, interval_hours, anchor_time,
             end_date, end_time, start_weekday, start_time, end_weekday,
             ping_everyone, required_role_id},
  "active": { "<message_id>": {channel_id, prize, end_ts, winners, participants:[], ...} },
  "ended":  { "<message_id>": {..., participants:[]} },   # pentru reroll
  "next_post_ts": <unix>                                   # urmatoarea postare recurenta
}

Comenzi (admin):
  /giveaway                    - deschide panoul (modal) ca sa faci unul rapid din Discord
  /giveaway_start              - posteaza acum un giveaway (foloseste config din dashboard)
  /giveaway_end <message_id>   - incheie acum un giveaway
  /giveaway_reroll <message_id>- alege alt castigator pentru un giveaway incheiat
"""

import time
import random
import asyncio
import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage
from utils.perms import bot_access


TZ = ZoneInfo("Europe/Bucharest")
WEEKDAYS_RO = ["Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă", "Duminică"]


def _color_from_hex(value: str) -> discord.Color:
    try:
        return discord.Color(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Color.blurple()


def _parse_hhmm(value, default=(12, 0)):
    """'20:30' -> (20, 30). Pe eroare -> default."""
    try:
        hh, mm = str(value).split(":")
        return int(hh), int(mm)
    except (ValueError, AttributeError):
        return default


def _next_weekday_time(now_dt, weekday, hh, mm):
    """Urmatorul moment (strict dupa now_dt) care cade pe `weekday` la hh:mm."""
    days_ahead = (weekday - now_dt.weekday()) % 7
    cand = (now_dt + datetime.timedelta(days=days_ahead)).replace(
        hour=hh, minute=mm, second=0, microsecond=0)
    while cand <= now_dt:
        cand += datetime.timedelta(days=7)
    return cand


def _end_after(start_dt, weekday, hh, mm):
    """Primul moment pe `weekday` la hh:mm care e strict DUPA start_dt.
    Ex: start Joi 20:00, end Duminica 22:00 -> aceeasi saptamana.
    Daca ziua/ora de final ar cadea inainte de start -> saptamana urmatoare."""
    days_ahead = (weekday - start_dt.weekday()) % 7
    cand = (start_dt + datetime.timedelta(days=days_ahead)).replace(
        hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= start_dt:
        cand += datetime.timedelta(days=7)
    return cand


def _sched_mode(cfg):
    """Modul de programare, cu retrocompatibilitate pt. vechiul flag 'recurring'."""
    mode = cfg.get("sched_mode")
    if mode:
        return mode
    return "interval" if cfg.get("recurring") else "duration"


class GiveawayView(discord.ui.View):
    """Butonul de inscriere. custom_id fix -> persistent (merge si dupa restart)."""

    def __init__(self, label: str = "🎉 Particip"):
        super().__init__(timeout=None)
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.green,
                                custom_id="giveaway_join")
        btn.callback = self.on_join
        self.add_item(btn)

    async def on_join(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        mid = str(interaction.message.id)
        data = storage.get(gid, "giveaways", {})
        active = data.get("active", {})
        gw = active.get(mid)
        if not gw:
            return await interaction.response.send_message(
                "Acest giveaway s-a incheiat.", ephemeral=True)

        # timpul a expirat -> nu mai acceptam inscrieri (counter-ul se opreste)
        if time.time() >= gw.get("end_ts", 0):
            return await interaction.response.send_message(
                "⏰ Giveaway-ul s-a terminat — castigatorul se anunta imediat.", ephemeral=True)

        # daca giveaway-ul e doar pentru un rol, verificam ca userul il are
        required = gw.get("required_role_id")
        if required:
            role_ids = [r.id for r in getattr(interaction.user, "roles", [])]
            if int(required) not in role_ids:
                return await interaction.response.send_message(
                    f"Acest giveaway e doar pentru <@&{required}>. Nu ai rolul necesar.",
                    ephemeral=True)

        parts = set(gw.get("participants", []))
        if interaction.user.id in parts:
            parts.discard(interaction.user.id)
            msg = "Ai iesit din giveaway."
        else:
            parts.add(interaction.user.id)
            msg = f"Participi! 🎉 ({len(parts)} participanti)"

        gw["participants"] = list(parts)
        active[mid] = gw
        data["active"] = active
        storage.set(gid, "giveaways", data)
        await interaction.response.send_message(msg, ephemeral=True)

        # actualizam counter-ul live pe embed
        try:
            if interaction.message.embeds:
                e = interaction.message.embeds[0]
                e.set_footer(text=f"👥 {len(parts)} participanti")
                await interaction.message.edit(embed=e)
        except discord.HTTPException:
            pass


def _fmt_duration(minutes):
    if minutes % 1440 == 0 and minutes >= 1440:
        return f"{minutes // 1440} zile"
    if minutes % 60 == 0 and minutes >= 60:
        return f"{minutes // 60} ore"
    return f"{minutes} minute"


class GiveawayModal(discord.ui.Modal, title="🎉 Creează un giveaway"):
    """Un singur formular cu tot ce trebuie: titlu, premiu, castigatori, durata, canal.

    NOTA: Discord limiteaza modalul la 5 campuri, asa ca panoul rapid din Discord
    ramane pe durata (minute). Programarile exacte/saptamanale se fac din dashboard."""

    def __init__(self, cog, default_channel_id):
        super().__init__()
        self.cog = cog
        self.default_channel_id = default_channel_id
        self.titlu = discord.ui.TextInput(
            label="Titlu", default="🎉 GIVEAWAY 🎉",
            max_length=100, required=True)
        self.premiu = discord.ui.TextInput(
            label="Premiu (ce se câștigă)",
            placeholder="ex: Discord Nitro", max_length=200, required=True)
        self.castigatori = discord.ui.TextInput(
            label="Câți câștigători", default="1",
            max_length=3, required=True)
        self.durata = discord.ui.TextInput(
            label="Durată în minute (60 = o oră)", default="60",
            placeholder="ex: 60", max_length=6, required=True)
        self.canal = discord.ui.TextInput(
            label="Canal (ID sau nume) — gol = canalul curent",
            placeholder="lasă gol ca să posteze aici", required=False, max_length=100)
        for it in (self.titlu, self.premiu, self.castigatori, self.durata, self.canal):
            self.add_item(it)

    def _resolve_channel(self, guild):
        raw = str(self.canal.value).strip()
        if not raw:
            return guild.get_channel(self.default_channel_id)
        raw = raw.strip("<#>")
        if raw.isdigit():
            ch = guild.get_channel(int(raw))
            if ch:
                return ch
        name = raw.lstrip("#").lower()
        for ch in guild.text_channels:
            if ch.name.lower() == name:
                return ch
        return None

    async def on_submit(self, interaction: discord.Interaction):
        try:
            winners = max(1, int(str(self.castigatori.value).strip()))
        except ValueError:
            winners = 1
        try:
            minutes = max(1, int(str(self.durata.value).strip()))
        except ValueError:
            minutes = 60

        channel = self._resolve_channel(interaction.guild)
        if channel is None:
            return await interaction.response.send_message(
                "Nu am găsit canalul scris. Verifică ID-ul/numele, sau lasă câmpul gol "
                "ca să postez pe canalul curent.", ephemeral=True)

        cfg = {
            "channel_id": channel.id,
            "title": str(self.titlu.value).strip() or "🎉 GIVEAWAY 🎉",
            "prize": str(self.premiu.value).strip(),
            "winners": winners,
            "sched_mode": "duration",
            "duration_minutes": minutes,
            "button_label": "🎉 Particip",
            "color": "#8b5cf6",
            "ping_everyone": True,   # ping pornit implicit
            "required_role_id": None,
            "host_id": interaction.user.id,
        }
        msg = await self.cog._post_giveaway(interaction.guild, cfg, host_id=interaction.user.id)
        if msg:
            await interaction.response.send_message(
                f"✅ Giveaway pornit în {channel.mention}: {msg.jump_url}", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Nu am putut posta (verifică permisiunile botului pe canalul ales).",
                ephemeral=True)


class Giveaway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # reinregistram butonul (sa mearga dupa restart) si pornim ceasul
        self.bot.add_view(GiveawayView())
        self.ticker.start()

    async def cog_unload(self):
        self.ticker.cancel()

    # ------------------------------------------------------------- programare
    def _compute_end_ts(self, cfg, start_ts):
        """Calculeaza momentul de INCHEIERE (unix) in functie de modul de programare."""
        mode = _sched_mode(cfg)

        if mode == "exact":
            date_s = str(cfg.get("end_date", "")).strip()
            hh, mm = _parse_hhmm(cfg.get("end_time"), (23, 59))
            if date_s:
                try:
                    y, mo, d = (int(x) for x in date_s.split("-"))
                    end_dt = datetime.datetime(y, mo, d, hh, mm, tzinfo=TZ)
                    ts = int(end_dt.timestamp())
                    if ts > start_ts:
                        return ts
                except (ValueError, TypeError):
                    pass
            # data lipseste sau e in trecut -> cadem pe durata
            return start_ts + int(cfg.get("duration_minutes", 60)) * 60

        if mode == "weekly":
            start_dt = datetime.datetime.fromtimestamp(start_ts, TZ)
            ew = int(cfg.get("end_weekday", 6))
            hh, mm = _parse_hhmm(cfg.get("end_time"), (22, 0))
            return int(_end_after(start_dt, ew, hh, mm).timestamp())

        # "duration" si "interval" -> fereastra fixa in minute de la postare
        return start_ts + int(cfg.get("duration_minutes", 60)) * 60

    def _next_weekly_start(self, cfg, from_ts):
        """Urmatorul moment de START (unix) pt. programarea saptamanala."""
        now_dt = datetime.datetime.fromtimestamp(from_ts, TZ)
        sw = int(cfg.get("start_weekday", 3))       # implicit Joi
        hh, mm = _parse_hhmm(cfg.get("start_time"), (20, 0))
        return _next_weekday_time(now_dt, sw, hh, mm).timestamp()

    # ------------------------------------------------------------- embed
    def _build_embed(self, cfg, prize, winners, end_ts, count=0, host_id=None) -> discord.Embed:
        desc = (f"**Premiu:** {prize}\n"
                f"**Castigatori:** {winners}\n"
                f"⏰ **Se termina:** <t:{end_ts}:R>\n"
                f"📅 **Exact la:** <t:{end_ts}:F>\n")
        if cfg.get("required_role_id"):
            desc += f"**Doar pentru:** <@&{cfg['required_role_id']}>\n"
        if host_id:
            desc += f"🎤 **Organizat de:** <@{host_id}>\n"
        desc += "\nApasa butonul de mai jos ca sa participi!"
        embed = discord.Embed(
            title=cfg.get("title", "🎉 GIVEAWAY 🎉"),
            description=desc,
            color=_color_from_hex(cfg.get("color", "#5865f2")),
        )
        embed.set_footer(text=f"👥 {count} participanti")
        return embed

    # ------------------------------------------------------------- postare
    async def _post_giveaway(self, guild, cfg, host_id=None):
        channel = guild.get_channel(int(cfg["channel_id"])) if cfg.get("channel_id") else None
        if channel is None:
            return None
        prize = cfg.get("prize") or "Premiu"
        winners = cfg.get("winners", 1)
        start_ts = int(time.time())
        end_ts = self._compute_end_ts(cfg, start_ts)
        if host_id is None:
            host_id = cfg.get("host_id")

        embed = self._build_embed(cfg, prize, winners, end_ts, count=0, host_id=host_id)
        view = GiveawayView(cfg.get("button_label") or "🎉 Particip")
        required = cfg.get("required_role_id")

        # daca e setat un rol -> ping pe rol; altfel @everyone; daca e oprit -> niciun ping
        if cfg.get("ping_everyone"):
            if required:
                content = f"<@&{required}>"
                allowed = discord.AllowedMentions(roles=True)
            else:
                content = "@everyone"
                allowed = discord.AllowedMentions(everyone=True)
        else:
            content = None
            allowed = discord.AllowedMentions.none()

        try:
            msg = await channel.send(content=content, embed=embed, view=view,
                                     allowed_mentions=allowed)
        except discord.HTTPException:
            return None

        data = storage.get(guild.id, "giveaways", {})
        active = data.get("active", {})
        active[str(msg.id)] = {
            "channel_id": channel.id, "prize": prize, "end_ts": end_ts,
            "winners": winners, "participants": [], "required_role_id": required,
            "host_id": host_id,
        }
        data["active"] = active
        storage.set(guild.id, "giveaways", data)
        self._schedule(guild, str(msg.id), end_ts)  # incheiere precisa la timp
        return msg

    # ------------------------------------------------------------- incheiere
    def _schedule(self, guild, mid, end_ts):
        """Programeaza incheierea EXACT la end_ts (nu astepta ceasul de 30s)."""
        delay = max(0, end_ts - time.time())

        async def runner():
            try:
                await asyncio.sleep(delay)
                await self._finalize(guild, mid)
            except asyncio.CancelledError:
                pass

        self.bot.loop.create_task(runner())

    async def _finalize(self, guild, mid):
        """Incheie un giveaway o SINGURA data (pop atomic), apoi anunta castigatorii."""
        data = storage.get(guild.id, "giveaways", {})
        active = data.get("active", {})
        gw = active.pop(mid, None)
        if gw is None:
            return  # deja incheiat de alt task -> nu dublam anuntul

        ended = data.get("ended", {})
        ended[mid] = gw
        if len(ended) > 20:
            for k in list(ended)[:-20]:
                ended.pop(k, None)
        data["ended"] = ended
        data["active"] = active
        storage.set(guild.id, "giveaways", data)

        await self._announce(guild, mid, gw)

    async def _announce(self, guild, mid, gw):
        channel = guild.get_channel(gw["channel_id"])
        if channel is None:
            return
        parts = gw.get("participants", [])
        if not parts:
            await channel.send(
                f"🎉 Giveaway pentru **{gw['prize']}** s-a incheiat — niciun participant. 😢")
        else:
            n = min(gw.get("winners", 1), len(parts))
            winners = random.sample(parts, n)
            mentions = ", ".join(f"<@{u}>" for u in winners)
            await channel.send(f"🎉 Felicitari {mentions}! Ai castigat **{gw['prize']}**!")

        # edit mesajul original: scrie ca s-a terminat si scoate butonul
        try:
            msg = await channel.fetch_message(int(mid))
            if msg.embeds:
                e = msg.embeds[0]
                desc = e.description or ""
                # inlocuim ceasul care numara (<t:...:R>) cu un text fix, ca sa nu mai para activ
                end_ts = gw.get("end_ts", 0)
                desc = desc.replace(f"⏰ **Se termina:** <t:{end_ts}:R>", "🔒 **Giveaway încheiat**")
                desc = desc.replace(
                    "Apasa butonul de mai jos ca sa participi!", "**🔒 Giveaway-ul s-a terminat!**")
                e.description = desc
                e.set_footer(text=f"👥 {len(parts)} participanti · Incheiat")
                await msg.edit(embed=e, view=None)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------- ceasul din fundal
    @tasks.loop(seconds=30)
    async def ticker(self):
        now = time.time()
        for guild in self.bot.guilds:
            data = storage.get(guild.id, "giveaways", {})
            active = data.get("active", {})

            # 1. plasa de siguranta: incheiem ce a expirat (de obicei deja
            #    incheiat de programarea precisa; _finalize e idempotent)
            expired = [mid for mid, gw in active.items() if gw.get("end_ts", 0) <= now]
            for mid in expired:
                await self._finalize(guild, mid)

            # 2. postare recurenta (interval sau saptamanal)
            data = storage.get(guild.id, "giveaways", {})
            cfg = data.get("config", {})
            if not cfg.get("channel_id"):
                continue
            mode = _sched_mode(cfg)

            if mode == "interval":
                nxt = data.get("next_post_ts")
                if nxt is None:
                    # plasa de siguranta (dashboard-ul o calculeaza ancorat la salvare)
                    data["next_post_ts"] = now + cfg.get("interval_hours", 24) * 3600
                    storage.set(guild.id, "giveaways", data)
                elif now >= nxt:
                    await self._post_giveaway(guild, cfg)
                    data = storage.get(guild.id, "giveaways", {})
                    # avansam de la ORA PROGRAMATA anterior (nu de la "acum"),
                    # ca sa ramana ancorat si sa nu se acumuleze intarziere
                    data["next_post_ts"] = nxt + cfg.get("interval_hours", 24) * 3600
                    storage.set(guild.id, "giveaways", data)

            elif mode == "weekly":
                nxt = data.get("next_post_ts")
                if nxt is None:
                    data["next_post_ts"] = self._next_weekly_start(cfg, now)
                    storage.set(guild.id, "giveaways", data)
                elif now >= nxt:
                    await self._post_giveaway(guild, cfg)
                    data = storage.get(guild.id, "giveaways", {})
                    # urmatorul start dupa "acum" -> daca botul a fost oprit
                    # mai multe saptamani, nu posteaza in rafala, sare direct
                    # la urmatoarea aparitie viitoare
                    data["next_post_ts"] = self._next_weekly_start(cfg, time.time())
                    storage.set(guild.id, "giveaways", data)

            elif mode == "exact":
                # programat O SINGURA DATA: daca ai setat o data de START (viitoare),
                # dashboard-ul pune next_post_ts la acel moment. Cand vine timpul,
                # postam o data si golim next_post_ts (nu se repeta). Daca n-ai pus
                # start, next_post_ts e None -> il postezi manual cu /giveaway_start.
                nxt = data.get("next_post_ts")
                if nxt is not None and now >= nxt:
                    await self._post_giveaway(guild, cfg)
                    data = storage.get(guild.id, "giveaways", {})
                    data["next_post_ts"] = None
                    storage.set(guild.id, "giveaways", data)

    @ticker.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        # dupa restart, reprogramam incheierea giveaway-urilor inca active
        # (cele care au expirat cat botul a fost oprit se incheie imediat)
        for guild in self.bot.guilds:
            data = storage.get(guild.id, "giveaways", {})
            for mid, gw in list(data.get("active", {}).items()):
                self._schedule(guild, mid, gw.get("end_ts", 0))

    # ------------------------------------------------------------- comenzi
    # /giveaway (comanda simpla) -> deschide mini-dashboardul chiar in Discord
    @app_commands.command(name="giveaway",
                          description="Deschide panoul ca sa configurezi si sa pornesti un giveaway")
    @bot_access()
    async def giveaway(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            GiveawayModal(self, interaction.channel_id))

    @app_commands.command(name="giveaway_start",
                          description="Posteaza acum un giveaway configurat in dashboard")
    @bot_access()
    async def giveaway_start(self, interaction: discord.Interaction):
        data = storage.get(interaction.guild_id, "giveaways", {})
        cfg = data.get("config", {})
        if not cfg.get("channel_id"):
            return await interaction.response.send_message(
                "Configureaza intai giveaway-ul in dashboard (canal, premiu, durata), "
                "sau da `/giveaway` ca sa-l faci direct de aici cu panoul.", ephemeral=True)
        # retinem cine a dat start (apare ca "Organizat de" si la postarile recurente)
        cfg["host_id"] = interaction.user.id
        data["config"] = cfg
        storage.set(interaction.guild_id, "giveaways", data)
        msg = await self._post_giveaway(interaction.guild, cfg, host_id=interaction.user.id)
        if msg:
            await interaction.response.send_message(f"✅ Giveaway postat: {msg.jump_url}", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Nu am putut posta (verifica canalul si permisiunile).", ephemeral=True)

    @app_commands.command(name="giveaway_end", description="Incheie acum un giveaway")
    @bot_access()
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str):
        data = storage.get(interaction.guild_id, "giveaways", {})
        active = data.get("active", {})
        gw = active.get(message_id)
        if not gw:
            return await interaction.response.send_message(
                "Nu exista un giveaway activ cu acest ID.", ephemeral=True)
        await interaction.response.send_message("Incheiem giveaway-ul...", ephemeral=True)
        await self._finalize(interaction.guild, message_id)

    @app_commands.command(name="giveaway_reroll", description="Alege alt castigator pentru un giveaway incheiat")
    @bot_access()
    async def giveaway_reroll(self, interaction: discord.Interaction, message_id: str):
        data = storage.get(interaction.guild_id, "giveaways", {})
        gw = data.get("ended", {}).get(message_id)
        if not gw or not gw.get("participants"):
            return await interaction.response.send_message(
                "Nu gasesc participanti pentru acest giveaway incheiat.", ephemeral=True)
        winner = random.choice(gw["participants"])
        await interaction.response.send_message(
            f"🎲 Noul castigator pentru **{gw['prize']}**: <@{winner}>!")


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
