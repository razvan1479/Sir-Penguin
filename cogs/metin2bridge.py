"""
cogs/metin2bridge.py — punte tickete Metin2 ⇆ Discord (MODUL OPTIONAL).

Nu atinge sistemul de tickete de Discord existent (cogs/tickets.py). E separat.
Se porneste/opreste din dashboard (pagina „Metin2 tickete"). Cand e pornit:
  - intreaba periodic API-ul tau de Metin2 ce tickete/mesaje noi sunt
  - creeaza canale pe Discord pentru ticketele noi
  - trimite mesajele jucatorilor in canalul de Discord
  - cand staff-ul scrie in canal, trimite mesajul inapoi la API-ul tau

Config in storage, cheia „metin2” (per server):
  enabled, api_base, api_token, category_id (unde se creeaza canalele),
  staff_role_id (ping), poll_seconds
Vezi METIN2_TICKET_API.md pentru formatul exact al API-ului.
"""

import asyncio
import datetime

import aiohttp
import discord
from discord.ext import commands, tasks

from utils import storage

# canalele deschise de punte: storage cheia „metin2_open” = { channel_id: ticket_id }
# ca sa stim, cand scrie staff intr-un canal, la ce ticket din joc apartine.


def _cfg(gid):
    return storage.get(gid, "metin2", {}) or {}


def _route_for(cfg, game_category):
    """Gaseste ruta pentru o categorie din joc: (categoria Discord, rolul staff).
    Daca nu exista mapare pentru categoria respectiva -> setarile implicite."""
    gc = (game_category or "").strip().lower()
    for m in cfg.get("cat_map", []):
        if (m.get("game_category") or "").strip().lower() == gc and gc:
            return (m.get("category_id") or cfg.get("category_id"),
                    m.get("staff_role_id") or cfg.get("staff_role_id"))
    return cfg.get("category_id"), cfg.get("staff_role_id")


def _ticket_buttons():
    """Butoanele de pe fiecare ticket din joc (ca la ticketele de Discord)."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Rezolvat", emoji="✅",
                                    style=discord.ButtonStyle.success,
                                    custom_id="m2t:resolve"))
    view.add_item(discord.ui.Button(label="În lucru", emoji="🛠️",
                                    style=discord.ButtonStyle.primary,
                                    custom_id="m2t:progress"))
    view.add_item(discord.ui.Button(label="Claim", emoji="🙋",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="m2t:claim"))
    return view


class Metin2Bridge(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self._last_poll = {}  # gid -> ultima data cand am facut poll (anti-spam)
        self.poll_loop.start()

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        self.poll_loop.cancel()
        if self.session:
            self.bot.loop.create_task(self.session.close())

    # -------------------------------------------------- API helpers
    def _headers(self, cfg):
        return {"Authorization": f"Bearer {cfg.get('api_token','')}",
                "Content-Type": "application/json"}

    async def _get(self, cfg, path):
        base = cfg.get("api_base", "").rstrip("/")
        try:
            async with self.session.get(base + path, headers=self._headers(cfg),
                                        timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    async def _post(self, cfg, path, payload):
        base = cfg.get("api_base", "").rstrip("/")
        try:
            async with self.session.post(base + path, headers=self._headers(cfg),
                                         json=payload,
                                         timeout=aiohttp.ClientTimeout(total=15)) as r:
                return r.status < 300
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    # -------------------------------------------------- bucla de sincronizare
    @tasks.loop(seconds=10)
    async def poll_loop(self):
        for guild in list(self.bot.guilds):
            cfg = _cfg(guild.id)
            if not cfg.get("enabled") or not cfg.get("api_base") or not cfg.get("api_token"):
                continue
            # respectam intervalul ales (implicit 10s)
            interval = max(5, int(cfg.get("poll_seconds", 10)))
            import time
            now = time.time()
            if now - self._last_poll.get(guild.id, 0) < interval:
                continue
            self._last_poll[guild.id] = now
            try:
                await self._sync_guild(guild, cfg)
            except Exception:
                # orice eroare -> sarim peste ciclul asta, botul merge mai departe
                continue

    async def _sync_guild(self, guild, cfg):
        # 1) tickete noi din joc -> creeaza canale pe Discord
        data = await self._get(cfg, "/tickets/pending")
        if data and isinstance(data.get("tickets"), list):
            for t in data["tickets"]:
                await self._create_ticket_channel(guild, cfg, t)

        # 2) mesaje noi de la jucatori -> pune-le in canalele de Discord
        msgs = await self._get(cfg, "/messages/pending")
        if msgs and isinstance(msgs.get("messages"), list):
            acked = []
            open_map = storage.get(guild.id, "metin2_open", {}) or {}
            # inversam: ticket_id -> channel_id
            tid_to_cid = {v: k for k, v in open_map.items()}
            for m in msgs["messages"]:
                cid = tid_to_cid.get(m.get("ticket_id"))
                if not cid:
                    continue
                channel = guild.get_channel(int(cid))
                if channel is None:
                    continue
                embed = discord.Embed(
                    description=m.get("text", ""),
                    color=discord.Color(0x3BA55D))
                embed.set_author(name=f"🎮 {m.get('sender_name','Jucator')}")
                if m.get("image_url"):
                    embed.set_image(url=m["image_url"])
                try:
                    await channel.send(embed=embed)
                    acked.append(m.get("id"))
                except discord.HTTPException:
                    pass
            if acked:
                await self._post(cfg, "/messages/ack", {"message_ids": acked})

    async def _create_ticket_channel(self, guild, cfg, t):
        open_map = storage.get(guild.id, "metin2_open", {}) or {}
        # deja creat? (nu dublam)
        if str(t.get("id")) in open_map.values():
            return
        # ruta per categorie (ca la tipurile de tickete): fiecare categorie din joc
        # poate avea categoria ei Discord + rolul ei de staff
        cat_id, staff_id = _route_for(cfg, t.get("category"))
        category = guild.get_channel(int(cat_id)) if cat_id else None
        name = f"metin-{t.get('id')}-{(t.get('player_name') or 'jucator')[:20]}"
        try:
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            if staff_id:
                role = guild.get_role(int(staff_id))
                if role:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True)
            channel = await guild.create_text_channel(
                name=name,
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                reason="Ticket Metin2")
        except discord.HTTPException:
            return

        embed = discord.Embed(
            title=f"🎫 {t.get('title','Ticket')}",
            description=t.get("description", ""),
            color=discord.Color(0x5865F2),
            timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="Jucător", value=t.get("player_name", "?"), inline=True)
        embed.add_field(name="Categorie", value=t.get("category", "—"), inline=True)
        embed.add_field(name="Status", value=t.get("status", "open"), inline=True)
        embed.set_footer(text=f"Ticket #{t.get('id')} · din joc")
        ping = f"<@&{staff_id}>" if staff_id else ""
        try:
            await channel.send(content=ping or None, embed=embed, view=_ticket_buttons())
        except discord.HTTPException:
            pass

        # retinem legatura channel <-> ticket
        open_map[str(channel.id)] = t.get("id")
        storage.set(guild.id, "metin2_open", open_map)
        # confirmam la API ca l-am preluat
        await self._post(cfg, f"/tickets/{t.get('id')}/link",
                         {"discord_channel_id": str(channel.id)})

    # ------------------------------------------- butoanele de pe tickete
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("m2t:"):
            return
        action = cid.split(":", 1)[1]
        guild = interaction.guild
        if guild is None:
            return
        open_map = storage.get(guild.id, "metin2_open", {}) or {}
        ticket_id = open_map.get(str(interaction.channel_id))
        if ticket_id is None:
            return await interaction.response.send_message(
                "Acest canal nu mai e legat de un ticket din joc.", ephemeral=True)
        cfg = _cfg(guild.id)
        if action == "resolve":
            ok = await self._post(cfg, f"/tickets/{ticket_id}/status",
                                  {"status": "resolved"})
            if ok:
                await interaction.response.send_message(
                    f"✅ {interaction.user.mention} a marcat ticketul **rezolvat** (trimis în joc).")
            else:
                await interaction.response.send_message(
                    "⚠️ N-am putut trimite statusul la API. Mai încearcă.", ephemeral=True)
        elif action == "progress":
            ok = await self._post(cfg, f"/tickets/{ticket_id}/status",
                                  {"status": "in_progress"})
            if ok:
                await interaction.response.send_message(
                    f"🛠️ {interaction.user.mention} a marcat ticketul **în lucru** (trimis în joc).")
            else:
                await interaction.response.send_message(
                    "⚠️ N-am putut trimite statusul la API. Mai încearcă.", ephemeral=True)
        elif action == "claim":
            claims = storage.get(guild.id, "metin2_claims", {}) or {}
            existing = claims.get(str(interaction.channel_id))
            if existing and str(existing) != str(interaction.user.id):
                return await interaction.response.send_message(
                    f"Ticketul e deja preluat de <@{existing}>.", ephemeral=True)
            claims[str(interaction.channel_id)] = str(interaction.user.id)
            storage.set(guild.id, "metin2_claims", claims)
            await interaction.response.send_message(
                f"🙋 {interaction.user.mention} a preluat acest ticket.")

    # -------------------------------------------------- staff scrie in canal -> trimite in joc
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        open_map = storage.get(message.guild.id, "metin2_open", {}) or {}
        ticket_id = open_map.get(str(message.channel.id))
        if ticket_id is None:
            return
        cfg = _cfg(message.guild.id)
        if not cfg.get("enabled"):
            return
        # comenzi de status simple in canal
        content = message.content.strip()
        if content.lower() in ("!rezolvat", "!resolved", "!close"):
            await self._post(cfg, f"/tickets/{ticket_id}/status", {"status": "resolved"})
            try:
                await message.channel.send("✅ Ticket marcat ca **rezolvat** (trimis în joc).")
            except discord.HTTPException:
                pass
            return
        if content.lower() in ("!inlucru", "!progress"):
            await self._post(cfg, f"/tickets/{ticket_id}/status", {"status": "in_progress"})
            try:
                await message.channel.send("🛠️ Ticket marcat ca **în lucru** (trimis în joc).")
            except discord.HTTPException:
                pass
            return
        # mesaj normal de staff -> il trimitem in joc
        image_url = message.attachments[0].url if message.attachments else None
        await self._post(cfg, f"/tickets/{ticket_id}/message", {
            "sender": "staff",
            "sender_name": message.author.display_name,
            "text": message.content,
            "image_url": image_url,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })

    @poll_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Metin2Bridge(bot))
