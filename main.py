# main.py
import os
import asyncio
import logging
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
APP_ID = os.getenv("APPLICATION_ID")
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
GUILD_ID = os.getenv("GUILD_ID")
PORT = int(os.getenv("PORT", "10000"))

if not TOKEN or not APP_ID:
    raise RuntimeError(".env の DISCORD_TOKEN / APPLICATION_ID を設定してください")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("werewolf")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True


class WerewolfBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, application_id=int(APP_ID))

    async def setup_hook(self):
        # Load cogs
        for ext in [
            "cogs.entry_manager",
            "cogs.game",
            "cogs.day_progress",
            "cogs.vote_manager",
        ]:
            try:
                await self.load_extension(ext)
                log.info(f"✅ Loaded: {ext}")
            except Exception as e:
                log.exception(f"❌ Failed to load {ext}: {e}")

        # Sync commands
        try:
            if DEBUG_MODE and GUILD_ID:
                guild_obj = discord.Object(id=int(GUILD_ID))
                synced = await self.tree.sync(guild=guild_obj)
                log.info(f"🧪 Synced {len(synced)} guild cmds to {GUILD_ID}")
            else:
                synced = await self.tree.sync()
                log.info(f"🌍 Synced {len(synced)} global cmds")
        except Exception as e:
            log.exception(f"❌ Sync failed: {e}")

    async def on_ready(self):
        log.info(f"✅ Logged in as {self.user} ({self.user.id})")


async def run_bot():
    bot = WerewolfBot()
    backoff = [1, 2, 5, 10]
    for i, wait in enumerate([0] + backoff, start=1):
        try:
            if wait:
                log.warning(f"🌐 再接続試行 {i}/{len(backoff)+1}… {wait}s 後に再試行")
                await asyncio.sleep(wait)
            await bot.start(TOKEN)
            return
        except (OSError, discord.GatewayNotFound, discord.HTTPException) as e:
            log.warning(f"⚠️ 接続エラー: {e}")
            continue
        except Exception:
            log.exception("🔥 予期せぬ例外で停止")
            break


async def run_http_server():
    async def health(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    log.info(f"🌐 HTTP server listening on :{PORT}")
    await site.start()
    # Keep running
    while True:
        await asyncio.sleep(3600)


async def main():
    await asyncio.gather(
        run_bot(),
        run_http_server(),
    )


if __name__ == "__main__":
    if os.name == "nt":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    asyncio.run(main())
