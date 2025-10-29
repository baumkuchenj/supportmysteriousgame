"""Cog implementing the entry (player registration) workflow."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import storage
import utils

logger = logging.getLogger(__name__)


class EntryView(discord.ui.View):
    def __init__(self, cog: "EntryCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:  # type: ignore[override]
        logger.exception("Entry view error: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message("内部エラーが発生しました。", ephemeral=True)

    @discord.ui.button(
        label=config.ENTRY_JOIN_BUTTON_LABEL,
        style=discord.ButtonStyle.success,
        custom_id=config.ENTRY_JOIN_BUTTON_ID,
    )
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.handle_join(interaction)

    @discord.ui.button(
        label=config.ENTRY_LEAVE_BUTTON_LABEL,
        style=discord.ButtonStyle.danger,
        custom_id=config.ENTRY_LEAVE_BUTTON_ID,
    )
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.handle_leave(interaction)


class EntryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.view = EntryView(self)
        self.bot.add_view(self.view)

    async def cog_load(self) -> None:  # type: ignore[override]
        await storage.Storage.ensure_loaded()

    async def ensure_roles(self, guild: discord.Guild) -> discord.Role:
        state = await storage.Storage.read_state()
        player_role_id = state["roles"].get("player_role_id")
        player_role = guild.get_role(player_role_id) if player_role_id else None
        if player_role is None:
            player_role = discord.utils.get(guild.roles, name=config.PLAYER_ROLE_NAME)
        if player_role is None:
            player_role = await guild.create_role(
                name=config.PLAYER_ROLE_NAME,
                reason="Create Player role for Werewolf game",
            )
        gm_role_id = state["roles"].get("gm_role_id")
        gm_role = guild.get_role(gm_role_id) if gm_role_id else None
        if gm_role is None:
            gm_role = discord.utils.get(guild.roles, name=config.GM_ROLE_NAME)
        if gm_role is None:
            gm_role = await guild.create_role(
                name=config.GM_ROLE_NAME,
                reason="Create GM role for Werewolf game",
            )

        def mutate(data):
            roles = data["roles"]
            roles["player_role_id"] = player_role.id
            roles["gm_role_id"] = gm_role.id

        await storage.Storage.update_state(mutate)
        return player_role

    async def build_entry_embed(self) -> discord.Embed:
        state = await storage.Storage.read_state()
        embed = discord.Embed(
            title=config.ENTRY_EMBED_TITLE,
            description=config.ENTRY_EMBED_DESCRIPTION,
            colour=config.ENTRY_MESSAGE_COLOR,
        )
        players = state["entry"]["players"]
        if players:
            lines = [
                f"• {info['name']} (<@{member_id}>)"
                for member_id, info in sorted(players.items(), key=lambda item: item[1]["name"].lower())
            ]
            embed.add_field(
                name=f"現在の参加者 ({len(players)}名)",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(name="現在の参加者", value="まだ参加者はいません。", inline=False)
        return embed

    async def fetch_entry_message(self, state: Optional[dict] = None) -> Optional[discord.Message]:
        state = state or await storage.Storage.read_state()
        channel_id = state["entry"].get("channel_id")
        message_id = state["entry"].get("message_id")
        if not channel_id or not message_id:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None and self.bot.is_ready():
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.NotFound:
                return None
        if isinstance(channel, discord.TextChannel):
            try:
                return await channel.fetch_message(message_id)
            except discord.NotFound:
                logger.warning("Entry message (%s) was not found", message_id)
        return None

    async def update_entry_message(self) -> None:
        message = await self.fetch_entry_message()
        if message is None:
            return
        embed = await self.build_entry_embed()
        await message.edit(embed=embed, view=self.view)

    @app_commands.command(name="entry", description="人狼ゲームの参加募集を開始します。")
    async def start_entry(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ使用できます。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        await self.ensure_roles(guild)
        await utils.ensure_gm_environment(guild)
        embed = await self.build_entry_embed()
        message = await interaction.channel.send(embed=embed, view=self.view)
        self.bot.dispatch("ensure_dashboard", guild.id)

        def mutate(data):
            entry = data["entry"]
            entry["message_id"] = message.id
            entry["channel_id"] = message.channel.id

        await storage.Storage.update_state(mutate)
        await interaction.followup.send(
            "参加募集を開始しました。メッセージをピン留めしておくと便利です。",
            ephemeral=True,
        )

    async def handle_join(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return
        guild = interaction.guild
        player_role = await self.ensure_roles(guild)
        member = interaction.user

        state = await storage.Storage.read_state()
        players = state["entry"]["players"]
        member_id = str(member.id)
        if member_id in players:
            await interaction.response.send_message("既に参加しています。", ephemeral=True)
            return

        await member.add_roles(player_role, reason="Werewolf game entry join")

        def mutate(data):
            data["entry"]["players"][member_id] = {"name": member.display_name}

        await storage.Storage.update_state(mutate)
        await interaction.response.send_message("参加を受け付けました。", ephemeral=True)
        await self.update_entry_message()

    async def handle_leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return
        guild = interaction.guild
        player_role = await self.ensure_roles(guild)
        member = interaction.user

        state = await storage.Storage.read_state()
        players = state["entry"]["players"]
        member_id = str(member.id)
        if member_id not in players:
            await interaction.response.send_message("参加していません。", ephemeral=True)
            return

        await member.remove_roles(player_role, reason="Werewolf game entry leave")

        def mutate(data):
            data["entry"]["players"].pop(member_id, None)

        await storage.Storage.update_state(mutate)
        await interaction.response.send_message("参加をキャンセルしました。", ephemeral=True)
        await self.update_entry_message()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EntryCog(bot))
