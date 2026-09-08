"""
cogs/automod.py — sistem de moderare automata (filtru de limbaj neadecvat).

CE FACE:
  - Verifica mesajele DINTR-UN SINGUR canal configurabil (nu tot serverul).
  - Compara textul (normalizat) cu o lista de cuvinte interzise, configurabila.
  - Daca gaseste un cuvant interzis:
      1. sterge mesajul
      2. aplica timeout (durata configurabila), DAR nu scurteaza un timeout
         deja mai lung pe care userul il are
      3. NU trimite DM si NU posteaza niciun mesaj in canal — doar stergerea
         si timeout-ul, fara nimic vizibil

CE NU FACE:
  - Nu ignora Administratorii (skip automat).
  - Nu se declanseaza pe mesajele botului insusi.
  - Nu atinge alte canale in afara celui configurat.

Storage (cheia "automod", per guild):
  enabled, channel_id, timeout_minutes, banned_words (lista de string-uri)
"""
import re
import datetime

import discord
from discord.ext import commands

from utils import storage

DEFAULT_TIMEOUT_MINUTES = 2

# Substituiri comune folosite pentru a "masca" un cuvant (leetspeak simplu).
# Nu e un filtru perfect (niciun filtru de cuvinte nu poate fi 100%), dar
# prinde cele mai frecvente variante.
_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
    "@": "a", "$": "s", "!": "i",
})


def _normalize(text: str) -> str:
    """Litere mici + inlocuiri leetspeak + scoate tot ce nu e litera/cifra
    (spatii, cratime, puncte etc, des folosite ca sa desparta literele unui
    cuvant interzis) + reduce literele repetate (aaaa -> a) ca sa prinda si
    variante scrise "intins"."""
    t = text.lower().translate(_LEET)
    t = re.sub(r"[^a-z0-9]", "", t)
    t = re.sub(r"(.)\1{2,}", r"\1", t)  # 3+ repetari identice -> 1 singura
    return t


def _cfg(gid):
    return storage.get(gid, "automod", {}) or {}


def _is_admin(member) -> bool:
    """True daca membrul are permisiunea Administrator. Separata intr-o
    functie proprie ca sa fie usor de testat fara un obiect discord.Member
    real (doar API-ul .guild_permissions.administrator conteaza)."""
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and getattr(perms, "administrator", False))


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return  # ignoram alte boturi (inclusiv pe noi) si DM-urile

        cfg = _cfg(message.guild.id)
        if not cfg.get("enabled", False):
            return
        channel_id = cfg.get("channel_id")
        if not channel_id or message.channel.id != int(channel_id):
            return  # verificam STRICT doar canalul configurat

        member = message.author
        if _is_admin(member):
            return  # Administratorii sunt ignorati intotdeauna

        words = cfg.get("banned_words") or []
        if not words:
            return

        norm = _normalize(message.content or "")
        hit = next((w for w in words if w and _normalize(w) in norm), None)
        if not hit:
            return

        # 1) stergem mesajul
        try:
            await message.delete()
        except discord.Forbidden:
            return  # nu are Manage Messages -> nu putem continua sigur
        except discord.NotFound:
            pass  # a fost deja sters intre timp

        # 2) timeout — dar nu scurtam unul deja mai lung
        minutes = cfg.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES)
        new_until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        current_until = getattr(member, "timed_out_until", None)
        timeout_applied = False
        if current_until is None or current_until < new_until:
            try:
                await member.timeout(new_until, reason="Limbaj neadecvat (automod)")
                timeout_applied = True
            except discord.Forbidden:
                pass  # lipseste "Moderate Members" sau rolul botului e prea jos
            except discord.HTTPException:
                pass
        # Nu se posteaza niciun mesaj de avertizare — doar stergerea + timeout-ul,
        # fara nimic vizibil in canal (la cererea explicita a userului).


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
