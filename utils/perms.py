"""
utils/perms.py — control de acces la comenzile de management ale botului.

Cine poate folosi comenzile „de admin":
  - OWNER-ul botului (tu, cel care l-a creat) — MEREU, pe orice server, indiferent
    de rangul pe care il are acolo
  - oricine are permisiunea Administrator sau Manage Server pe Discord
  - proprietarul serverului
  - oricine are unul dintre ROLURILE permise, alese din dashboard
    (storage cheia "permissions" -> {"roles": ["id1", "id2", ...]})

Folosire in cog-uri:
    from utils.perms import bot_access
    @bot_access()
    async def comanda(self, interaction): ...
"""

import discord
from discord import app_commands

from utils import storage


# ---------------------------------------------------------------------------
# Useri ABSOLUTI (super-useri): au acces la bot MEREU, pe orice server, exact
# ca owner-ul botului — indiferent de rangul lor pe acel server.
# Poti adauga dupa USERNAME (ex. "w1.ideal") sau dupa ID (mai sigur, nu se
# schimba niciodata). Username-urile se compara fara sa conteze literele mari/mici.
ABSOLUTE_USERNAMES = {"w1.ideal"}
ABSOLUTE_IDS = {1493163753447882894}  # w1.ideal (owner) — fixat pe ID, nu se strica niciodata


def _is_absolute(user) -> bool:
    try:
        if user.id in ABSOLUTE_IDS:
            return True
        uname = (getattr(user, "name", "") or "").lower()
        return uname in {u.lower() for u in ABSOLUTE_USERNAMES}
    except Exception:
        return False


def allowed_roles(gid) -> list:
    return storage.get(gid, "permissions", {}).get("roles", []) or []


def has_bot_access(interaction: discord.Interaction) -> bool:
    user = interaction.user

    # OWNER-ul botului poate folosi MEREU botul, pe orice server, chiar daca
    # acolo are un grad mic / fara permisiuni. E botul lui, deci are acces peste tot.
    owner_id = storage.get(0, "bot_owner_id", None)
    if owner_id and str(user.id) == str(owner_id):
        return True

    # Userii ABSOLUTI (super-useri) — acces mereu, ca owner-ul.
    if _is_absolute(user):
        return True

    perms = getattr(user, "guild_permissions", None)
    if perms and (perms.administrator or perms.manage_guild):
        return True
    if interaction.guild and user.id == interaction.guild.owner_id:
        return True
    allowed = {str(r) for r in allowed_roles(interaction.guild_id)}
    if allowed:
        user_roles = {str(r.id) for r in getattr(user, "roles", [])}
        if allowed & user_roles:
            return True
    return False


def bot_access():
    """Check pentru comenzi de management: admin/Manage Server sau rol permis."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if has_bot_access(interaction):
            return True
        raise app_commands.CheckFailure("no_bot_access")
    return app_commands.check(predicate)
