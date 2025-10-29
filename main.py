"""Entry point for the Werewolf support Discord bot."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List

import discord
from discord.ext import commands
from dotenv import load_dotenv

import storage

COG_MODULES: List[str] = [
    "cogs.entry",
    "cogs.game",
    "cogs.dashboard",
    "cogs.day_progress",
]

logger = logging.getLogger(__name__)


class WerewolfBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        intents.messages = True
        super().__init__(command_prefix=commands.when_mentioned_or("!"), intents=intents)

    async def setup_hook(self) -> None:  # type: ignore[override]
        await storage.Storage.ensure_loaded()
        for module in COG_MODULES:
            try:
                await self.load_extension(module)
                logger.info("Loaded extension %s", module)
            except Exception:
                logger.exception("Failed to load extension %s", module)
        await self.tree.sync()
        logger.info("Application commands synchronised")

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set in the environment.")
    bot = WerewolfBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
