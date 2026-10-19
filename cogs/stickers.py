"""
cogs/stickers.py — descarca stickerele TRIMISE intr-un canal.

CE FACE:
  - Scaneaza istoricul unui canal si aduna toate stickerele trimise acolo
    (unice, fara duplicate).
  - Le descarca pe disc (folder per server) ca sa le poti lua din dashboard
    (unul anume sau toate ca .zip) SAU printr-o comanda in Discord.

Comanda (admin):
  /descarca_stickere [canal] [limita]  - scaneaza si posteaza un .zip in Discord

Dashboard: pagina "Stickere" -> alegi canal -> Scanează -> descarci individual sau tot.

Fisierele se salveaza in: data/stickers/<guild_id>/
Lista ultimei scanari in storage cheia "stickers_scan" per guild:
  { "ts", "channel_id", "channel_name", "items": [ {id, name, ext, fmt, file} ] }
"""
import os
import time
import asyncio

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import storage
from utils.perms import bot_access


# folderul unde salvam stickerele descarcate (langa cod, in data/)
_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "stickers")


def _guild_dir(guild_id):
    d = os.path.join(_BASE_DIR, str(guild_id))
    os.makedirs(d, exist_ok=True)
    return d


def _ext_for(fmt):
    """Extensia de fisier in functie de formatul stickerului."""
    try:
        name = fmt.name.lower()  # 'png' / 'apng' / 'lottie' / 'gif'
    except AttributeError:
        name = str(fmt).lower()
    if "lottie" in name:
        return "json"
    if "gif" in name:
        return "gif"
    if "apng" in name:
        return "png"   # APNG se salveaza tot ca .png
    return "png"


def _safe_name(name):
    keep = "".join(c for c in (name or "sticker") if c.isalnum() or c in " -_").strip()
    return (keep or "sticker")[:60]


async def scan_and_download(channel, limit=2000):
    """Scaneaza <limit> mesaje din canal, aduna stickerele UNICE si le descarca
    pe disc. Returneaza (items, err). items = [{id,name,ext,fmt,file,url}]."""
    guild = channel.guild
    seen = {}  # sticker_id -> StickerItem (unic)
    try:
        async for msg in channel.history(limit=limit):
            for st in (msg.stickers or []):
                if st.id not in seen:
                    seen[st.id] = st
    except discord.Forbidden:
        return [], "Nu am acces la istoricul acestui canal (lipsește Read Message History)."
    except discord.HTTPException as e:
        return [], f"Eroare la citirea canalului: {e}"

    if not seen:
        return [], None

    # curatam folderul vechi al acestui server inainte de o scanare noua
    gdir = _guild_dir(guild.id)
    for f in os.listdir(gdir):
        try:
            os.remove(os.path.join(gdir, f))
        except OSError:
            pass

    items = []
    async with aiohttp.ClientSession() as sess:
        for sid, st in seen.items():
            ext = _ext_for(getattr(st, "format", None))
            fname = f"{sid}_{_safe_name(st.name)}.{ext}"
            url = getattr(st, "url", None) or f"https://media.discordapp.net/stickers/{sid}.png"
            try:
                async with sess.get(str(url)) as r:
                    if r.status != 200:
                        continue
                    data = await r.read()
                with open(os.path.join(gdir, fname), "wb") as fh:
                    fh.write(data)
            except (aiohttp.ClientError, OSError):
                continue
            items.append({
                "id": str(sid), "name": st.name,
                "ext": ext, "fmt": _ext_for(getattr(st, "format", None)),
                "file": fname, "url": str(url),
            })
    return items, None


class Stickers(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="descarca_stickere",
        description="Descarca toate stickerele trimise intr-un canal (ca .zip)")
    @app_commands.describe(canal="Canalul de scanat (gol = canalul curent)",
                           limita="Cate mesaje sa scaneze (implicit 2000)")
    @bot_access()
    async def descarca_stickere(self, interaction: discord.Interaction,
                                canal: discord.TextChannel = None,
                                limita: int = 2000):
        channel = canal or interaction.channel
        await interaction.response.defer(ephemeral=True, thinking=True)
        items, err = await scan_and_download(channel, max(1, min(limita, 10000)))
        if err:
            return await interaction.followup.send(f"⚠️ {err}", ephemeral=True)
        if not items:
            return await interaction.followup.send(
                f"Nu am găsit niciun sticker în {channel.mention}.", ephemeral=True)

        # facem un zip in memorie si il trimitem (daca incape in limita Discord)
        import io
        import zipfile
        buf = io.BytesIO()
        gdir = _guild_dir(interaction.guild_id)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for it in items:
                fp = os.path.join(gdir, it["file"])
                if os.path.exists(fp):
                    z.write(fp, it["file"])
        size = buf.tell()
        buf.seek(0)

        # retinem scanarea si pentru dashboard
        storage.set(interaction.guild_id, "stickers_scan", {
            "ts": time.time(), "channel_id": str(channel.id),
            "channel_name": channel.name, "items": items,
        })

        limit_bytes = (interaction.guild.filesize_limit or 8 * 1024 * 1024)
        if size > limit_bytes:
            return await interaction.followup.send(
                f"Am găsit **{len(items)}** stickere, dar arhiva ({size//1024} KB) "
                f"e prea mare pentru Discord. Descarc-o din **dashboard → Stickere**.",
                ephemeral=True)
        await interaction.followup.send(
            content=f"✅ **{len(items)}** stickere din {channel.mention}:",
            file=discord.File(buf, filename=f"stickere_{channel.name}.zip"),
            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Stickers(bot))
