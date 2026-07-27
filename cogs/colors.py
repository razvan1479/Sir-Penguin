"""
cogs/colors.py — panou de CULORI la nume (self-assign prin butoane).

/culori panou  → posteaza un embed cu butoane de culoare. Membrii apasa si
primesc automat un rol colorat. Botul creeaza rolul de culoare doar prima data;
daca exista deja, il refoloseste (nu dubleaza). Un membru are o singura culoare
— la alegerea alteia, cea veche se scoate. Butonul "Scoate culoarea" o elimina.

Butoanele sunt persistente (merg si dupa ce botul reporneste).
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import bot_access


def _enabled(gid) -> bool:
    # pornit implicit; se poate dezactiva din dashboard
    return (storage.get(gid, "colors", {}) or {}).get("enabled", True)

# prefix pentru rolurile de culoare create de bot (ca sa le recunoastem)
ROLE_PREFIX = "🎨 "

# paleta de culori (nume, emoji-cerc, cod). Emoji-ul arata culoarea pe buton.
COLORS = [
    ("Roșu",          "❤️", 0xE74C3C),
    ("Coral",         "🧡", 0xFF7043),
    ("Portocaliu",    "🟠", 0xE67E22),
    ("Auriu",         "🟡", 0xF39C12),
    ("Galben",        "💛", 0xF1C40F),
    ("Verde-lămâie",  "🟢", 0xA3E635),
    ("Verde",         "💚", 0x2ECC71),
    ("Verde-închis",  "🌲", 0x1E8449),
    ("Turcoaz",       "🩵", 0x1ABC9C),
    ("Cyan",          "🔵", 0x00CEC9),
    ("Albastru",      "💙", 0x3498DB),
    ("Albastru-cer",  "🌊", 0x74B9FF),
    ("Indigo",        "🟦", 0x5C6BC0),
    ("Mov",           "💜", 0x9B59B6),
    ("Violet",        "🟣", 0xBB6BD9),
    ("Roz",           "🩷", 0xE84393),
    ("Roz-pal",       "🌸", 0xFDA7DF),
    ("Maro",          "🤎", 0xA04000),
    ("Alb",           "🤍", 0xFFFFFF),
    ("Gri",           "🩶", 0x95A5A6),
    ("Negru",         "🖤", 0x010101),
]


async def _assign_color(interaction: discord.Interaction, name: str, color_val: int):
    if not _enabled(interaction.guild_id):
        return await interaction.response.send_message(
            "Funcția de culori e dezactivată momentan.", ephemeral=True)
    guild = interaction.guild
    member = interaction.user
    role_name = f"{ROLE_PREFIX}{name}"

    # rolul exista deja? (refolosim, nu dublam)
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        try:
            role = await guild.create_role(
                name=role_name, colour=discord.Colour(color_val),
                reason=f"Rol de culoare (auto) cerut de {member}")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "Nu am permisiunea să creez roluri. Dă-mi permisiunea **Gestionează rolurile**.",
                ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message(
                "Nu am putut crea rolul de culoare acum. Încearcă din nou.", ephemeral=True)

        # mutam rolul cat mai sus (imediat sub rolul botului) ca sa se vada culoarea.
        # in Discord, culoarea numelui vine de la cel mai de sus rol COLORAT.
        try:
            bot_top = guild.me.top_role.position
            target = max(1, bot_top - 1)
            if role.position != target:
                await role.edit(position=target, reason="Rol de culoare sus, ca sa se vada")
        except (discord.Forbidden, discord.HTTPException):
            pass  # daca nu putem muta, ramane unde e (macar rolul exista)

    # scoatem orice ALTA culoare din sistemul asta (o singura culoare de nume)
    others = [r for r in member.roles if r.name.startswith(ROLE_PREFIX) and r.id != role.id]

    # daca deja are exact culoarea asta -> o scoatem (toggle off)
    if role in member.roles:
        try:
            await member.remove_roles(role, reason="A scos culoarea")
            return await interaction.response.send_message(
                f"{name} — ți-am scos culoarea. 🤍", ephemeral=True)
        except discord.Forbidden:
            return await interaction.response.send_message(
                "Nu pot modifica rolurile tale (rolul meu trebuie să fie mai sus).", ephemeral=True)

    try:
        if others:
            await member.remove_roles(*others, reason="Schimbare culoare")
        await member.add_roles(role, reason="A ales o culoare")
    except discord.Forbidden:
        return await interaction.response.send_message(
            "Nu pot să-ți pun rolul — **mută rolul botului mai sus** decât rolurile de culoare.",
            ephemeral=True)

    await interaction.response.send_message(
        f"Gata! Ți-am pus culoarea **{name}**. 🎨", ephemeral=True)


class ColorButton(discord.ui.Button):
    def __init__(self, name: str, emoji: str, color_val: int):
        super().__init__(style=discord.ButtonStyle.secondary, label=name, emoji=emoji,
                         custom_id=f"color:{color_val:06x}:{name}")
        self._name = name
        self._color = color_val

    async def callback(self, interaction: discord.Interaction):
        await _assign_color(interaction, self._name, self._color)


class RemoveColorButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="Scoate culoarea",
                         emoji="🚫", custom_id="color:remove", row=4)

    async def callback(self, interaction: discord.Interaction):
        if not _enabled(interaction.guild_id):
            return await interaction.response.send_message(
                "Funcția de culori e dezactivată momentan.", ephemeral=True)
        member = interaction.user
        mine = [r for r in member.roles if r.name.startswith(ROLE_PREFIX)]
        if not mine:
            return await interaction.response.send_message(
                "Nu ai nicio culoare pusă. 🤍", ephemeral=True)
        try:
            await member.remove_roles(*mine, reason="A scos culoarea")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "Nu pot modifica rolurile tale (rolul meu trebuie să fie mai sus).", ephemeral=True)
        await interaction.response.send_message("Ți-am scos culoarea. 🤍", ephemeral=True)


class ColorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistent
        for name, emoji, color in COLORS:
            self.add_item(ColorButton(name, emoji, color))
        self.add_item(RemoveColorButton())


class Colors(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="culori", description="Panou de culori la nume")

    @group.command(name="panou", description="Posteaza panoul cu butoane de culoare")
    @bot_access()
    async def panou(self, interaction: discord.Interaction):
        if not _enabled(interaction.guild_id):
            return await interaction.response.send_message(
                "Activează întâi funcția din dashboard (Distracție → Culori la nume).",
                ephemeral=True)
        embed = discord.Embed(
            title="🎨 Alege-ți o culoare la nume",
            description=("Apasă pe un buton mai jos ca să-ți pui culoarea aleasă.\n"
                         "Poți schimba oricând — apeși altă culoare și se schimbă.\n"
                         "Ca s-o scoți, apasă **🚫 Scoate culoarea**."),
            color=discord.Color(0x8B5CF6))
        embed.set_footer(text="Fiecare membru poate avea o singură culoare.")
        await interaction.response.send_message(embed=embed, view=ColorView())


async def setup(bot: commands.Bot):
    await bot.add_cog(Colors(bot))
    bot.add_view(ColorView())  # inregistram panoul ca persistent (merge dupa restart)
