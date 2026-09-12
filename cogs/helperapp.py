"""
cogs/helperapp.py — sistem de CERERI HELPER pentru un server de Metin2.

Flux:
  1. Un panou permanent (embed + buton "DEPUNE CERERE") sta intr-un canal.
  2. Userul apasa butonul -> i se deschide un formular (modal) cu 4 campuri:
     Nume caracter, Nivel, Cat timp poti sta zilnic, Varsta.
  3. La trimitere, cererea apare ca embed intr-un canal separat (configurabil),
     cu doua butoane: ACCEPTA / RESPINGE.
  4. Doar cine are rolul de staff configurat poate apasa acele butoane.
  5. ACCEPTA -> DM cu vestea buna + rolul de Helper acordat + mesajul se sterge.
     RESPINGE -> DM cu refuzul, fara rol + mesajul se sterge.
  6. O cerere e procesata O SINGURA DATA (blocaj impotriva dublei procesari,
     util cand doi staffi apasa aproape simultan).

Setarile (canal cereri, canal primire, rol staff, rol helper, texte) se
configureaza din dashboard — cheia de storage "helper_app" per guild — deci
nu trebuie umblat in codul botului pentru nimic din toate astea.

Storage:
  "helper_app"          -> {enabled, apply_channel_id, receive_channel_id,
                            staff_role_id, helper_role_id, panel_message_id,
                            panel_title, panel_text, button_label}
  "helper_processing"   -> {request_id: True}   # blocaj anti-dubla-procesare
"""
import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import has_bot_access


def _cfg(gid):
    return storage.get(gid, "helper_app", {}) or {}


def _save(gid, cfg):
    storage.set(gid, "helper_app", cfg)


def _color_from_hex(value):
    try:
        return discord.Color(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Color.blurple()


DEFAULT_QUESTIONS = [
    {"id": "q1", "label": "Nume caracter", "emoji": "🎮", "style": "short", "required": True},
    {"id": "q2", "label": "Nivel", "emoji": "⭐", "style": "short", "required": True},
    {"id": "q3", "label": "Cat timp poti sta zilnic", "emoji": "⏱️", "style": "short", "required": True},
    {"id": "q4", "label": "Varsta", "emoji": "🎂", "style": "short", "required": True},
]
MAX_QUESTIONS = 5  # limita HARD impusa de Discord pentru orice modal/formular


def _questions(cfg):
    """Intrebarile configurate din dashboard, sau cele 4 implicite daca
    setarea nu a fost atinsa NICIODATA (o lista goala inseamna ca userul
    chiar a sters toate intrebarile, intentionat — nu revenim la implicit)."""
    qs = cfg.get("questions")
    if qs is None:
        return DEFAULT_QUESTIONS
    return qs[:MAX_QUESTIONS]


# ============================================================ MODAL (formular)
class HelperApplyModal(discord.ui.Modal, title="Cerere Helper"):
    def __init__(self, cog, questions):
        super().__init__()
        self.cog = cog
        self.questions = questions
        for q in questions:
            style = discord.TextStyle.paragraph if q.get("style") == "paragraph" else discord.TextStyle.short
            self.add_item(discord.ui.TextInput(
                label=q["label"][:45],  # Discord limiteaza eticheta la 45 caractere
                style=style,
                required=q.get("required", True),
                max_length=1000 if style == discord.TextStyle.paragraph else 100,
                custom_id=q["id"]))

    async def on_submit(self, interaction: discord.Interaction):
        answers = {item.custom_id: str(item.value) for item in self.children}
        await self.cog.submit_application(interaction, self.questions, answers)


# ============================================================ VIEW-uri
def _apply_view():
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="DEPUNE CERERE", emoji="📝", style=discord.ButtonStyle.blurple,
        custom_id="helperapp:apply"))
    return view


def _decision_view(req_id):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="ACCEPTĂ", emoji="✅", style=discord.ButtonStyle.green,
        custom_id=f"helperapp:accept:{req_id}"))
    view.add_item(discord.ui.Button(
        label="RESPINGE", emoji="❌", style=discord.ButtonStyle.red,
        custom_id=f"helperapp:reject:{req_id}"))
    return view


# ============================================================ COG
class HelperApp(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------- verificare acces pentru butoanele Accepta/Respinge --------
    @staticmethod
    def _is_authorized(interaction, cfg):
        # adminii / cei cu Manage Server / owner-ul serverului pot MEREU procesa
        # cereri, chiar daca nu au exact rolul de staff configurat in dashboard.
        # In plus, oricine are rolul de staff setat e la fel autorizat.
        if has_bot_access(interaction):
            return True
        role_id = cfg.get("staff_role_id")
        if not role_id:
            return False
        member = interaction.user
        return any(str(r.id) == str(role_id) for r in getattr(member, "roles", []))

    # -------- comanda: posteaza panoul in canalul curent --------
    @app_commands.command(name="helper_panel",
                          description="Posteaza panoul de cereri Helper in acest canal")
    async def helper_panel(self, interaction: discord.Interaction):
        if not has_bot_access(interaction):
            return await interaction.response.send_message("Nu ai acces.", ephemeral=True)
        cfg = _cfg(interaction.guild_id)
        embed = discord.Embed(
            title=cfg.get("panel_title") or "📝 Cereri Helper",
            description=cfg.get("panel_text") or
            "Apasă butonul de mai jos ca să depui o cerere pentru funcția de Helper.",
            color=_color_from_hex(cfg.get("panel_color", "#5865f2")))
        try:
            msg = await interaction.channel.send(embed=embed, view=_apply_view())
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"Nu am putut posta panoul: `{e}`",
                                                            ephemeral=True)
        cfg["apply_channel_id"] = interaction.channel.id
        cfg["panel_message_id"] = msg.id
        _save(interaction.guild_id, cfg)

        # incercam sa blocam userii sa scrie in canal (cerinta: canal doar-citire)
        try:
            await interaction.channel.set_permissions(
                interaction.guild.default_role, send_messages=False,
                reason="Canal cereri Helper — doar citire")
            note = "\n\n🔒 Am setat canalul ca membrii să nu poată scrie în el."
        except discord.HTTPException:
            note = ("\n\n⚠️ Nu am putut restricționa scrisul în canal automat "
                   "(lipsește permisiunea Manage Channels) — setează-l manual dacă vrei.")
        await interaction.response.send_message("✅ Panou postat." + note, ephemeral=True)

    # -------- primeste formularul completat --------
    async def submit_application(self, interaction, questions, answers):
        cfg = _cfg(interaction.guild_id)
        recv_id = cfg.get("receive_channel_id")
        channel = interaction.guild.get_channel(int(recv_id)) if recv_id else None
        if channel is None:
            return await interaction.response.send_message(
                "⚠️ Nu e configurat canalul unde ajung cererile. Anunță un admin.",
                ephemeral=True)

        req_id = uuid.uuid4().hex[:10]
        embed = discord.Embed(title="🛡️ CERERE HELPER", color=discord.Color(0x5865F2))
        embed.add_field(name="👤 Utilizator", value=interaction.user.mention, inline=False)
        for q in questions:
            val = answers.get(q["id"], "") or "-"
            emoji = q.get("emoji") or "▫️"
            embed.add_field(name=f"{emoji} {q['label']}", value=val,
                            inline=(q.get("style") != "paragraph"))
        embed.set_footer(text=f"ID cerere: {req_id}")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(
                content=interaction.user.mention,
                embed=embed, view=_decision_view(req_id),
                allowed_mentions=discord.AllowedMentions(users=[interaction.user]))
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"Nu am putut trimite cererea: `{e}`. Anunță un admin.", ephemeral=True)

        # tinem minte pe cine trebuie sa anuntam (DM + rol) cand se proceseaza
        reqs = storage.get(interaction.guild_id, "helper_requests", {}) or {}
        reqs[req_id] = {"user_id": str(interaction.user.id), "created_ts": time.time()}
        storage.set(interaction.guild_id, "helper_requests", reqs)

        await interaction.response.send_message(
            "✅ Cererea ta a fost trimisă! Vei primi un mesaj privat cu rezultatul.",
            ephemeral=True)

    # -------- dispatch butoane (merge si dupa restart, ca la tickete) --------
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("helperapp:"):
            return
        try:
            if cid == "helperapp:apply":
                cfg = _cfg(interaction.guild_id)
                if not cfg.get("enabled", True):
                    return await interaction.response.send_message(
                        "🔒 Cererile de Helper sunt momentan **închise**. "
                        "Revino mai târziu.", ephemeral=True)
                qs = _questions(cfg) or DEFAULT_QUESTIONS  # Discord cere minim 1 camp
                await interaction.response.send_modal(HelperApplyModal(self, qs))
            elif cid.startswith("helperapp:accept:"):
                await self._decide(interaction, cid.split(":", 2)[2], accept=True)
            elif cid.startswith("helperapp:reject:"):
                await self._decide(interaction, cid.split(":", 2)[2], accept=False)
        except discord.HTTPException:
            pass

    # -------- procesare Accepta / Respinge --------
    async def _decide(self, interaction, req_id, accept: bool):
        cfg = _cfg(interaction.guild_id)

        if not self._is_authorized(interaction, cfg):
            return await interaction.response.send_message(
                "❌ Nu ai permisiunea de a procesa cereri Helper.", ephemeral=True)

        # blocaj anti-dubla-procesare: primul care ajunge aici "castiga"
        processing = storage.get(interaction.guild_id, "helper_processing", {}) or {}
        if processing.get(req_id):
            return await interaction.response.send_message(
                "⚠️ Această cerere a fost deja procesată de altcineva.", ephemeral=True)
        processing[req_id] = True
        storage.set(interaction.guild_id, "helper_processing", processing)

        reqs = storage.get(interaction.guild_id, "helper_requests", {}) or {}
        info = reqs.get(req_id)
        if not info:
            # cerere necunoscuta (poate un restart intre timp) — stergem oricum mesajul
            try:
                await interaction.message.delete()
            except discord.HTTPException:
                pass
            return await interaction.response.send_message(
                "⚠️ Nu mai găsesc datele acestei cereri (posibil botul a repornit). "
                "Mesajul a fost șters.", ephemeral=True)

        member = interaction.guild.get_member(int(info["user_id"]))
        await interaction.response.defer(ephemeral=True)

        # 1) DM catre aplicant
        dm_ok = True
        if member:
            try:
                if accept:
                    await member.send(
                        "🎉 **CERERE HELPER ACCEPTATĂ**\n\n"
                        "Cererea ta pentru funcția de Helper a fost acceptată!\n\n"
                        "Bine ai venit în echipa Staff! 🛡️")
                else:
                    await member.send(
                        "❌ **CERERE HELPER RESPINSĂ**\n\n"
                        "Din păcate, cererea ta pentru funcția de Helper nu a fost acceptată.\n\n"
                        "Îți mulțumim pentru interes și îți dorim succes!")
            except discord.Forbidden:
                dm_ok = False

        # 2) rolul de Helper (doar la acceptare)
        role_ok = True
        if accept and member:
            role_id = cfg.get("helper_role_id")
            role = interaction.guild.get_role(int(role_id)) if role_id else None
            if role:
                try:
                    await member.add_roles(role, reason=f"Cerere Helper acceptata ({req_id})")
                except discord.HTTPException:
                    role_ok = False
            else:
                role_ok = False

        # 2b) porecla cu prefix in fata (doar la acceptare). Prefixul se seteaza
        # din dashboard (implicit "[H] "); daca il lasi GOL, nu schimbam porecla.
        # Discord NU permite niciunui bot sa schimbe porecla proprietarului
        # serverului, nici a cuiva cu un rol mai sus decat al botului -> tratam
        # ambele fara sa cadem.
        nick_ok = True
        prefix = cfg.get("nick_prefix", "[H] ")
        if accept and member and prefix:
            current = member.display_name
            if not current.startswith(prefix):
                new_nick = f"{prefix}{current}"[:32]  # Discord limiteaza porecla la 32 caractere
                try:
                    await member.edit(nick=new_nick, reason=f"Cerere Helper acceptata ({req_id})")
                except discord.Forbidden:
                    nick_ok = False
                except discord.HTTPException:
                    nick_ok = False

        # 3) curatam evidentele + stergem mesajul cererii
        reqs.pop(req_id, None)
        storage.set(interaction.guild_id, "helper_requests", reqs)
        processing.pop(req_id, None)
        storage.set(interaction.guild_id, "helper_processing", processing)
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass

        # confirmare catre staff (doar el o vede)
        who = member.mention if member else f"<@{info['user_id']}>"
        result = "✅ acceptată" if accept else "❌ respinsă"
        extra = []
        if not dm_ok:
            extra.append("nu i-am putut trimite DM (are mesajele private închise)")
        if accept and not role_ok:
            extra.append("nu am putut acorda rolul de Helper (verifică rolul configurat "
                        "și poziția rolului botului)")
        if accept and not nick_ok:
            extra.append("nu am putut schimba porecla (probabil e proprietarul "
                        "serverului sau are un rol mai sus decât al botului)")
        note = f" ({'; '.join(extra)})" if extra else ""
        await interaction.followup.send(
            f"Cererea lui {who} a fost {result}.{note}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelperApp(bot))
