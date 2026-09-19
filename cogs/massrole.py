"""
cogs/massrole.py — gestionare ROLURI in masa.

Comenzi slash (folosire directa, cer permisiunea Manage Roles):
  /massrole give_all <rol>              - da rolul TUTUROR
  /massrole remove_all <rol>            - scoate rolul de la TOTI
  /massrole give_to <rol> <conditie>    - da <rol> celor care au <conditie>
  /massrole remove_from <rol> <conditie>- scoate <rol> de la cei care au <conditie>

Dashboard: pagina /massrole trimite un "job" pe care botul il executa in ~10s.
Botul scrie si lista rolurilor serverului in storage, ca dashboardul sa le
poata afisa in liste.

Reguli de siguranta: nu se poate atribui rolul @everyone, roluri gestionate de
integrari/boti, sau roluri mai sus decat rolul botului.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import storage
from utils.perms import has_bot_access


class MassRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.job_loop.start()

    def cog_unload(self):
        self.job_loop.cancel()

    async def interaction_check(self, interaction):
        return has_bot_access(interaction)

    # ----------------------------------------------- sync roluri -> storage
    def _store_roles(self, guild: discord.Guild):
        bot_top = guild.me.top_role.position if guild.me else 0
        lst = []
        for r in guild.roles:
            lst.append({
                "id": str(r.id), "name": r.name, "position": r.position,
                "managed": r.managed, "default": r.is_default(),
                "color": f"#{r.color.value:06x}" if r.color.value else "#99aab5",
                "assignable": (not r.managed and not r.is_default() and r.position < bot_top),
            })
        lst.sort(key=lambda x: -x["position"])
        storage.set(guild.id, "roles", {"bot_top": bot_top, "list": lst})

    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.bot.guilds:
            self._store_roles(g)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        self._store_roles(role.guild)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        self._store_roles(after.guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        self._store_roles(role.guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        self._store_roles(guild)

    # ----------------------------------------------- verificari
    def _bot_problem(self, guild, role):
        """Probleme care tin de bot/rol (independent de user)."""
        if role.is_default():
            return "Nu pot folosi rolul @everyone."
        if role.managed:
            return f"Rolul **{role.name}** e gestionat de o integrare si nu poate fi atribuit manual."
        if guild.me and role >= guild.me.top_role:
            return f"Rolul **{role.name}** e mai sus decat rolul botului. Muta rolul botului mai sus in setarile serverului."
        return None

    def _user_problem(self, guild, role, user):
        if user.id != guild.owner_id and role >= user.top_role:
            return f"Rolul **{role.name}** e mai sus sau egal cu rolul tau cel mai inalt."
        return None

    # ----------------------------------------------- autocomplete roluri
    async def _role_ac_assign(self, interaction: discord.Interaction, current: str):
        """Rolurile pe care botul le poate DA/SCOATE (fara @everyone si fara roluri
        gestionate). Le arata pe TOATE — inclusiv SIDRA — spre deosebire de selectorul
        nativ Discord care mai ascunde din ele. Cele prea sus in ierarhie sunt marcate."""
        guild = interaction.guild
        if not guild:
            return []
        cur = (current or "").lower()
        me_top = guild.me.top_role.position if guild.me else 0
        out = []
        for r in sorted(guild.roles, key=lambda x: -x.position):
            if r.is_default() or r.managed:
                continue
            if cur and cur not in r.name.lower():
                continue
            label = r.name if r.position < me_top else f"{r.name}  ⚠️ prea sus"
            out.append(app_commands.Choice(name=label[:100], value=str(r.id)))
            if len(out) >= 25:
                break
        return out

    async def _role_ac_any(self, interaction: discord.Interaction, current: str):
        """Toate rolurile (pentru 'conditie') — fara @everyone."""
        guild = interaction.guild
        if not guild:
            return []
        cur = (current or "").lower()
        out = []
        for r in sorted(guild.roles, key=lambda x: -x.position):
            if r.is_default():
                continue
            if cur and cur not in r.name.lower():
                continue
            out.append(app_commands.Choice(name=r.name[:100], value=str(r.id)))
            if len(out) >= 25:
                break
        return out

    def _resolve_role(self, guild, value):
        """Transforma valoarea din autocomplete (id) — sau un nume — intr-un rol."""
        if not guild or not value:
            return None
        value = str(value).strip()
        if value.isdigit():
            return guild.get_role(int(value))
        for r in guild.roles:
            if r.name.lower() == value.lower():
                return r
        return None

    # ----------------------------------------------- motorul
    async def apply_roles(self, guild, action, role, condition=None, include_bots=False):
        changed = errors = 0
        for m in guild.members:
            if m.bot and not include_bots:
                continue
            if condition and condition not in m.roles:
                continue
            try:
                if action in ("give_all", "give_to"):
                    if role not in m.roles:
                        await m.add_roles(role, reason="massrole")
                        changed += 1
                else:  # remove_all / remove_from
                    if role in m.roles:
                        await m.remove_roles(role, reason="massrole")
                        changed += 1
            except (discord.Forbidden, discord.HTTPException):
                errors += 1
        return {"changed": changed, "errors": errors, "total": len(guild.members)}

    # ----------------------------------------------- joburi din dashboard
    @tasks.loop(seconds=12)
    async def job_loop(self):
        for guild in self.bot.guilds:
            job = storage.get(guild.id, "massrole_job", None)
            if not job or job.get("status") != "pending":
                continue
            job["status"] = "running"
            storage.set(guild.id, "massrole_job", job)

            role = guild.get_role(int(job["role_id"])) if job.get("role_id") else None
            cond = guild.get_role(int(job["condition_role_id"])) if job.get("condition_role_id") else None

            if not role:
                job.update(status="error", result="Rolul selectat nu mai exista.")
                storage.set(guild.id, "massrole_job", job)
                continue
            prob = self._bot_problem(guild, role)
            if prob:
                job.update(status="error", result=prob)
                storage.set(guild.id, "massrole_job", job)
                continue

            res = await self.apply_roles(guild, job["action"], role, cond,
                                         job.get("include_bots", False))
            job.update(status="done",
                       result=f"{res['changed']} membri modificati, {res['errors']} erori (din {res['total']}).")
            storage.set(guild.id, "massrole_job", job)
            self._store_roles(guild)

    @job_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------- comenzi slash
    massrole = app_commands.Group(name="massrole", description="Roluri in masa")

    async def _run(self, interaction, action, role, condition, include_bots):
        prob = self._bot_problem(interaction.guild, role) or \
               self._user_problem(interaction.guild, role, interaction.user)
        if prob:
            return await interaction.response.send_message("⚠️ " + prob, ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        res = await self.apply_roles(interaction.guild, action, role, condition, include_bots)
        verb = "dat" if action.startswith("give") else "scos"
        extra = f" celor cu rolul **{condition.name}**" if condition else ""
        await interaction.followup.send(
            f"✓ Am {verb} rolul **{role.name}**{extra} — "
            f"{res['changed']} membri ({res['errors']} erori, din {res['total']}).",
            ephemeral=True)

    @massrole.command(name="give_all", description="Da un rol TUTUROR membrilor")
    @app_commands.describe(rol="Rolul de dat (scrie ca sa cauti)", include_bots="Include si botii? (implicit nu)")
    @app_commands.autocomplete(rol=_role_ac_assign)
    async def give_all(self, interaction, rol: str, include_bots: bool = False):
        role = self._resolve_role(interaction.guild, rol)
        if role is None:
            return await interaction.response.send_message(
                "⚠️ Rol invalid — alege unul din listă.", ephemeral=True)
        await self._run(interaction, "give_all", role, None, include_bots)

    @massrole.command(name="remove_all", description="Scoate un rol de la TOTI membrii")
    @app_commands.describe(rol="Rolul de scos (scrie ca sa cauti)", include_bots="Include si botii? (implicit nu)")
    @app_commands.autocomplete(rol=_role_ac_assign)
    async def remove_all(self, interaction, rol: str, include_bots: bool = False):
        role = self._resolve_role(interaction.guild, rol)
        if role is None:
            return await interaction.response.send_message(
                "⚠️ Rol invalid — alege unul din listă.", ephemeral=True)
        await self._run(interaction, "remove_all", role, None, include_bots)

    @massrole.command(name="give_to", description="Da un rol celor care au deja un anumit rol")
    @app_commands.describe(rol="Rolul de dat", conditie="Doar cei care au acest rol")
    @app_commands.autocomplete(rol=_role_ac_assign, conditie=_role_ac_any)
    async def give_to(self, interaction, rol: str, conditie: str):
        role = self._resolve_role(interaction.guild, rol)
        cond = self._resolve_role(interaction.guild, conditie)
        if role is None or cond is None:
            return await interaction.response.send_message(
                "⚠️ Rol invalid — alege din liste.", ephemeral=True)
        await self._run(interaction, "give_to", role, cond, True)

    @massrole.command(name="remove_from", description="Scoate un rol de la cei care au un anumit rol")
    @app_commands.describe(rol="Rolul de scos", conditie="Doar cei care au acest rol")
    @app_commands.autocomplete(rol=_role_ac_assign, conditie=_role_ac_any)
    async def remove_from(self, interaction, rol: str, conditie: str):
        role = self._resolve_role(interaction.guild, rol)
        cond = self._resolve_role(interaction.guild, conditie)
        if role is None or cond is None:
            return await interaction.response.send_message(
                "⚠️ Rol invalid — alege din liste.", ephemeral=True)
        await self._run(interaction, "remove_from", role, cond, True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MassRole(bot))
