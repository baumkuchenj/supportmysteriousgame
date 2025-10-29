"""Cog providing simple manual day progression command."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import storage

logger = logging.getLogger(__name__)


class DayProgressCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:  # type: ignore[override]
        await storage.Storage.ensure_loaded()

    async def is_gm(self, member: discord.Member) -> bool:
        state = await storage.Storage.read_state()
        gm_role_id = state["roles"].get("gm_role_id")
        gm_role = member.guild.get_role(gm_role_id) if gm_role_id else None
        if gm_role is None:
            return False
        return gm_role in member.roles

    async def send_log(self, guild: discord.Guild, message: str) -> None:
        state = await storage.Storage.read_state()
        log_channel_id = state["gm"].get("log_channel_id")
        if not log_channel_id:
            return
        channel = guild.get_channel(log_channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(log_channel_id)
            except discord.NotFound:
                return
        if isinstance(channel, discord.TextChannel):
            await channel.send(message)

    @app_commands.command(name="bump_day", description="現在の日付を1日進めます。")
    async def bump_day(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return
        if not await self.is_gm(interaction.user):
            await interaction.response.send_message("GM専用のコマンドです。", ephemeral=True)
            return
        state = await storage.Storage.read_state()
        if not state["game"].get("started"):
            await interaction.response.send_message("ゲームが開始されていません。", ephemeral=True)
            return

        def mutate(data):
            data["game"]["current_day"] = data["game"].get("current_day", 0) + 1

        new_state = await storage.Storage.update_state(mutate)
        current_day = new_state["game"].get("current_day", 0)
        await interaction.response.send_message(f"{current_day}日目に進めました。", ephemeral=True)
        await self.send_log(interaction.guild, f"日付が {current_day}日目 に更新されました。")
        self.bot.dispatch("ensure_dashboard", interaction.guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DayProgressCog(bot))
