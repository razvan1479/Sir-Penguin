"""
cogs/tempvoice.py — canale de voice temporare ("Join to Create").

CUM FUNCTIONEAZA:
  - Setezi din dashboard un canal de voice "generator" (ex. "➕ Creează voice")
    si (optional) o categorie unde sa apara canalele noi.
  - Cand cineva INTRA in canalul generator, botul ii creeaza un canal de voice
    propriu, il muta acolo si ii pune in chat-ul canalului un PANOU cu butoane.
  - Din panou, proprietarul alege: 🔒 Privat / 🔓 Public, ➕ Permite pe cineva,
    🚫 Scoate acces, ✏️ Redenumește, 👥 Limită locuri.
  - Cand canalul ramane GOL, botul il sterge automat.

Config (storage cheia "tempvoice", per guild):
  enabled, generator_id, category_id, name_tpl ("{user}" = numele lui),
  default_private (bool), user_limit (0 = nelimitat)

Stare (storage cheia "tempvoice_active", per guild):
  { "<channel_id>": {"owner_id": "...", "private": bool} }   # canalele temporare active
"""
import discord
from discord.ext import commands

from utils import storage


def _cfg(gid):
    return storage.get(gid, "tempvoice", {}) or {}


def _active(gid):
    return storage.get(gid, "tempvoice_active", {}) or {}


def _save_active(gid, data):
    storage.set(gid, "tempvoice_active", data)


# ============================================================ PANOU (butoane)
def _panel_view():
    """View persistent cu butoane (custom_id fix -> merge si dupa restart)."""
    v = discord.ui.View(timeout=None)
    specs = [
        ("tv:lock", "Privat", "🔒", discord.ButtonStyle.secondary),
        ("tv:unlock", "Public", "🔓", discord.ButtonStyle.secondary),
        ("tv:permit", "Permite", "➕", discord.ButtonStyle.success),
        ("tv:unpermit", "Scoate acces", "🚫", discord.ButtonStyle.danger),
        ("tv:rename", "Redenumește", "✏️", discord.ButtonStyle.secondary),
        ("tv:limit", "Limită", "👥", discord.ButtonStyle.secondary),
    ]
    for cid, label, emoji, style in specs:
        v.add_item(discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=cid))
    return v


def _panel_embed(owner_id, private):
    stare = "🔒 **Privat** — doar tu și cei pe care îi lași poți intra." if private \
        else "🔓 **Public** — toată lumea poate intra."
    e = discord.Embed(
        title="🎛️ Panoul canalului tău",
        description=(f"Bun venit! Acesta e canalul tău de voice.\n\n"
                     f"Stare: {stare}\n\n"
                     f"• **🔒 Privat / 🔓 Public** — alegi cine poate intra\n"
                     f"• **➕ Permite** — lași anumite persoane să intre (când e privat)\n"
                     f"• **🚫 Scoate acces** — scoți pe cineva căruia i-ai dat voie\n"
                     f"• **✏️ Redenumește** · **👥 Limită** locuri\n\n"
                     f"Doar tu (<@{owner_id}>) poți folosi butoanele. "
                     f"Canalul se șterge singur când rămâne gol."),
        color=discord.Color(0x5865F2))
    return e


# ============================================================ MODALE
class RenameModal(discord.ui.Modal, title="Redenumește canalul"):
    nume = discord.ui.TextInput(label="Nume nou", max_length=100, required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.channel.edit(name=str(self.nume.value).strip()[:100])
            await interaction.response.send_message("✅ Am redenumit canalul.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Nu am putut redenumi (rate limit?).", ephemeral=True)


class LimitModal(discord.ui.Modal, title="Limită de locuri"):
    limita = discord.ui.TextInput(label="Câte locuri (0 = nelimitat)", max_length=2, required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = max(0, min(int(str(self.limita.value).strip()), 99))
        except ValueError:
            return await interaction.response.send_message("Scrie un număr între 0 și 99.", ephemeral=True)
        try:
            await interaction.channel.edit(user_limit=n)
            txt = "fără limită" if n == 0 else f"{n} locuri"
            await interaction.response.send_message(f"✅ Limită setată: {txt}.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Nu am putut seta limita.", ephemeral=True)


# ============================================================ SELECT (permite/scoate)
class _UserPick(discord.ui.View):
    def __init__(self, cog, mode):
        super().__init__(timeout=120)
        self.cog = cog
        self.mode = mode  # "permit" / "unpermit"
        sel = discord.ui.UserSelect(
            placeholder="Alege persoana...", min_values=1, max_values=1)
        sel.callback = self._picked
        self.add_item(sel)

    async def _picked(self, interaction: discord.Interaction):
        target = interaction.data["values"][0]
        member = interaction.guild.get_member(int(target))
        if member is None:
            return await interaction.response.send_message("Nu am găsit persoana.", ephemeral=True)
        try:
            if self.mode == "permit":
                await interaction.channel.set_permissions(
                    member, connect=True, view_channel=True,
                    reason="tempvoice: permite acces")
                msg = f"✅ {member.mention} poate intra acum în canal."
            else:
                await interaction.channel.set_permissions(
                    member, overwrite=None, reason="tempvoice: scoate acces")
                # daca e inauntru si canalul e privat, il dam afara
                if member.voice and member.voice.channel and member.voice.channel.id == interaction.channel.id:
                    try:
                        await member.move_to(None)
                    except discord.HTTPException:
                        pass
                msg = f"🚫 Am scos accesul lui {member.mention}."
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "Nu am permisiuni să modific canalul.", ephemeral=True)


# ============================================================ COG
class TempVoice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(_panel_view())  # butoanele merg si dupa restart

    # ---------------------------------------------------------- creare/stergere
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        guild = member.guild

        # 1) a intrat in canalul GENERATOR -> ii facem canal propriu
        if after.channel is not None:
            cfg = _cfg(guild.id)
            gen_id = cfg.get("generator_id")
            if cfg.get("enabled") and gen_id and str(after.channel.id) == str(gen_id):
                await self._create_for(member, after.channel, cfg)

        # 2) a iesit dintr-un canal temporar care a ramas GOL -> il stergem
        if before.channel is not None:
            active = _active(guild.id)
            info = active.get(str(before.channel.id))
            if info is not None and len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="tempvoice: canal gol")
                except discord.HTTPException:
                    pass
                active.pop(str(before.channel.id), None)
                _save_active(guild.id, active)

    async def _create_for(self, member, gen_channel, cfg):
        guild = member.guild
        # categoria: cea aleasa in dashboard, altfel cea a generatorului
        category = None
        if cfg.get("category_id"):
            category = guild.get_channel(int(cfg["category_id"]))
        if category is None:
            category = gen_channel.category

        name = (cfg.get("name_tpl") or "🔊 {user}").replace("{user}", member.display_name)[:100]
        private = bool(cfg.get("default_private"))
        limit = int(cfg.get("user_limit", 0) or 0)

        overwrites = {
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True, move_members=True),
            member: discord.PermissionOverwrite(view_channel=True, connect=True),
        }
        if private:
            overwrites[guild.default_role] = discord.PermissionOverwrite(connect=False)

        try:
            ch = await guild.create_voice_channel(
                name=name, category=category, overwrites=overwrites,
                user_limit=limit, reason=f"tempvoice pentru {member}")
        except discord.Forbidden:
            return  # botul n-are Manage Channels
        except discord.HTTPException:
            return

        try:
            await member.move_to(ch)
        except discord.HTTPException:
            pass

        active = _active(guild.id)
        active[str(ch.id)] = {"owner_id": str(member.id), "private": private}
        _save_active(guild.id, active)

        # panoul in chat-ul canalului de voice
        try:
            await ch.send(content=member.mention,
                          embed=_panel_embed(member.id, private), view=_panel_view())
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------- butoane
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("tv:"):
            return

        active = _active(interaction.guild_id)
        info = active.get(str(interaction.channel_id))
        if info is None:
            return await interaction.response.send_message(
                "Acest canal nu mai e un canal temporar.", ephemeral=True)

        # doar PROPRIETARUL poate folosi butoanele
        if str(interaction.user.id) != str(info.get("owner_id")):
            return await interaction.response.send_message(
                "Doar proprietarul canalului poate folosi butoanele.", ephemeral=True)

        ch = interaction.channel
        try:
            if cid == "tv:lock":
                await ch.set_permissions(interaction.guild.default_role, connect=False,
                                         reason="tempvoice: privat")
                info["private"] = True
                active[str(ch.id)] = info
                _save_active(interaction.guild_id, active)
                await interaction.response.send_message(
                    "🔒 Canalul e acum **privat**. Folosește ➕ Permite ca să lași pe cineva să intre.",
                    ephemeral=True)
                await self._refresh_panel(ch, info)
            elif cid == "tv:unlock":
                await ch.set_permissions(interaction.guild.default_role, connect=True,
                                         reason="tempvoice: public")
                info["private"] = False
                active[str(ch.id)] = info
                _save_active(interaction.guild_id, active)
                await interaction.response.send_message(
                    "🔓 Canalul e acum **public** — toată lumea poate intra.", ephemeral=True)
                await self._refresh_panel(ch, info)
            elif cid == "tv:permit":
                await interaction.response.send_message(
                    "Alege pe cine lași să intre:", view=_UserPick(self, "permit"), ephemeral=True)
            elif cid == "tv:unpermit":
                await interaction.response.send_message(
                    "Alege cui îi scoți accesul:", view=_UserPick(self, "unpermit"), ephemeral=True)
            elif cid == "tv:rename":
                await interaction.response.send_modal(RenameModal(self))
            elif cid == "tv:limit":
                await interaction.response.send_modal(LimitModal(self))
        except discord.Forbidden:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Nu am permisiuni să modific canalul.", ephemeral=True)
        except discord.HTTPException:
            pass

    async def _refresh_panel(self, channel, info):
        """Actualizeaza textul panoului (ca sa arate starea curenta privat/public)."""
        try:
            async for msg in channel.history(limit=15):
                if msg.author.id == self.bot.user.id and msg.embeds:
                    await msg.edit(embed=_panel_embed(info.get("owner_id"), info.get("private")))
                    break
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------- curatenie la restart
    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            active = _active(guild.id)
            if not active:
                continue
            changed = False
            for chid in list(active.keys()):
                ch = guild.get_channel(int(chid))
                if ch is None:  # canalul nu mai exista
                    active.pop(chid, None)
                    changed = True
                elif len(ch.members) == 0:  # a ramas gol cat botul era oprit
                    try:
                        await ch.delete(reason="tempvoice: gol la pornire")
                    except discord.HTTPException:
                        pass
                    active.pop(chid, None)
                    changed = True
            if changed:
                _save_active(guild.id, active)


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVoice(bot))
