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


def _player_message(m, style="simplu"):
    """Construieste mesajul unui jucator in stilul ales din dashboard.
    Intoarce (content, embed) — content e text simplu, embed poate fi None.
    Stiluri: simplu, embed, citat, terminal."""
    name = m.get("sender_name", "Jucator")
    text = m.get("text", "") or ""
    img = m.get("image_url")
    tid = m.get("ticket_id", "")

    if style == "embed":
        # embed complet: bara colorata + autor cu iconita + footer cu numarul ticketului
        e = discord.Embed(description=text or "\u200b", color=discord.Color(0xEB459E))
        e.set_author(name=name)
        e.set_footer(text=f"Ticket #{tid}")
        if img:
            e.set_image(url=img)
        return None, e

    if style == "citat":
        # numele + mesajul ca un citat (blockquote Discord: „> ")
        quoted = "\n".join(f"> {line}" for line in text.split("\n")) if text else "> \u200b"
        content = f"🎮 **{name}**\n{quoted}"
        e = None
        if img:
            e = discord.Embed(color=discord.Color(0x4E5058))
            e.set_image(url=img)
        return content, e

    if style == "terminal":
        # bloc de cod colorat, doar numele + numarul ticketului (fara [JOC])
        body = text.replace("```", "'''")
        content = f"```ansi\n\u001b[36m{name}\u001b[0m \u001b[30m· #{tid}\u001b[0m\n{body}\n```"
        e = None
        if img:
            e = discord.Embed(color=discord.Color(0x2B2D31))
            e.set_image(url=img)
        return content, e

    # implicit „simplu” — mesaj normal, fara embed: 🎮 nume + text
    content = f"🎮 **{name}**\n{text}" if text else f"🎮 **{name}**"
    e = None
    if img:
        e = discord.Embed(color=discord.Color(0x3BA55D))
        e.set_image(url=img)
    return content, e


def _cfg(gid):
    return storage.get(gid, "metin2", {}) or {}


def _staff_ids(d):
    """Rolurile de staff dintr-o configurare — accepta si formatul nou (lista),
    si pe cel vechi (un singur rol), ca nimic sa nu se strice la trecere."""
    ids = list(d.get("staff_role_ids") or [])
    old = d.get("staff_role_id")
    if old and str(old) not in [str(x) for x in ids]:
        ids.append(old)
    out = []
    for x in ids:
        try:
            out.append(int(x))
        except (ValueError, TypeError):
            pass
    return out


def _route_for(cfg, game_category):
    """Gaseste ruta pentru o categorie din joc: (categoria Discord, rolurile staff).
    Daca nu exista mapare pentru categoria respectiva -> setarile implicite."""
    gc = (game_category or "").strip().lower()
    for m in cfg.get("cat_map", []):
        if (m.get("game_category") or "").strip().lower() == gc and gc:
            roles = _staff_ids(m) or _staff_ids(cfg)
            return (m.get("category_id") or cfg.get("category_id"), roles)
    return cfg.get("category_id"), _staff_ids(cfg)


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
                content, embed = _player_message(m, cfg.get("msg_style", "simplu"))
                try:
                    await channel.send(content=content, embed=embed)
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
        # ruta per categorie: fiecare categorie din joc poate avea categoria ei
        # Discord + rolurile ei de staff (unul sau mai multe)
        cat_id, staff_ids = _route_for(cfg, t.get("category"))
        category = guild.get_channel(int(cat_id)) if cat_id else None
        name = f"metin-{t.get('id')}-{(t.get('player_name') or 'jucator')[:20]}"
        try:
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            for rid in staff_ids:
                role = guild.get_role(rid)
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
        ping = " ".join(f"<@&{rid}>" for rid in staff_ids)
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

    RESOLVE_DELETE_DELAY = 10  # secunde pana la stergerea canalului dupa Rezolvat

    async def _resolve_and_cleanup(self, guild, channel, ticket_id, cfg):
        """Marcheaza rezolvat in joc; daca a mers, sterge canalul dupa cateva secunde.
        Nu sterge nimic daca statusul nu a ajuns la API (sa nu pierdem conversatia)."""
        ok = await self._post(cfg, f"/tickets/{ticket_id}/status", {"status": "resolved"})
        if not ok:
            return False
        # curatam evidentele intai (poll-ul sa nu mai trimita nimic aici)
        open_map = storage.get(guild.id, "metin2_open", {}) or {}
        open_map.pop(str(channel.id), None)
        storage.set(guild.id, "metin2_open", open_map)
        claims = storage.get(guild.id, "metin2_claims", {}) or {}
        claims.pop(str(channel.id), None)
        storage.set(guild.id, "metin2_claims", claims)

        async def _delete_later():
            await asyncio.sleep(self.RESOLVE_DELETE_DELAY)
            try:
                await channel.delete(reason="Ticket Metin2 rezolvat")
            except (discord.HTTPException, AttributeError):
                pass  # canal deja sters / fara permisiuni -> nu crapam

        asyncio.create_task(_delete_later())
        return True

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
            ok = await self._resolve_and_cleanup(guild, interaction.channel,
                                                 ticket_id, cfg)
            if ok:
                await interaction.response.send_message(
                    f"✅ Ticket-ul a fost rezolvat de {interaction.user.mention} "
                    f"și se va șterge în **10s**.")
            else:
                await interaction.response.send_message(
                    "⚠️ N-am putut trimite statusul la API. Canalul rămâne. Mai încearcă.",
                    ephemeral=True)
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
            ok = await self._resolve_and_cleanup(message.guild, message.channel,
                                                 ticket_id, cfg)
            try:
                if ok:
                    await message.channel.send(
                        f"✅ Ticket-ul a fost rezolvat de {message.author.mention} "
                        f"și se va șterge în **10s**.")
                else:
                    await message.channel.send(
                        "⚠️ N-am putut trimite statusul la API. Canalul rămâne.")
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
