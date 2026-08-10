"""
cogs/tickets.py — sistem de TICKETE configurabil (in stil Ticket Tool).

- Panou cu embed configurabil (titlu, descriere, culoare, imagine, thumbnail) si
  unul sau mai multe butoane; fiecare buton = un TIP de ticket, denumit cum vrei.
- La apasare se creeaza un canal privat, vizibil doar pentru cel ce a deschis +
  rolurile de suport configurate pentru acel tip.
- In ticket: butoane configurabile per tip — Inchide, Inchide cu motiv, Claim.
- La inchidere: transcript HTML salvat in canalul de loguri, apoi canalul se sterge.
- Comenzi: /add si /remove (membru/rol in ticket).

Butoanele sunt gestionate prin on_interaction (custom_id cu prefix "tkt:"), deci
merg si dupa restart, fara reinregistrare.

Date (cheia "tickets"):
{
  "panel": {title, description, color, image, thumbnail},
  "log_channel_id": "...",
  "counters": { "<type_id>": 0 },   # numaratoare SEPARATA pe fiecare tip
  "types": [ {id,label,emoji,button_color,support_roles[],category_id,
              open_message,ping_support,one_per_user,
              btn_close,btn_close_reason,btn_claim} ],
  "open": { "<channel_id>": {type_id, owner_id, number, claimed_by} }
}
"""

import io
import time
import html
import secrets

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import has_bot_access

_COLORS = {"blurple": discord.ButtonStyle.primary, "green": discord.ButtonStyle.success,
           "red": discord.ButtonStyle.danger, "grey": discord.ButtonStyle.secondary,
           "gray": discord.ButtonStyle.secondary}


def _color_from_hex(value: str) -> discord.Color:
    try:
        return discord.Color(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Color.blurple()


def _data(gid):
    return storage.get(gid, "tickets", {}) or {}


def _save(gid, data):
    storage.set(gid, "tickets", data)


def _type(data, type_id):
    for t in data.get("types", []):
        if t.get("id") == type_id:
            return t
    return None


def _sanitize(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name.lower())
    return keep.strip("-")[:90] or "ticket"


# ============================================================ VIEWS
def build_panel_view(types) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for t in types:
        style = _COLORS.get(t.get("button_color", "blurple"), discord.ButtonStyle.primary)
        v.add_item(discord.ui.Button(
            label=t.get("label", "Ticket"), emoji=(t.get("emoji") or None),
            style=style, custom_id=f"tkt:open:{t.get('id')}"))
    return v


def build_ticket_controls(t) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    if t.get("btn_claim"):
        v.add_item(discord.ui.Button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id="tkt:claim"))
    if t.get("btn_close", True):
        v.add_item(discord.ui.Button(label="Inchide", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="tkt:close"))
    if t.get("btn_close_reason"):
        v.add_item(discord.ui.Button(label="Inchide cu motiv", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="tkt:closereason"))
    return v


class ReasonModal(discord.ui.Modal, title="Inchide ticketul"):
    reason = discord.ui.TextInput(label="Motivul inchiderii", style=discord.TextStyle.paragraph,
                                  required=False, max_length=500)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.do_close(interaction, reason=str(self.reason) or "fara motiv")


# ============================================================ COG
class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------- dispatch butoane (merge si dupa restart) --------
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("tkt:"):
            return
        try:
            if cid.startswith("tkt:open:"):
                await self.open_ticket(interaction, cid.split(":", 2)[2])
            elif cid == "tkt:close":
                await self.ask_close(interaction)
            elif cid == "tkt:closereason":
                await interaction.response.send_modal(ReasonModal(self))
            elif cid == "tkt:claim":
                await self.claim(interaction)
            elif cid == "tkt:confirmclose":
                await self.do_close(interaction, reason=None)
            elif cid == "tkt:cancelclose":
                await interaction.response.edit_message(content="Anulat.", view=None)
        except discord.HTTPException:
            pass

    # -------- deschidere ticket --------
    async def open_ticket(self, interaction, type_id):
        guild = interaction.guild
        data = _data(guild.id)
        t = _type(data, type_id)
        if not t:
            return await interaction.response.send_message("Acest tip de ticket nu mai exista.", ephemeral=True)

        # limita: un singur ticket deschis per persoana (per tip)
        if t.get("one_per_user"):
            for cid, info in data.get("open", {}).items():
                if info.get("owner_id") == interaction.user.id and info.get("type_id") == type_id:
                    return await interaction.response.send_message(
                        f"Ai deja un ticket deschis: <#{cid}>", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        # permisiuni canal
        support_roles = []
        for rid in t.get("support_roles", []):
            role = guild.get_role(int(rid))
            if role:
                support_roles.append(role)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
        }
        for role in support_roles:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        category = guild.get_channel(int(t["category_id"])) if t.get("category_id") else None
        # numaratoare SEPARATA pentru fiecare tip de ticket
        counters = data.setdefault("counters", {})
        number = counters.get(type_id, 0) + 1
        counters[type_id] = number
        name = f"{_sanitize(t.get('label','ticket'))}-{number:04d}"

        try:
            channel = await guild.create_text_channel(
                name=name, overwrites=overwrites,
                category=category if isinstance(category, discord.CategoryChannel) else None,
                topic=f"Ticket #{number} · {t.get('label')} · deschis de {interaction.user}")
        except discord.Forbidden:
            return await interaction.followup.send(
                "Nu am permisiuni sa creez canalul (am nevoie de Manage Channels).", ephemeral=True)

        data.setdefault("open", {})[str(channel.id)] = {
            "type_id": type_id, "owner_id": interaction.user.id,
            "number": number, "claimed_by": None, "opened_ts": time.time()}
        _save(guild.id, data)

        # mesaj de deschidere
        msg = (t.get("open_message") or "Salut {user}! Echipa de suport îți va răspunde în curând.")
        msg = msg.replace("{user}", interaction.user.mention).replace("{server}", guild.name)
        embed = discord.Embed(title=f"🎫 {t.get('label','Ticket')} · #{number}",
                              description=msg, color=_color_from_hex(data.get("panel", {}).get("color", "#5865f2")))
        ping = ""
        if t.get("ping_support") and support_roles:
            ping = " ".join(r.mention for r in support_roles)
        await channel.send(
            content=(f"{interaction.user.mention} {ping}").strip(),
            embed=embed, view=build_ticket_controls(t),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True))

        await interaction.followup.send(f"✅ Ticket creat: {channel.mention}", ephemeral=True)

    # -------- claim --------
    async def claim(self, interaction):
        data = _data(interaction.guild_id)
        info = data.get("open", {}).get(str(interaction.channel_id))
        if not info:
            return await interaction.response.send_message("Nu pare un ticket activ.", ephemeral=True)
        if not self._is_staff(interaction, data, info):
            return await interaction.response.send_message("Doar echipa de suport poate revendica.", ephemeral=True)
        if info.get("claimed_by"):
            return await interaction.response.send_message(f"Deja revendicat de <@{info['claimed_by']}>.", ephemeral=True)
        info["claimed_by"] = interaction.user.id
        data["open"][str(interaction.channel_id)] = info
        _save(interaction.guild_id, data)
        await interaction.response.send_message(f"🙋 {interaction.user.mention} a revendicat acest ticket.")

    # -------- inchidere (confirmare) --------
    async def ask_close(self, interaction):
        data = _data(interaction.guild_id)
        if str(interaction.channel_id) not in data.get("open", {}):
            return await interaction.response.send_message("Nu pare un ticket activ.", ephemeral=True)
        v = discord.ui.View(timeout=60)
        v.add_item(discord.ui.Button(label="Da, inchide", style=discord.ButtonStyle.danger, custom_id="tkt:confirmclose"))
        v.add_item(discord.ui.Button(label="Anuleaza", style=discord.ButtonStyle.secondary, custom_id="tkt:cancelclose"))
        await interaction.response.send_message("Sigur vrei sa inchizi ticketul?", view=v, ephemeral=True)

    async def do_close(self, interaction, reason=None):
        guild = interaction.guild
        data = _data(guild.id)
        info = data.get("open", {}).get(str(interaction.channel_id))
        if not info:
            return await interaction.response.send_message("Nu pare un ticket activ.", ephemeral=True)

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        # transcript
        await self._save_transcript(guild, channel, data, info, reason, interaction.user)

        # scoatem din open si stergem canalul
        data.get("open", {}).pop(str(channel.id), None)
        _save(guild.id, data)
        try:
            await channel.delete(reason=f"Ticket inchis de {interaction.user}")
        except discord.HTTPException:
            pass

    async def _save_transcript(self, guild, channel, data, info, reason, closer):
        log_id = data.get("log_channel_id")
        if not log_id:
            return
        log_channel = guild.get_channel(int(log_id))
        if not log_channel:
            return
        # embed curat de log (fara fisier transcript, ca sa nu mai apara codul HTML)
        owner = f"<@{info.get('owner_id')}>"
        embed = discord.Embed(title="📋 Ticket inchis", color=discord.Color.orange())
        embed.add_field(name="Ticket", value=f"#{info.get('number')} ({channel.name})", inline=True)
        embed.add_field(name="Deschis de", value=owner, inline=True)
        embed.add_field(name="Inchis de", value=closer.mention, inline=True)
        if info.get("claimed_by"):
            embed.add_field(name="Revendicat de", value=f"<@{info['claimed_by']}>", inline=True)
        embed.add_field(name="📝 Motiv", value=reason if reason else "*(închis fără motiv)*", inline=False)
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    def _is_staff(self, interaction, data, info):
        if has_bot_access(interaction):
            return True
        t = _type(data, info.get("type_id"))
        if not t:
            return False
        support = {str(r) for r in t.get("support_roles", [])}
        return bool(support & {str(r.id) for r in getattr(interaction.user, "roles", [])})

    # -------- comenzi --------
    @app_commands.command(name="add", description="Adauga un membru sau rol in ticketul curent")
    async def add(self, interaction: discord.Interaction, tinta: str):
        await self._access(interaction, tinta, add=True)

    @app_commands.command(name="remove", description="Scoate un membru sau rol din ticketul curent")
    async def remove(self, interaction: discord.Interaction, tinta: str):
        await self._access(interaction, tinta, add=False)

    async def _access(self, interaction, tinta, add):
        data = _data(interaction.guild_id)
        info = data.get("open", {}).get(str(interaction.channel_id))
        if not info:
            return await interaction.response.send_message("Comanda merge doar intr-un ticket.", ephemeral=True)
        if not self._is_staff(interaction, data, info):
            return await interaction.response.send_message("Doar echipa de suport poate face asta.", ephemeral=True)

        target = None
        raw = tinta.strip("<@&!#> ")
        if raw.isdigit():
            target = interaction.guild.get_member(int(raw)) or interaction.guild.get_role(int(raw))
        if target is None:
            return await interaction.response.send_message("Nu am gasit membrul/rolul. Da mention sau ID.", ephemeral=True)

        ow = discord.PermissionOverwrite(view_channel=add, send_messages=add, read_message_history=add) if add else None
        try:
            await interaction.channel.set_permissions(target, overwrite=ow)
        except discord.Forbidden:
            return await interaction.response.send_message("Nu am permisiuni sa modific canalul.", ephemeral=True)
        verb = "adaugat in" if add else "scos din"
        await interaction.response.send_message(f"✅ {target.mention} a fost {verb} ticket.")

    # -------- postare panou (comanda admin) --------
    @app_commands.command(name="ticket_panel", description="Posteaza panoul de tickete in acest canal")
    async def ticket_panel(self, interaction: discord.Interaction):
        if not has_bot_access(interaction):
            return await interaction.response.send_message("Nu ai acces.", ephemeral=True)
        data = _data(interaction.guild_id)
        types = data.get("types", [])
        if not types:
            return await interaction.response.send_message(
                "Nu ai configurat niciun tip de ticket. Mergi pe dashboard.", ephemeral=True)
        p = data.get("panel", {})
        embed = discord.Embed(
            title=p.get("title") or "🎫 Suport",
            description=p.get("description") or "Apasa un buton mai jos ca sa deschizi un ticket.",
            color=_color_from_hex(p.get("color", "#5865f2")))
        if p.get("image"):
            embed.set_image(url=p["image"])
        if p.get("thumbnail"):
            embed.set_thumbnail(url=p["thumbnail"])
        await interaction.channel.send(embed=embed, view=build_panel_view(types))
        await interaction.response.send_message("✅ Panou postat.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
