"""
cogs/giveaway.py — GIVEAWAY cu buton de inscriere.

Posteaza un embed cu un buton; cine apasa intra in tragere. La final, botul
alege castigatorii automat. Totul e configurabil din dashboard:
  - canal, premiu, durata (timp), nr. castigatori, textul butonului, titlu, culoare
  - postare AUTOMATA la interval (giveaway recurent) — la fiecare X ore

CUM functioneaza tehnic:
  - butonul e "persistent" (custom_id fix) -> merge si dupa restart
  - un ceas in fundal (tasks.loop) verifica la fiecare 30s ce giveaway-uri
    expira (le incheie + alege castigatori) si daca e cazul posteaza unul nou.

Date salvate (cheia "giveaways"):
{
  "config": {channel_id, prize, duration_minutes, winners, button_label,
             title, color, recurring, interval_hours},
  "active": { "<message_id>": {channel_id, prize, end_ts, winners, participants:[], ...} },
  "ended":  { "<message_id>": {..., participants:[]} },   # pentru reroll
  "next_post_ts": <unix>                                   # urmatoarea postare recurenta
}

Comenzi (admin):
  /giveaway start              - posteaza acum un giveaway (foloseste config din dashboard)
  /giveaway end <message_id>   - incheie acum un giveaway
  /giveaway reroll <message_id>- alege alt castigator pentru un giveaway incheiat
"""

import time
import random
import asyncio

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage
from utils.perms import bot_access


def _color_from_hex(value: str) -> discord.Color:
    try:
        return discord.Color(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Color.blurple()


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
    """Un singur formular cu tot ce trebuie: titlu, premiu, castigatori, durata, canal."""

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
        end_ts = int(time.time()) + cfg.get("duration_minutes", 60) * 60
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

            # 2. postare recurenta (la interval)
            data = storage.get(guild.id, "giveaways", {})
            cfg = data.get("config", {})
            if cfg.get("recurring") and cfg.get("channel_id"):
                nxt = data.get("next_post_ts")
                if nxt is None:
                    data["next_post_ts"] = now + cfg.get("interval_hours", 24) * 3600
                    storage.set(guild.id, "giveaways", data)
                elif now >= nxt:
                    await self._post_giveaway(guild, cfg)
                    data = storage.get(guild.id, "giveaways", {})
                    data["next_post_ts"] = now + cfg.get("interval_hours", 24) * 3600
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
