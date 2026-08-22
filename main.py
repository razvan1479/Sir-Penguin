"""
main.py — punctul de pornire al botului.

Rolul lui e DOAR sa porneasca botul si sa incarce automat tot ce e in cogs/.
Cand adaugi un modul nou (giveaway, tickete...), nu atingi acest fisier:
pui un fisier nou in cogs/ si se incarca singur.
"""

import os
import ssl
import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from utils import storage

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# Intents = ce evenimente primeste botul.
# members  -> ca sa stim cand intra cineva (on_member_join)
# message_content -> pentru comenzi clasice (optional)
# IMPORTANT: activeaza-le si in Developer Portal -> Bot -> Privileged Intents
INTENTS = discord.Intents.default()
INTENTS.members = True
INTENTS.message_content = True


class MyBot(commands.Bot):
    def __init__(self, connector=None):
        super().__init__(command_prefix="!", intents=INTENTS,
                         help_command=None, connector=connector)

    async def setup_hook(self):
        # Doar incarcam modulele. Sincronizarea comenzilor se face AUTOMAT in
        # on_ready (_auto_sync), instant pe fiecare server. Nu rulezi nimic manual.
        await self.load_all_cogs()
        self.tree.on_error = self._on_app_command_error

    async def _on_app_command_error(self, interaction, error):
        # mesaj prietenos cand cineva nu are acces la o comanda de management
        if isinstance(error, app_commands.CheckFailure):
            msg = ("⛔ Nu ai acces la aceasta comanda. Un admin iti poate da acces "
                   "alegand un rol permis din dashboard, pagina Permisiuni.")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except discord.HTTPException:
                pass
        else:
            log.error("Eroare la comanda %s: %s",
                      getattr(interaction.command, "qualified_name", "?"), error)

    async def load_all_cogs(self):
        cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and not filename.startswith("_"):
                ext = f"cogs.{filename[:-3]}"
                try:
                    await self.load_extension(ext)
                    log.info("✓ Modul incarcat: %s", ext)
                except Exception as e:
                    log.error("✗ Eroare la %s: %s", ext, e)

    async def on_ready(self):
        log.info("Conectat ca %s (ID: %s) — pe %d servere.",
                 self.user, self.user.id, len(self.guilds))
        # retinem cine e owner-ul aplicatiei (cel care a creat botul), ca dashboardul
        # sa stie cine are voie la actiuni globale (restart, lista servere). Se seteaza
        # automat, nu poate fi schimbat de altcineva.
        try:
            from utils import storage
            app = await self.application_info()
            owner = app.team.owner_id if app.team else app.owner.id
            if owner:
                storage.set(0, "bot_owner_id", str(owner))
                log.info("Owner bot: %s", owner)
        except Exception as e:
            log.warning("Nu am putut seta owner-ul botului: %s", e)
        await self._auto_sync()

    async def _auto_sync(self):
        # Sincronizeaza comenzile slash AUTOMAT pe fiecare server.
        # Tine minte PE CE SERVERE a sincronizat deja (synced_guilds), ca sa
        # prinda automat orice server nou — chiar daca botul era oprit cand a
        # fost adaugat. Nu trebuie sa repornesti niciodata manual.
        if getattr(self, "_did_sync", False):
            return
        self._did_sync = True

        sig = ",".join(sorted(c.qualified_name for c in self.tree.walk_commands()))
        saved_sig = storage.get("_global", "cmd_sig", None)
        synced = set(storage.get("_global", "synced_guilds", []) or [])

        if sig != saved_sig:
            # comenzile s-au schimbat -> resincronizam TOATE serverele
            try:
                await self.http.bulk_upsert_global_commands(self.application_id, [])
            except Exception:
                pass
            to_sync = list(self.guilds)
            synced = set()
        else:
            # comenzile la fel -> sincronizam doar serverele NOI
            to_sync = [g for g in self.guilds if str(g.id) not in synced]
            if not to_sync:
                log.info("Comenzile sunt deja sincronizate pe toate serverele.")
                return

        total = 0
        for g in to_sync:
            try:
                self.tree.copy_global_to(guild=g)
                res = await self.tree.sync(guild=g)
                total += len(res)
                synced.add(str(g.id))
            except discord.HTTPException as e:
                log.warning("Sync esuat pe %s: %s", g.id, e)

        storage.set("_global", "cmd_sig", sig)
        storage.set("_global", "synced_guilds", sorted(synced))
        log.info("Auto-sincronizat %d comenzi pe %d servere.", total, len(to_sync))

    async def on_guild_join(self, guild):
        # server nou (cat botul e pornit) -> ii sincronizam comenzile instant
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            synced = set(storage.get("_global", "synced_guilds", []) or [])
            synced.add(str(guild.id))
            storage.set("_global", "synced_guilds", sorted(synced))
            log.info("Server nou %s — comenzi sincronizate instant.", guild.id)
        except discord.HTTPException as e:
            log.warning("Sync esuat la intrare pe %s: %s", guild.id, e)


async def main():
    # --- Fix SSL pentru Python 3.13+ pe retele cu inspectie HTTPS ---
    # Python 3.13+ verifica strict certificatele (VERIFY_X509_STRICT). Pe retele
    # cu antivirus/proxy corporate care re-semneaza traficul, certificatul nu are
    # extensia "Authority Key Identifier" -> eroare. Relaxam DOAR aceasta verificare
    # stricta; restul validarii certificatului ramane activ.
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    bot = MyBot(connector=connector)

    # hot reload: modifici un cog si il reincarci fara sa repornesti botul
    @bot.command(name="reload")
    @commands.is_owner()
    async def reload_cog(ctx, name: str):
        try:
            await bot.reload_extension(f"cogs.{name}")
            await ctx.send(f"♻️ Modulul `{name}` reincarcat.")
        except Exception as e:
            await ctx.send(f"❌ Eroare: `{e}`")

    token = os.getenv("DISCORD_TOKEN")
    if not token or token == "pune_tokenul_aici":
        raise RuntimeError("Lipseste DISCORD_TOKEN din .env (sau e inca placeholder-ul).")

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
