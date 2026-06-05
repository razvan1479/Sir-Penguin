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
        await self._auto_sync()

    async def _auto_sync(self):
        # Sincronizeaza comenzile slash AUTOMAT, instant pe fiecare server,
        # dar doar daca s-au schimbat fata de ultima pornire (altfel lovim
        # degeaba limitele Discord). Nu trebuie sa rulezi nimic manual.
        if getattr(self, "_did_sync", False):
            return
        self._did_sync = True

        sig = ",".join(sorted(c.qualified_name for c in self.tree.walk_commands()))
        if storage.get("_global", "cmd_sig", None) == sig:
            log.info("Comenzile nu s-au schimbat — nu mai sincronizez.")
            return

        # Stergem comenzile globale vechi (daca s-a facut candva sync global),
        # ca sa nu apara dublate alaturi de cele sincronizate pe server.
        try:
            await self.http.bulk_upsert_global_commands(self.application_id, [])
        except Exception:
            pass

        total = 0
        for g in self.guilds:
            try:
                self.tree.copy_global_to(guild=g)
                synced = await self.tree.sync(guild=g)
                total += len(synced)
            except discord.HTTPException as e:
                log.warning("Sync esuat pe %s: %s", g.id, e)
        storage.set("_global", "cmd_sig", sig)
        log.info("Auto-sincronizat %d comenzi pe %d servere.", total, len(self.guilds))

    async def on_guild_join(self, guild):
        # server nou -> ii sincronizam comenzile instant
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except discord.HTTPException:
            pass


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
