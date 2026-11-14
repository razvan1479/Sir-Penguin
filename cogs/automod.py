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

  links: {                      # REGULA DE LINKURI (separata de filtrul de cuvinte)
    enabled, channel_ids [list], role_ids [list],
    threshold (cate linkuri), window_minutes (in cat timp),
    mute_minutes (durata mute)
  }
  Pe canalele alese, daca un membru cu unul din rolurile alese pune un link,
  mesajul se sterge. Daca repeta de <threshold> ori in <window_minutes>, primeste
  mute <mute_minutes>. Daca NU alegi niciun rol -> regula se aplica tuturor
  (fara Administratori si boti).
"""
import re
import time
import datetime

import discord
from discord.ext import commands

from utils import storage

DEFAULT_TIMEOUT_MINUTES = 2

# Detecteaza linkuri: protocol explicit, www., invitatii discord, si domenii
# "goale" cu un TLD cunoscut (ex. site.com, server.ro). Acopera cazurile uzuale
# de pe un server de Metin2 (alte servere, invitatii discord etc).
_LINK_RE = re.compile(
    r"(https?://\S+"
    r"|www\.\S+"
    r"|discord\.(?:gg|com/invite|me)/\S+"
    r"|\b[a-z0-9][a-z0-9\-]*\.(?:com|net|org|ro|gg|io|me|tv|xyz|co|info|biz|"
    r"online|store|shop|link|site|app|dev|eu|uk|de|fr|es|it|pl|ru|fun|gg)\b"
    r"(?:/\S*)?)",
    re.IGNORECASE,
)

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
        # evidenta in memorie a linkurilor sterse recent, per (guild, user):
        # lista de timestamp-uri. Se curata singura (fereastra de timp), deci
        # ramane mica — nu creste memoria. Se reseteaza la restart (nu conteaza
        # pentru o fereastra de cateva minute).
        self._link_hits: dict = {}
        # la fel, dar pentru incercarile de a mentiona persoane protejate
        self._mention_hits: dict = {}

    # ---------------------------------------------------- filtru de LINKURI
    async def _maybe_filter_links(self, message: discord.Message) -> bool:
        """Returneaza True daca a tratat mesajul (l-a sters). Altfel False."""
        cfg = _cfg(message.guild.id)
        lk = cfg.get("links") or {}
        if not lk.get("enabled"):
            return False

        channel_ids = {str(c) for c in (lk.get("channel_ids") or [])}
        if str(message.channel.id) not in channel_ids:
            return False  # nu e un canal vizat

        member = message.author
        if _is_admin(member):
            return False  # Administratorii sunt mereu scutiti

        # roluri vizate: daca ai ales roluri, regula se aplica DOAR celor care au
        # unul din ele. Daca nu ai ales niciun rol -> se aplica tuturor.
        role_ids = {str(r) for r in (lk.get("role_ids") or [])}
        if role_ids:
            member_roles = {str(r.id) for r in getattr(member, "roles", [])}
            if not (role_ids & member_roles):
                return False  # nu are niciun rol vizat -> il lasam in pace

        if not _LINK_RE.search(message.content or ""):
            return False  # nu contine link

        # 1) stergem mesajul cu link
        try:
            await message.delete()
        except discord.Forbidden:
            return False  # fara Manage Messages -> nu putem face nimic sigur
        except discord.NotFound:
            pass  # deja sters

        # 2) numaram incalcarile in fereastra de timp
        threshold = max(1, int(lk.get("threshold", 3)))
        window = max(1, int(lk.get("window_minutes", 5))) * 60
        now = time.time()
        key = (message.guild.id, member.id)
        hits = [t for t in self._link_hits.get(key, []) if now - t < window]
        hits.append(now)
        self._link_hits[key] = hits
        self._prune_hits(now)

        # 3) daca a atins pragul -> mute pe durata aleasa
        mins = max(1, int(lk.get("mute_minutes", 5)))
        if len(hits) >= threshold:
            self._link_hits[key] = []  # resetam dupa ce dam mute
            until = discord.utils.utcnow() + datetime.timedelta(minutes=mins)
            current_until = getattr(member, "timed_out_until", None)
            if current_until is None or current_until < until:
                try:
                    await member.timeout(until, reason="Spam linkuri (automod)")
                except discord.Forbidden:
                    pass  # lipseste "Moderate Members" sau rolul botului e prea jos
                except discord.HTTPException:
                    pass
        else:
            # inca nu a atins pragul -> ii trimitem un avertisment (daca e pornit)
            await self._send_link_warning(message, member, lk, mins)
        return True

    async def _send_link_warning(self, message, member, lk, mute_minutes):
        """Avertisment cand i se sterge un link (inainte de a primi mute).
        mod 'channel' = mesaj scurt in canal care-l mentioneaza, auto-sters;
        mod 'dm' = mesaj privat. Gol/oprit = nu trimite nimic."""
        if not lk.get("warn_enabled"):
            return
        default_text = ("⚠️ {user}, linkurile nu sunt permise aici. "
                        "Dacă mai încerci, primești mute automat {mute} minute.")
        text = (lk.get("warn_text") or default_text)
        text = text.replace("{user}", member.mention).replace("{mute}", str(mute_minutes))

        if lk.get("warn_mode") == "dm":
            try:
                await member.send(text)
            except (discord.Forbidden, discord.HTTPException):
                pass  # are DM-urile inchise -> nu putem face nimic
            return

        # mod implicit: mesaj in canal, auto-sters dupa cateva secunde
        secs = max(1, min(int(lk.get("warn_delete_seconds", 8)), 60))
        try:
            await message.channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=True),
                delete_after=secs)
        except discord.HTTPException:
            pass

    def _prune_hits(self, now: float):
        """Scoate intrarile vechi ca sa nu creasca memoria (rar, doar cand e cazul)."""
        for d in (self._link_hits, self._mention_hits):
            if len(d) < 500:
                continue
            for k in list(d.keys()):
                if not d[k] or now - d[k][-1] > 3600:
                    d.pop(k, None)

    # ----------------------------------------- filtru de MENTIUNI protejate
    async def _maybe_filter_mentions(self, message: discord.Message) -> bool:
        """Blocheaza mentionarea anumitor persoane. Returneaza True daca a
        tratat mesajul (l-a sters). Cine incearca repetat primeste mute."""
        cfg = _cfg(message.guild.id)
        mn = cfg.get("mentions") or {}
        if not mn.get("enabled"):
            return False

        protected = {str(x) for x in (mn.get("protected_ids") or [])}
        if not protected:
            return False  # n-ai adaugat nicio persoana protejata

        # unde se aplica: "all" = tot serverul; "channels" = doar canalele alese
        if mn.get("scope", "all") == "channels":
            channel_ids = {str(c) for c in (mn.get("channel_ids") or [])}
            # daca ai pus pe "doar canale alese" dar n-ai bifat niciunul,
            # tratam ca "tot serverul" (altfel regula n-ar face nimic)
            if channel_ids and str(message.channel.id) not in channel_ids:
                return False

        member = message.author
        if _is_admin(member):
            return False  # Administratorii sunt mereu scutiti

        # persoane scutite individual (pe ID) -> au voie sa mentioneze
        exempt_users = {str(x) for x in (mn.get("exempt_user_ids") or [])}
        if str(member.id) in exempt_users:
            return False

        # roluri scutite (ex. staff) -> au voie sa mentioneze persoanele protejate
        exempt = {str(r) for r in (mn.get("exempt_role_ids") or [])}
        if exempt:
            member_roles = {str(r.id) for r in getattr(member, "roles", [])}
            if exempt & member_roles:
                return False

        # ce persoane au fost mentionate: mentiuni scrise (<@id>) + ping la reply
        mentioned = {str(u.id) for u in getattr(message, "mentions", [])}
        mentioned |= {str(i) for i in getattr(message, "raw_mentions", [])}
        if not (protected & mentioned):
            return False  # nu a mentionat nicio persoana protejata

        # daca autorul e chiar persoana protejata, il lasam in pace
        if str(member.id) in protected:
            return False

        # 1) stergem mesajul (daca e activat; implicit DA)
        if mn.get("delete_message", True):
            try:
                await message.delete()
            except discord.Forbidden:
                return False  # fara Manage Messages -> nu putem face nimic sigur
            except discord.NotFound:
                pass

        # 2) numaram incercarile in fereastra de timp
        threshold = max(1, int(mn.get("threshold", 3)))
        window = max(1, int(mn.get("window_minutes", 5))) * 60
        now = time.time()
        key = (message.guild.id, member.id)
        hits = [t for t in self._mention_hits.get(key, []) if now - t < window]
        hits.append(now)
        self._mention_hits[key] = hits
        self._prune_hits(now)

        # 3) daca a atins pragul -> mute pe durata aleasa
        mins = max(1, int(mn.get("mute_minutes", 5)))
        if len(hits) >= threshold:
            self._mention_hits[key] = []  # resetam dupa ce dam mute
            until = discord.utils.utcnow() + datetime.timedelta(minutes=mins)
            current_until = getattr(member, "timed_out_until", None)
            if current_until is None or current_until < until:
                try:
                    await member.timeout(until, reason="Mentionare persoana protejata (automod)")
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass
        else:
            await self._send_mention_warning(message, member, mn, mins)
        return True

    async def _send_mention_warning(self, message, member, mn, mute_minutes):
        """Avertisment cand incearca sa mentioneze o persoana protejata (inainte
        de mute). La fel ca la linkuri: 'channel' (in canal, auto-sters) sau 'dm'."""
        if not mn.get("warn_enabled"):
            return
        default_text = ("⚠️ {user}, nu ai voie să menționezi această persoană. "
                        "Dacă mai încerci, primești mute automat {mute} minute.")
        text = (mn.get("warn_text") or default_text)
        text = text.replace("{user}", member.mention).replace("{mute}", str(mute_minutes))

        if mn.get("warn_mode") == "dm":
            try:
                await member.send(text)
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        secs = max(1, min(int(mn.get("warn_delete_seconds", 8)), 60))
        try:
            await message.channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=True),
                delete_after=secs)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return  # ignoram alte boturi (inclusiv pe noi) si DM-urile

        # intai filtrul de linkuri; daca a sters mesajul, ne oprim aici
        if await self._maybe_filter_links(message):
            return

        # apoi protectia la mentiuni; daca a sters mesajul, ne oprim aici
        if await self._maybe_filter_mentions(message):
            return

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
        # IMPORTANT: ignoram cuvintele care, dupa normalizare, raman sub 3 litere.
        # Altfel un cuvant format doar din diacritice/semne (ex. "???", "ăăă") s-ar
        # normaliza la "" si ar sterge ORICE mesaj. Un cuvant cu 1-2 litere e prea
        # scurt oricum (ar prinde subsiruri din cuvinte normale).
        hit = next((w for w in words
                    if w and len(_normalize(w)) >= 3 and _normalize(w) in norm), None)
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
