"""
cogs/invitesources.py — linkuri de invite ETICHETATE pe surse (site, TikTok, etc.)

Ideea: creezi un invite dedicat unei surse (ex. "Site"), il pui pe site, si botul
iti spune cati oameni si CINE anume a intrat prin el. Se bazeaza pe faptul ca
modulul de invitatii (cogs/invites.py) salveaza deja codul de invite in istoricul
fiecarei intrari — aici doar mapam coduri -> etichete si citim din acel istoric.

Nu atinge logica din cogs/invites.py; doar citeste datele ei.

Stocare (per guild, cheia "invite_sources"):
  { code: {"label": "Site", "created_ts": ..., "created_by": user_id} }

Comenzi:
  /sursa creaza <nume>   - creeaza un invite etichetat si iti da linkul
  /sursa lista           - toate sursele, cu cati au intrat prin fiecare
  /sursa cine <nume>     - lista membrilor care au intrat de pe acea sursa
  /sursa sterge <nume>   - scoate eticheta (invite-ul de pe Discord ramane)
"""
import time
import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import has_bot_access, bot_access


def _sources(gid):
    return storage.get(gid, "invite_sources", {}) or {}


def _save_sources(gid, data):
    storage.set(gid, "invite_sources", data)


def _find_code_by_label(gid, label):
    for code, s in _sources(gid).items():
        if (s.get("label") or "").strip().lower() == label.strip().lower():
            return code, s
    return None, None


def _joins_for_code(gid, code):
    """Din istoricul modulului de invitatii, cine a intrat prin acest cod.
    Intoarce lista de dict-uri {member, member_name, ts, left}."""
    hist = (storage.get(gid, "invites", {}) or {}).get("history", [])
    seen = set()
    out = []
    for e in hist:
        if e.get("code") != code:
            continue
        mid = e.get("member")
        if mid in seen:
            continue  # o persoana o numaram o data (chiar daca a intrat de mai multe ori)
        seen.add(mid)
        out.append({"member": mid, "member_name": e.get("member_name") or mid,
                    "ts": e.get("ts", 0), "left": e.get("left", False)})
    return out


class InviteSources(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="sursa",
                               description="Linkuri de invite etichetate pe surse (site, etc.)")

    @group.command(name="creaza", description="Creeaza un link de invite etichetat (ex. pentru site)")
    @app_commands.describe(nume="Numele sursei (ex. Site, TikTok, YouTube)",
                           canal="Canalul spre care duce invite-ul (implicit: cel curent)")
    @bot_access()
    async def creaza(self, interaction: discord.Interaction, nume: str,
                     canal: discord.TextChannel = None):
        target = canal or interaction.channel
        # verificam sa nu existe deja o sursa cu acelasi nume
        existing_code, _ = _find_code_by_label(interaction.guild_id, nume)
        if existing_code:
            link = f"https://discord.gg/{existing_code}"
            return await interaction.response.send_message(
                f"⚠️ Exista deja o sursă numită **{nume}**: {link}\n"
                f"Folosește alt nume sau șterge-o cu `/sursa sterge {nume}`.", ephemeral=True)
        try:
            invite = await target.create_invite(
                max_age=0, max_uses=0, unique=True,
                reason=f"Sursă invite: {nume}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"Nu am putut crea invite-ul: `{e}` (verifică permisiunile botului "
                f"în {target.mention}).", ephemeral=True)

        srcs = _sources(interaction.guild_id)
        srcs[invite.code] = {"label": nume, "created_ts": time.time(),
                             "created_by": str(interaction.user.id)}
        _save_sources(interaction.guild_id, srcs)

        await interaction.response.send_message(
            f"✅ Link pentru **{nume}** creat:\n{invite.url}\n\n"
            f"Pune-l pe site. Vezi cine intră prin el cu `/sursa cine {nume}` "
            f"sau din dashboard → Surse invite.", ephemeral=True)

    @group.command(name="lista", description="Toate sursele si cati oameni au intrat prin fiecare")
    @bot_access()
    async def lista(self, interaction: discord.Interaction):
        srcs = _sources(interaction.guild_id)
        if not srcs:
            return await interaction.response.send_message(
                "Nu ai nicio sursă încă. Creează una cu `/sursa creaza <nume>`.", ephemeral=True)
        lines = []
        for code, s in sorted(srcs.items(), key=lambda kv: kv[1].get("label", "")):
            joins = _joins_for_code(interaction.guild_id, code)
            present = sum(1 for j in joins if not j["left"])
            left = sum(1 for j in joins if j["left"])
            lines.append(f"**{s.get('label')}** — {present} pe server"
                         f"{f' · {left} au plecat' if left else ''}  \n"
                         f"`https://discord.gg/{code}`")
        await interaction.response.send_message(
            "📊 **Surse de invite:**\n\n" + "\n\n".join(lines), ephemeral=True)

    @group.command(name="cine", description="Cine a intrat de pe o anumita sursa")
    @app_commands.describe(nume="Numele sursei (ex. Site)")
    @bot_access()
    async def cine(self, interaction: discord.Interaction, nume: str):
        code, s = _find_code_by_label(interaction.guild_id, nume)
        if not code:
            return await interaction.response.send_message(
                f"Nu există o sursă numită **{nume}**. Vezi `/sursa lista`.", ephemeral=True)
        joins = _joins_for_code(interaction.guild_id, code)
        if not joins:
            return await interaction.response.send_message(
                f"Încă n-a intrat nimeni de pe **{nume}**.", ephemeral=True)
        joins.sort(key=lambda j: -j["ts"])
        lines = []
        for j in joins[:40]:
            mark = "✅" if not j["left"] else "❌"
            when = f"<t:{int(j['ts'])}:R>" if j["ts"] else ""
            lines.append(f"{mark} <@{j['member']}> ({j['member_name']}) {when}")
        present = sum(1 for j in joins if not j["left"])
        more = f"\n\n…și încă {len(joins) - 40}." if len(joins) > 40 else ""
        await interaction.response.send_message(
            f"👥 **Intrați de pe „{nume}”** — {present} pe server acum "
            f"(din {len(joins)} total):\n\n" + "\n".join(lines) + more, ephemeral=True)

    @group.command(name="sterge", description="Sterge eticheta unei surse (invite-ul de pe Discord ramane)")
    @app_commands.describe(nume="Numele sursei de sters")
    @bot_access()
    async def sterge(self, interaction: discord.Interaction, nume: str):
        code, s = _find_code_by_label(interaction.guild_id, nume)
        if not code:
            return await interaction.response.send_message(
                f"Nu există o sursă numită **{nume}**.", ephemeral=True)
        srcs = _sources(interaction.guild_id)
        srcs.pop(code, None)
        _save_sources(interaction.guild_id, srcs)
        await interaction.response.send_message(
            f"🗑️ Sursa **{nume}** a fost ștearsă din evidență. "
            f"(Invite-ul `discord.gg/{code}` încă funcționează pe Discord — "
            f"dacă vrei să nu mai meargă, șterge-l din Setări server → Invitații.)",
            ephemeral=True)

    @creaza.autocomplete("nume")
    @cine.autocomplete("nume")
    @sterge.autocomplete("nume")
    async def _label_ac(self, interaction: discord.Interaction, current: str):
        labels = [s.get("label", "") for s in _sources(interaction.guild_id).values()]
        return [app_commands.Choice(name=l, value=l)
                for l in labels if current.lower() in l.lower()][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(InviteSources(bot))
