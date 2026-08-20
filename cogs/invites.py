"""
cogs/invites.py — sistem de INVITATII complet (stil Invite Tracker).

DIFERENTA fata de varianta simpla: invitatiile se impart pe CATEGORII:
  ✅ reale   (regular) - au intrat prin tine si sunt inca pe server
  ❌ plecate (left)    - au intrat prin tine dar au plecat
  🚫 false   (fake)    - cont prea nou (sub 7 zile) -> anti-trisare
  🎁 bonus   (bonus)   - adaugate manual de admin

  TOTAL = reale + bonus - plecate - false

CUM detectam cine a invitat: tinem in memorie un "cache" cu folosirile fiecarei
invitatii; cand intra cineva, comparam si vedem ce invitatie a crescut.
  - link personal -> +1 la cel care l-a creat
  - link personalizat al serverului (vanity) -> numarat separat
  - nedetectabil -> "necunoscut"

NECESITA permisiunea "Manage Server"!

Comenzi user:
  /invites [membru]      - numarul si detalierea invitatiilor
  /inviter <membru>      - cine a invitat membrul respectiv
  /invitedlist [membru]  - lista celor invitati de cineva
  /invitecodes [membru]  - codurile de invitatie ale cuiva (cu folosiri)
  /findlink              - unul dintre linkurile tale de invitatie
  /leaderboard [rol]     - clasamentul invitatorilor

Comenzi admin:
  /addinvites <membru> <numar>     - adauga invitatii bonus
  /removeinvites <membru> <numar>  - scade invitatii bonus
  /resetinvites [membru]           - reseteaza tot (sau un singur membru)
"""

import asyncio
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage
from utils.perms import bot_access

# cont mai nou de atatea zile = considerat "fals" (anti-trisare)
FAKE_DAYS = 7


def invite_total(stats: dict) -> int:
    """Formula de total a invitatiilor (ca la Invite Tracker). Niciodata sub 0."""
    total = (stats.get("regular", 0) + stats.get("bonus", 0)
             - stats.get("left", 0) - stats.get("fake", 0))
    return max(0, total)


def _empty_stats() -> dict:
    return {"regular": 0, "left": 0, "fake": 0, "bonus": 0}


class Invites(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.invite_cache: dict[int, dict[str, int]] = {}
        self.vanity_cache: dict[int, int] = {}
        self._join_locks: dict[int, asyncio.Lock] = {}
        self.contest_loop.start()
        self.resync_loop.start()

    def _lock_for(self, gid: int) -> "asyncio.Lock":
        lock = self._join_locks.get(gid)
        if lock is None:
            lock = asyncio.Lock()
            self._join_locks[gid] = lock
        return lock

    def cog_unload(self):
        self.contest_loop.cancel()
        self.resync_loop.cancel()

    # ------------------------------------------------------------- re-sincronizare
    @tasks.loop(minutes=5)
    async def resync_loop(self):
        """Reimprospateaza periodic lista de invitatii, ca sa prinda invitatii noi
        pe care le-am ratat (ex. eveniment pierdut). 'Bland': adauga codurile noi si
        scoate cele sterse, DAR nu atinge folosirile invitatiilor deja urmarite,
        ca sa nu strice o detectie in curs."""
        for guild in list(self.bot.guilds):
            try:
                invites = await guild.invites()
            except (discord.Forbidden, discord.HTTPException):
                continue
            live = {inv.code: (inv.uses or 0) for inv in invites}
            async with self._lock_for(guild.id):
                cache = self.invite_cache.setdefault(guild.id, {})
                for code, uses in live.items():
                    if code not in cache:
                        cache[code] = uses  # invitatie noua pe care am ratat-o
                for code in list(cache.keys()):
                    if code not in live:
                        cache.pop(code, None)  # invitatie stearsa
            if "VANITY_URL" in guild.features and guild.id not in self.vanity_cache:
                try:
                    v = await guild.vanity_invite()
                    self.vanity_cache[guild.id] = (v.uses or 0) if v else 0
                except discord.HTTPException:
                    pass
            # reconciliem plecarile cu realitatea si pentru jurnal/clasament general
            # (nu doar in concurs), ca numerele sa fie mereu corecte
            try:
                await self._reconcile_leaves(guild, 0)
            except Exception:
                pass

    @resync_loop.before_loop
    async def _before_resync(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------- cache
    async def _cache_guild(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
            self.invite_cache[guild.id] = {inv.code: inv.uses or 0 for inv in invites}
        except (discord.Forbidden, discord.HTTPException):
            self.invite_cache[guild.id] = {}
        if "VANITY_URL" in guild.features:
            try:
                v = await guild.vanity_invite()
                self.vanity_cache[guild.id] = (v.uses or 0) if v else 0
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._cache_guild(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self._cache_guild(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        self.invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        self.invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

    # ------------------------------------------------------------- detectare
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        info = {"type": "unknown", "inviter_id": None, "inviter_name": None, "code": None}
        # procesam intrarile pe RAND (per server) ca sa nu se incurce comparatia
        # numarului de folosiri cand intra multi oameni deodata (ex. la concurs)
        async with self._lock_for(guild.id):
            try:
                before = self.invite_cache.get(guild.id, {})
                after_list = await guild.invites()
                after = {inv.code: inv for inv in after_list}
                used = None
                for code, inv in after.items():
                    if (inv.uses or 0) > before.get(code, 0):
                        used = inv
                        break
                self.invite_cache[guild.id] = {inv.code: inv.uses or 0 for inv in after_list}

                if used and used.inviter:
                    info = {"type": "personal", "inviter_id": used.inviter.id,
                            "inviter_name": str(used.inviter), "code": used.code}
                elif "VANITY_URL" in guild.features:
                    try:
                        v = await guild.vanity_invite()
                        if v and (v.uses or 0) > self.vanity_cache.get(guild.id, 0):
                            self.vanity_cache[guild.id] = v.uses or 0
                            info = {"type": "vanity", "inviter_id": None,
                                    "inviter_name": None, "code": guild.vanity_url_code}
                    except discord.HTTPException:
                        pass
            except (discord.Forbidden, discord.HTTPException):
                pass
        inviter_key, is_fake, rejoin = self._record_join(guild, member, info)
        self.bot.dispatch("invite_join", member, info)

    def _record_join(self, guild, member, info):
        import time
        data = storage.get(guild.id, "invites", {})
        members = data.get("members", {})
        joined_by = data.get("joined_by", {})
        history = data.get("history", [])

        # cont prea nou = posibil fals
        age = datetime.datetime.now(datetime.timezone.utc) - member.created_at
        is_fake = age.days < FAKE_DAYS

        credited = data.get("credited", {})  # member_id -> inviterul care a primit creditul prima data
        rejoin = False

        if info["type"] == "personal" and info["inviter_id"]:
            inviter_key = str(info["inviter_id"])
            if str(member.id) in credited:
                # ANTI-ABUZ: persoana a mai fost invitata o data -> NU dam credit din nou
                # (cineva intra, iese si e re-invitat ca sa creasca artificial contorul)
                rejoin = True
                first_inviter = credited[str(member.id)]
                # daca se intoarce la acelasi invitator, anulam penalizarea de plecare
                if first_inviter == inviter_key and not is_fake:
                    st = members.setdefault(inviter_key, _empty_stats())
                    if st.get("left", 0) > 0:
                        st["left"] -= 1
                inviter_key = first_inviter  # creditul ramane la primul invitator
            else:
                # prima data cand e invitata aceasta persoana -> credit normal
                stats = members.setdefault(inviter_key, _empty_stats())
                # contul nou (fake) creste si regular, si fake -> se anuleaza (net 0),
                # nu penalizeaza invitatorul sub zero
                stats["regular"] += 1
                if is_fake:
                    stats["fake"] += 1
                credited[str(member.id)] = inviter_key
                data["credited"] = credited
        elif info["type"] == "vanity":
            inviter_key = "vanity"
            data["vanity_count"] = data.get("vanity_count", 0) + 1
        else:
            inviter_key = "unknown"

        joined_by[str(member.id)] = {"inviter": inviter_key, "code": info.get("code"), "fake": is_fake}
        # istoricul cu data + nume (pentru leaderboard pe saptamana/luna si jurnalul din dashboard)
        history.append({"member": str(member.id), "member_name": str(member),
                        "inviter": inviter_key, "inviter_name": info.get("inviter_name"),
                        "code": info.get("code"), "ts": time.time(),
                        "fake": is_fake, "left": False, "rejoin": rejoin})

        data["members"] = members
        data["joined_by"] = joined_by
        data["history"] = history
        storage.set(guild.id, "invites", data)
        return inviter_key, is_fake, rejoin

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        data = storage.get(member.guild.id, "invites", {})
        joined_by = data.get("joined_by", {})
        rec = joined_by.pop(str(member.id), None)

        if rec and rec["inviter"] not in ("vanity", "unknown"):
            members = data.get("members", {})
            stats = members.setdefault(rec["inviter"], _empty_stats())
            if rec.get("fake"):
                pass  # cont fals care pleaca ramane "fals" - nu se penalizeaza suplimentar
            else:
                # formula totalului scade deja "left", deci NU mai scadem si din
                # "regular" (altfel s-ar penaliza de doua ori aceeasi plecare)
                stats["left"] += 1
            data["members"] = members
        elif rec and rec["inviter"] == "vanity":
            data["vanity_count"] = max(0, data.get("vanity_count", 0) - 1)

        # marcam in istoric ca a plecat (ca sa nu se numere in leaderboardul pe perioada)
        for e in reversed(data.get("history", [])):
            if e.get("member") == str(member.id) and not e.get("left"):
                e["left"] = True
                break

        data["joined_by"] = joined_by
        storage.set(member.guild.id, "invites", data)

    # ------------------------------------------------------------- helper
    def _stats_for(self, guild_id, user_id) -> dict:
        members = storage.get(guild_id, "invites", {}).get("members", {})
        return members.get(str(user_id), _empty_stats())

    # ------------------------------------------------------------- comenzi user
    @app_commands.command(name="invites", description="Vezi numarul si detalierea invitatiilor")
    async def invites(self, interaction: discord.Interaction, membru: discord.Member = None):
        target = membru or interaction.user
        s = self._stats_for(interaction.guild_id, target.id)
        total = invite_total(s)
        embed = discord.Embed(
            title=f"📨 Invitatiile lui {target.display_name}",
            description=f"# {total} invitatii",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="✅ Reale", value=str(s["regular"]), inline=True)
        embed.add_field(name="❌ Plecate", value=str(s["left"]), inline=True)
        embed.add_field(name="🚫 False", value=str(s["fake"]), inline=True)
        embed.add_field(name="🎁 Bonus", value=str(s["bonus"]), inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="inviter", description="Cine a invitat un membru")
    async def inviter(self, interaction: discord.Interaction, membru: discord.Member):
        joined_by = storage.get(interaction.guild_id, "invites", {}).get("joined_by", {})
        rec = joined_by.get(str(membru.id))
        if not rec:
            return await interaction.response.send_message(
                f"Nu stiu cine a invitat pe {membru.mention} (a intrat inainte sa pornesc urmarirea).",
                ephemeral=True)
        src = rec["inviter"]
        if src == "vanity":
            msg = f"🔗 {membru.mention} a intrat prin linkul personalizat al serverului."
        elif src == "unknown":
            msg = f"❔ Sursa invitatiei lui {membru.mention} nu a putut fi determinata."
        else:
            extra = " (cont nou — posibil fals)" if rec.get("fake") else ""
            msg = f"📨 {membru.mention} a fost invitat de <@{src}>{extra}."
        await interaction.response.send_message(msg)

    @app_commands.command(name="invitedlist", description="Lista celor invitati de cineva")
    async def invitedlist(self, interaction: discord.Interaction, membru: discord.Member = None):
        target = membru or interaction.user
        joined_by = storage.get(interaction.guild_id, "invites", {}).get("joined_by", {})
        invited = [uid for uid, rec in joined_by.items() if rec.get("inviter") == str(target.id)]
        if not invited:
            return await interaction.response.send_message(
                f"{target.mention} nu a invitat pe nimeni (inca).", ephemeral=True)
        lines = "\n".join(f"• <@{uid}>" for uid in invited[:25])
        more = f"\n…si inca {len(invited) - 25}" if len(invited) > 25 else ""
        embed = discord.Embed(
            title=f"👥 Invitati de {target.display_name}",
            description=lines + more,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Total: {len(invited)} membri")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="invitecodes", description="Codurile de invitatie ale cuiva")
    async def invitecodes(self, interaction: discord.Interaction, membru: discord.Member = None):
        target = membru or interaction.user
        try:
            invites = await interaction.guild.invites()
        except discord.Forbidden:
            return await interaction.response.send_message(
                "Nu am permisiunea „Manage Server” ca sa vad invitatiile.", ephemeral=True)
        mine = sorted([i for i in invites if i.inviter and i.inviter.id == target.id],
                      key=lambda i: i.uses or 0, reverse=True)
        if not mine:
            return await interaction.response.send_message(
                f"{target.mention} nu are coduri de invitatie active.", ephemeral=True)
        lines = "\n".join(f"`{i.code}` — {i.uses or 0} folosiri" for i in mine[:25])
        embed = discord.Embed(title=f"🔗 Codurile lui {target.display_name}",
                              description=lines, color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="findlink", description="Unul dintre linkurile tale de invitatie")
    async def findlink(self, interaction: discord.Interaction):
        try:
            invites = await interaction.guild.invites()
        except discord.Forbidden:
            return await interaction.response.send_message(
                "Nu am permisiunea „Manage Server”.", ephemeral=True)
        mine = [i for i in invites if i.inviter and i.inviter.id == interaction.user.id]
        if not mine:
            return await interaction.response.send_message(
                "Nu ai niciun link de invitatie. Creeaza unul din Discord (click dreapta pe canal → Invite).",
                ephemeral=True)
        best = max(mine, key=lambda i: i.uses or 0)
        await interaction.response.send_message(
            f"🔗 Linkul tau: https://discord.gg/{best.code} ({best.uses or 0} folosiri)", ephemeral=True)

    def _period_counts(self, guild_id, days) -> dict:
        """Numara invitatiile reale (non-false, ramase) din ultimele `days` zile."""
        import time
        cutoff = time.time() - days * 86400
        return self._count_since(guild_id, cutoff)

    def _count_since(self, guild_id, since_ts) -> dict:
        """Numara invitatiile reale (non-false, ramase) de la un moment incoace.
        Numara fiecare membru O SINGURA DATA (reintrarile nu umfla contorul)."""
        history = storage.get(guild_id, "invites", {}).get("history", [])
        counts = {}
        counted = set()  # membri deja numarati (anti-dublare la reintrari)
        for e in history:
            if e.get("ts", 0) < since_ts:
                continue
            if e.get("fake") or e.get("left"):
                continue
            inv = e.get("inviter")
            if not inv or inv in ("vanity", "unknown"):
                continue
            mid = e.get("member")
            if mid in counted:
                continue  # aceeasi persoana a reintrat -> nu o numaram de doua ori
            counted.add(mid)
            counts[inv] = counts.get(inv, 0) + 1
        return counts

    @app_commands.command(name="leaderboard", description="Clasamentul invitatorilor")
    @app_commands.describe(perioada="Perioada clasamentului", rol="Filtreaza la un anumit rol")
    @app_commands.choices(perioada=[
        app_commands.Choice(name="Tot timpul", value="all"),
        app_commands.Choice(name="Saptamana (7 zile)", value="week"),
        app_commands.Choice(name="Luna (30 zile)", value="month"),
    ])
    async def leaderboard(self, interaction: discord.Interaction,
                          perioada: app_commands.Choice[str] = None,
                          rol: discord.Role = None):
        period = perioada.value if perioada else "all"

        # alegem sursa de date in functie de perioada
        if period == "week":
            raw = self._period_counts(interaction.guild_id, 7)
            label = "Saptamana"
        elif period == "month":
            raw = self._period_counts(interaction.guild_id, 30)
            label = "Luna"
        else:
            members = storage.get(interaction.guild_id, "invites", {}).get("members", {})
            raw = {uid: invite_total(s) for uid, s in members.items()}
            label = "Tot timpul"

        ranked = []
        for uid, n in raw.items():
            if n <= 0:
                continue
            if rol:
                m = interaction.guild.get_member(int(uid))
                if not m or rol not in m.roles:
                    continue
            ranked.append((uid, n))
        ranked.sort(key=lambda x: x[1], reverse=True)
        ranked = ranked[:10]

        if not ranked:
            return await interaction.response.send_message(
                f"Niciun invitator in clasament ({label.lower()}). 📭", ephemeral=True)

        lines = []
        for i, (uid, n) in enumerate(ranked, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`#{i}`")
            lines.append(f"{medal} <@{uid}> — **{n}** invitatii")

        title = f"🏆 Leaderboard Invitatii · {label}"
        if rol:
            title += f" · {rol.name}"
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold())
        if period == "all":
            vanity = storage.get(interaction.guild_id, "invites", {}).get("vanity_count", 0)
            if vanity:
                embed.set_footer(text=f"+ {vanity} intrari prin linkul personalizat")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------- comenzi admin
    @app_commands.command(name="addinvites", description="Adauga invitatii bonus unui membru")
    @bot_access()
    async def addinvites(self, interaction: discord.Interaction, membru: discord.Member, numar: int):
        data = storage.get(interaction.guild_id, "invites", {})
        members = data.get("members", {})
        stats = members.setdefault(str(membru.id), _empty_stats())
        stats["bonus"] += numar
        data["members"] = members
        storage.set(interaction.guild_id, "invites", data)
        await interaction.response.send_message(
            f"🎁 Am adaugat **{numar}** invitatii bonus pentru {membru.mention}. "
            f"Total acum: **{invite_total(stats)}**.")

    @app_commands.command(name="removeinvites", description="Scade invitatii bonus unui membru")
    @bot_access()
    async def removeinvites(self, interaction: discord.Interaction, membru: discord.Member, numar: int):
        data = storage.get(interaction.guild_id, "invites", {})
        members = data.get("members", {})
        stats = members.setdefault(str(membru.id), _empty_stats())
        stats["bonus"] -= numar
        data["members"] = members
        storage.set(interaction.guild_id, "invites", data)
        await interaction.response.send_message(
            f"➖ Am scazut **{numar}** invitatii bonus de la {membru.mention}. "
            f"Total acum: **{invite_total(stats)}**.")

    @app_commands.command(name="resetinvites", description="Reseteaza invitatiile (alege perioada, optional un membru)")
    @bot_access()
    @app_commands.describe(perioada="Ce clasament resetezi", membru="Doar pentru un anumit membru (optional)")
    @app_commands.choices(perioada=[
        app_commands.Choice(name="Tot timpul (sterge tot)", value="all"),
        app_commands.Choice(name="Ultimele 7 zile", value="week"),
        app_commands.Choice(name="Ultimele 30 zile", value="month"),
    ])
    async def resetinvites(self, interaction: discord.Interaction,
                           perioada: app_commands.Choice[str] = None,
                           membru: discord.Member = None):
        import time
        scope = perioada.value if perioada else "all"
        data = storage.get(interaction.guild_id, "invites", {})
        history = data.get("history", [])

        if scope == "week":
            cutoff = time.time() - 7 * 86400
            label = "ultimele 7 zile"
        elif scope == "month":
            cutoff = time.time() - 30 * 86400
            label = "ultimele 30 zile"
        else:
            cutoff = None
            label = "tot timpul"

        if membru:
            mid = str(membru.id)
            if scope == "all":
                data.get("members", {}).pop(mid, None)
                data["history"] = [e for e in history if e.get("inviter") != mid]
            else:
                data["history"] = [e for e in history
                                   if not (e.get("inviter") == mid and e.get("ts", 0) >= cutoff)]
            storage.set(interaction.guild_id, "invites", data)
            await interaction.response.send_message(
                f"🔄 Invitatiile lui {membru.mention} au fost resetate ({label}).")
        else:
            if scope == "all":
                data["members"] = {}
                data["vanity_count"] = 0
                data["history"] = []
                data["joined_by"] = {}
                msg = "🔄 Tot leaderboardul a fost resetat (tot timpul, saptamana si luna). Concurs nou, start!"
            else:
                # stergem doar evenimentele din fereastra aleasa (clasamentul pe acea
                # perioada se goleste; „tot timpul" ramane neatins)
                data["history"] = [e for e in history if e.get("ts", 0) < cutoff]
                msg = f"🔄 Clasamentul pe {label} a fost resetat. (Clasamentul pe tot timpul ramane neschimbat.)"
            storage.set(interaction.guild_id, "invites", data)
            await interaction.response.send_message(msg)

    # =================================================== CONCURS de invitatii
    # contest = {status: scheduled|running|ended, name, start_ts, end_ts(None=manual),
    #            announce_channel_id, winners_count}
    concurs = app_commands.Group(name="concurs", description="Concurs de invitatii (start/stop/clasament)")

    def _contest(self, gid):
        return storage.get(gid, "contest", {}) or {}

    def _contest_board(self, gid, limit=None):
        """Clasamentul concursului (de la start incoace). Cati apar = setabil din dashboard."""
        c = self._contest(gid)
        if c.get("status") not in ("running", "ended"):
            return []
        if limit is None:
            limit = max(1, min(25, int(c.get("board_count", 10))))
        # persoane excluse din concurs (ex. owner) - nu apar in clasament, dar
        # invitatiile lor reale raman neatinse in restul botului
        excluded = {str(x) for x in c.get("excluded", [])}
        counts = self._count_since(gid, c.get("start_ts", 0))
        return sorted([(u, n) for u, n in counts.items()
                       if n > 0 and str(u) not in excluded],
                      key=lambda x: x[1], reverse=True)[:limit]

    def _inviter_name(self, gid, uid):
        """Numele salvat al invitatorului (din istoric), ca sa apara si cand mentiunea
        arata doar @id (userul a plecat sau Discord nu-l rezolva)."""
        hist = (storage.get(gid, "invites", {}) or {}).get("history", [])
        for h in reversed(hist):  # cel mai recent nume cunoscut
            if str(h.get("inviter")) == str(uid) and h.get("inviter_name"):
                return h["inviter_name"]
        return None

    def _board_lines(self, gid, ranked):
        out = []
        for i, (uid, n) in enumerate(ranked, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`#{i}`")
            name = self._inviter_name(gid, uid)
            who = f"<@{uid}>" + (f" ({name})" if name else "")
            out.append(f"{medal} {who} — **{n}** invitatii")
        return "\n".join(out)

    def _final_embed(self, gid, c, ranked):
        wc = max(1, int(c.get("winners_count", 1)))
        winners = ranked[:wc]
        if winners:
            parts = []
            for u, n in winners:
                nm = self._inviter_name(gid, u)
                parts.append(f"<@{u}>" + (f" ({nm})" if nm else "") + f" (**{n}**)")
            win_txt = ", ".join(parts)
        else:
            win_txt = "nimeni"
        return discord.Embed(
            title=f"🏆 {c.get('name','Concurs')} · REZULTAT FINAL",
            description=f"🎉 Castigator(i): {win_txt}\n\n{self._board_lines(gid, ranked) or 'Nicio invitatie.'}",
            color=discord.Color.gold())

    # ------- bucla care porneste/incheie concursurile programate -------
    async def _reconcile_leaves(self, guild, since_ts):
        """Reconciliaza numaratoarea cu REALITATEA de pe server (autoritativ).
        Nu se bazeaza pe prinderea fiecarui eveniment de plecare — verifica direct
        cine mai e pe server si corecteaza: marcheaza 'left' in istoric si RECALCULEAZA
        cati au plecat pentru fiecare invitator. Sigur doar cand avem lista completa
        de membri (guild.chunked)."""
        if not guild.chunked:
            try:
                await guild.chunk()
            except Exception:
                return  # nu putem confirma cine e pe server -> nu riscam sa stricam datele
        data = storage.get(guild.id, "invites", {}) or {}
        history = data.get("history", [])
        members = data.get("members", {})
        credited = data.get("credited", {})

        def _present(mid):
            try:
                return guild.get_member(int(mid)) is not None
            except (ValueError, TypeError):
                return False

        changed = False
        # 1) sincronizam flag-ul 'left' din istoric cu realitatea (in ambele sensuri)
        for e in history:
            mid = e.get("member")
            if not mid:
                continue
            should_be_left = not _present(mid)
            if bool(e.get("left")) != should_be_left:
                e["left"] = should_be_left
                changed = True

        # 2) recalculam 'left' pentru fiecare invitator DIN ISTORIC (sursa completa,
        #    nu din lista 'credited' care poate fi incompleta pentru date vechi).
        #    Numaram membri DISTINCTI (reintrarile nu dubleaza), fara conturile false
        #    (care sunt deja anulate separat prin 'fake'). left = cati din ei nu mai sunt.
        fake_members = {str(h.get("member")) for h in history if h.get("fake")}
        invited = {}  # inviter -> set de membri reali (non-fake) pe care i-a adus
        for h in history:
            inv = h.get("inviter")
            m = str(h.get("member") or "")
            if not inv or inv in ("vanity", "unknown") or not m:
                continue
            if m in fake_members:
                continue
            invited.setdefault(inv, set()).add(m)
        for inv, member_set in invited.items():
            new_left = sum(1 for m in member_set if not _present(m))
            st = members.setdefault(inv, _empty_stats())
            if st.get("left", 0) != new_left:
                st["left"] = new_left
                changed = True

        if changed:
            data["members"] = members
            storage.set(guild.id, "invites", data)

    @tasks.loop(seconds=30)
    async def contest_loop(self):
        import time
        now = time.time()
        for guild in self.bot.guilds:
            c = self._contest(guild.id)
            status = c.get("status")
            # reconciliem plecarile ratate (ex. cand botul era offline la 1 noaptea):
            # marcam ca "left" oricine nu mai e pe server, ca sa scada corect din concurs
            if status == "running":
                try:
                    await self._reconcile_leaves(guild, c.get("start_ts", 0))
                except Exception:
                    pass
            if status == "scheduled" and now >= c.get("start_ts", 0):
                c["status"] = "running"
                storage.set(guild.id, "contest", c)
                ch = guild.get_channel(int(c["announce_channel_id"])) if c.get("announce_channel_id") else None
                if ch:
                    try:
                        await ch.send(f"🏁 Concursul **{c.get('name')}** a inceput! Invitati cat mai multi membri!")
                    except discord.HTTPException:
                        pass
            elif status == "running" and c.get("end_ts") and now >= c["end_ts"]:
                ranked = self._contest_board(guild.id)
                c["status"] = "ended"
                storage.set(guild.id, "contest", c)
                ch = guild.get_channel(int(c["announce_channel_id"])) if c.get("announce_channel_id") else None
                if ch:
                    try:
                        await ch.send(embed=self._final_embed(guild.id, c, ranked))
                    except discord.HTTPException:
                        pass

            # ---- clasament LIVE pe canal (daca e activat) ----
            if status == "running" and c.get("live_enabled") and c.get("live_channel_id"):
                interval = max(1, int(c.get("live_interval_minutes", 30))) * 60
                last = c.get("live_last_post", 0)
                if now - last >= interval:
                    await self._post_live_board(guild, c)

    async def _post_live_board(self, guild, c):
        import time
        ch = guild.get_channel(int(c["live_channel_id"]))
        if ch is None:
            return
        ranked = self._contest_board(guild.id)
        embed = discord.Embed(
            title=f"🏁 {c.get('name','Concurs')} · Clasament live",
            description=self._board_lines(guild.id, ranked) or "Încă nicio invitație. Fii primul!",
            color=discord.Color(0x8B5CF6))
        embed.set_footer(text="Se actualizează automat")
        embed.timestamp = discord.utils.utcnow()
        # editam acelasi mesaj daca exista, altfel postam unul nou
        msg_id = c.get("live_message_id")
        posted = False
        if msg_id:
            try:
                msg = await ch.fetch_message(int(msg_id))
                await msg.edit(embed=embed)
                posted = True
            except (discord.NotFound, discord.HTTPException):
                posted = False
        if not posted:
            try:
                msg = await ch.send(embed=embed)
                c["live_message_id"] = msg.id
            except discord.HTTPException:
                return
        c["live_last_post"] = time.time()
        storage.set(guild.id, "contest", c)

    @contest_loop.before_loop
    async def _before_contest(self):
        await self.bot.wait_until_ready()

    @concurs.command(name="start", description="Porneste un concurs de invitatii de acum (fara durata fixa)")
    @bot_access()
    async def concurs_start(self, interaction: discord.Interaction, nume: str = "Concurs invitatii"):
        import time
        if self._contest(interaction.guild_id).get("status") in ("scheduled", "running"):
            return await interaction.response.send_message(
                "Exista deja un concurs activ/programat. Opreste-l cu `/concurs stop`.", ephemeral=True)
        prev = self._contest(interaction.guild_id)
        storage.set(interaction.guild_id, "contest", {
            "status": "running", "name": nume, "start_ts": time.time(),
            "end_ts": None, "announce_channel_id": None, "winners_count": 1,
            # pastram setarile de clasament live (din dashboard), resetam mesajul
            "live_enabled": prev.get("live_enabled", False),
            "live_channel_id": prev.get("live_channel_id"),
            "live_interval_minutes": prev.get("live_interval_minutes", 30),
            "board_count": prev.get("board_count", 10),
            "live_message_id": None, "live_last_post": 0,
        })
        await interaction.response.send_message(
            f"🏁 **{nume}** a inceput! Vezi clasamentul cu `/concurs clasament`.\n"
            f"💡 Pentru concurs cu durata fixa si anunt automat, foloseste dashboardul.")

    @concurs.command(name="clasament", description="Clasamentul concursului curent")
    async def concurs_board(self, interaction: discord.Interaction):
        c = self._contest(interaction.guild_id)
        if c.get("status") not in ("running", "ended"):
            return await interaction.response.send_message(
                "Nu e niciun concurs activ. Porneste cu `/concurs start` sau din dashboard.", ephemeral=True)
        ranked = self._contest_board(interaction.guild_id)
        if not ranked:
            return await interaction.response.send_message(
                f"🏁 **{c.get('name')}** — inca nu a invitat nimeni.")
        embed = discord.Embed(title=f"🏁 {c.get('name')} · Clasament",
                              description=self._board_lines(interaction.guild_id, ranked), color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @concurs.command(name="stop", description="Opreste concursul acum si anunta castigatorul")
    @bot_access()
    async def concurs_stop(self, interaction: discord.Interaction):
        import time
        c = self._contest(interaction.guild_id)
        if c.get("status") not in ("running", "scheduled"):
            return await interaction.response.send_message("Nu e niciun concurs activ.", ephemeral=True)
        ranked = self._contest_board(interaction.guild_id)
        c["status"] = "ended"
        c["end_ts"] = time.time()
        storage.set(interaction.guild_id, "contest", c)
        if not ranked:
            return await interaction.response.send_message(
                f"🏁 **{c.get('name')}** s-a incheiat, dar nu a invitat nimeni. 📭")
        await interaction.response.send_message(embed=self._final_embed(interaction.guild_id, c, ranked))

    @concurs.command(name="status", description="Vezi starea concursului")
    async def concurs_status(self, interaction: discord.Interaction):
        import time
        c = self._contest(interaction.guild_id)
        st = c.get("status")
        if st == "scheduled":
            mins = (c.get("start_ts", 0) - time.time()) / 60
            msg = f"🕒 **{c.get('name')}** e programat sa inceapa peste **{mins:.0f} minute**."
        elif st == "running":
            if c.get("end_ts"):
                left = (c["end_ts"] - time.time()) / 3600
                msg = f"🏁 **{c.get('name')}** e activ. Mai sunt **{left:.1f} ore**."
            else:
                msg = f"🏁 **{c.get('name')}** e activ (fara durata fixa)."
        else:
            return await interaction.response.send_message("Niciun concurs activ.", ephemeral=True)
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Invites(bot))
