"""Cog that manages the transition from entry to the actual game."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import storage
import utils

logger = logging.getLogger(__name__)


class GameCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:  # type: ignore[override]
        await storage.Storage.ensure_loaded()

    async def _ensure_gm(self, member: discord.Member) -> bool:
        state = await storage.Storage.read_state()
        gm_role_id = state["roles"].get("gm_role_id")
        gm_role = member.guild.get_role(gm_role_id) if gm_role_id else None
        if gm_role is None:
            return True  # fallback if gm role is not defined yet
        return gm_role in member.roles

    async def _cleanup_previous_setup(self, guild: discord.Guild, state: dict) -> None:
        assignments: Dict[str, dict] = state["game"].get("ho_assignments", {})
        for assignment in assignments.values():
            channel_id = assignment.get("channel_id")
            if channel_id:
                channel = guild.get_channel(channel_id)
                if isinstance(channel, discord.TextChannel):
                    try:
                        await channel.delete(reason="Reset HO channel for new game")
                    except discord.Forbidden:
                        logger.warning("Missing permissions to delete channel %s", channel_id)
        ho_roles = state["roles"].get("ho_roles", {})
        for role_id in ho_roles.values():
            role = guild.get_role(role_id)
            if role:
                try:
                    await role.delete(reason="Reset HO role for new game")
                except discord.Forbidden:
                    logger.warning("Missing permissions to delete role %s", role_id)

    async def _send_log(self, guild: discord.Guild, message: str) -> None:
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

    def _ho_role_name(self, index: int) -> str:
        return f"{config.HO_ROLE_PREFIX}{index}"

    def _ho_channel_name(self, index: int) -> str:
        return f"{config.HO_CHANNEL_PREFIX}-{index}"

    @app_commands.command(name="start_game", description="Playerロールの参加者にHOロールとチャンネルを作成します。")
    @app_commands.describe(ho_category="HOチャンネルを作成するカテゴリ")
    async def start_game(
        self,
        interaction: discord.Interaction,
        ho_category: Optional[discord.CategoryChannel] = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ使用できます。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if not await self._ensure_gm(interaction.user):
            await interaction.followup.send("GMロールのユーザーのみ実行できます。", ephemeral=True)
            return

        state = await storage.Storage.read_state()
        players_dict: Dict[str, dict] = state["entry"].get("players", {})
        if not players_dict:
            await interaction.followup.send("参加者がいません。/entry で募集してください。", ephemeral=True)
            return
        player_ids = list(players_dict.keys())

        await self._cleanup_previous_setup(guild, state)

        if ho_category is None:
            ho_category_id = state["game"].get("ho_category_id")
            category = guild.get_channel(ho_category_id) if ho_category_id else None
            if category is None:
                category = discord.utils.get(guild.categories, name=config.HO_CATEGORY_NAME)
            if category is None:
                category = await guild.create_category(
                    name=config.HO_CATEGORY_NAME,
                    reason="Create HO category for Werewolf game",
                )
        else:
            category = ho_category

        gm_role_id = state["roles"].get("gm_role_id")
        gm_role = guild.get_role(gm_role_id) if gm_role_id else None

        ho_assignments: Dict[str, dict] = {}
        ho_roles: Dict[str, int] = {}
        created_channels: List[discord.TextChannel] = []

        for index, member_id in enumerate(player_ids, start=1):
            member = guild.get_member(int(member_id))
            if member is None:
                continue
            ho_role_name = self._ho_role_name(index)
            existing_role = discord.utils.get(guild.roles, name=ho_role_name)
            if existing_role is None:
                existing_role = await guild.create_role(
                    name=ho_role_name,
                    reason="Create HO role",
                )
            await member.add_roles(existing_role, reason="Assign HO role")

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            if gm_role:
                overwrites[gm_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )

            channel = await category.create_text_channel(
                name=self._ho_channel_name(index),
                overwrites=overwrites,
                reason="Create HO private channel",
            )
            created_channels.append(channel)

            ho_roles[ho_role_name] = existing_role.id
            ho_assignments[ho_role_name] = {
                "member_id": int(member_id),
                "channel_id": channel.id,
            }

        def mutate(data):
            game_block = data["game"]
            game_block["started"] = True
            game_block["current_day"] = 0
            game_block["ho_category_id"] = category.id
            game_block["ho_assignments"] = ho_assignments
            game_block["votes"] = {}
            data["roles"]["ho_roles"] = ho_roles

        await storage.Storage.update_state(mutate)

        await utils.ensure_gm_environment(guild)
        self.bot.dispatch("ensure_dashboard", guild.id)
        await self._send_log(guild, "ゲームを開始しました。HOチャンネルを構築しました。")
        await interaction.followup.send(
            f"{len(created_channels)}人分のHOチャンネルとロールを作成しました。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GameCog(bot))
