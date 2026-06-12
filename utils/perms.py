"""
utils/perms.py — control de acces la comenzile de management ale botului.

Cine poate folosi comenzile „de admin":
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


def allowed_roles(gid) -> list:
    return storage.get(gid, "permissions", {}).get("roles", []) or []


def has_bot_access(interaction: discord.Interaction) -> bool:
    user = interaction.user
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
