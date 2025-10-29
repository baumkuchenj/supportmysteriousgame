"""Cog implementing the GM dashboard and role specific actions."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import discord
from discord.ext import commands

import config
import storage
import utils

logger = logging.getLogger(__name__)

ACTION_LABELS = {
    "seer": "占い",
    "hunter": "狩人",
    "medium": "霊能",
    "werewolf": "人狼",
}


class RoleTargetSelect(discord.ui.Select):
    def __init__(self, view: "GMDashboardView", action_key: str, label: str) -> None:
        super().__init__(
            placeholder=f"{label}の対象を選択",
            custom_id=f"dashboard_select_{action_key}",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="対象がいません", value="__none__")],
        )
        self.view_ref = view
        self.action_key = action_key
        self.update_options([])

    def update_options(self, options: List[discord.SelectOption]) -> None:
        if options:
            self.options = options
            self.disabled = False
        else:
            self.options = [discord.SelectOption(label="対象がいません", value="__none__")]
            self.disabled = True

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if self.disabled:
            await interaction.response.send_message("選択可能な対象がありません。", ephemeral=True)
            return
        value = self.values[0]
        self.view_ref.selected_targets[self.action_key] = value
        option = discord.utils.get(self.options, value=value)
        label = option.label if option else value
        await interaction.response.send_message(f"{ACTION_LABELS[self.action_key]}の対象を {label} に設定しました。", ephemeral=True)


class RoleActionModal(discord.ui.Modal):
    def __init__(self, cog: "DashboardCog", action_key: str, target_ho: str) -> None:
        super().__init__(title=f"{ACTION_LABELS[action_key]}結果の送信", custom_id=f"dashboard_modal_{action_key}")
        self.cog = cog
        self.action_key = action_key
        self.target_ho = target_ho
        self.message = discord.ui.TextInput(
            label="送信するメッセージ",
            placeholder="結果を入力してください",
            style=discord.TextStyle.paragraph,
            max_length=2000,
        )
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self.cog.process_role_action(interaction, self.action_key, self.target_ho, str(self.message.value))


class VoteSelect(discord.ui.Select):
    def __init__(self, view: "VoteView", options: List[discord.SelectOption]) -> None:
        custom_id = f"{config.VOTE_VIEW_CUSTOM_ID}:{view.day}:{view.ho_name}"
        super().__init__(
            placeholder=config.VOTE_PROMPT_TEXT,
            min_values=1,
            max_values=1,
            options=options,
            custom_id=custom_id,
        )
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        value = self.values[0]
        self.view_ref.selected_target = value
        option = discord.utils.get(self.options, value=value)
        label = option.label if option else value
        await interaction.response.send_message(f"投票対象を {label} に設定しました。", ephemeral=True)

class VoteView(discord.ui.View):
    def __init__(self, cog: "DashboardCog", ho_name: str, day: int, options: List[discord.SelectOption]) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.ho_name = ho_name
        self.day = day
        self.selected_target: Optional[str] = None
        self.add_item(VoteSelect(self, options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return False
        assignment = await self.cog.get_assignment(self.ho_name)
        if assignment and interaction.user.id == assignment.get("member_id"):
            return True
        if await self.cog.is_gm(interaction.user):
            return True
        await interaction.response.send_message("この操作は許可されていません。", ephemeral=True)
        return False

    @discord.ui.button(
        label="投票する",
        style=discord.ButtonStyle.primary,
        custom_id=f"{config.VOTE_SUBMIT_BUTTON_ID}",
    )
    async def submit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if self.selected_target is None:
            await interaction.response.send_message("投票対象を選択してください。", ephemeral=True)
            return
        await self.cog.submit_vote(interaction, self.ho_name, self.day, self.selected_target)


class GMDashboardView(discord.ui.View):
    def __init__(self, cog: "DashboardCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.selects: Dict[str, RoleTargetSelect] = {}
        self.selected_targets: Dict[str, Optional[str]] = {key: None for key in ACTION_LABELS}
        for key, label in ACTION_LABELS.items():
            select = RoleTargetSelect(self, key, label)
            self.selects[key] = select
            self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return False
        if not await self.cog.is_gm(interaction.user):
            await interaction.response.send_message("GM専用の操作です。", ephemeral=True)
            return False
        return True

    def update_target_options(self, options: List[discord.SelectOption]) -> None:
        for select in self.selects.values():
            select.update_options(options)

    @discord.ui.button(label="占い結果送信", style=discord.ButtonStyle.primary, custom_id="dashboard_send_seer")
    async def send_seer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.open_action_modal(interaction, "seer")

    @discord.ui.button(label="狩人結果送信", style=discord.ButtonStyle.primary, custom_id="dashboard_send_hunter")
    async def send_hunter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.open_action_modal(interaction, "hunter")

    @discord.ui.button(label="霊能結果送信", style=discord.ButtonStyle.primary, custom_id="dashboard_send_medium")
    async def send_medium(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.open_action_modal(interaction, "medium")

    @discord.ui.button(label="人狼連絡送信", style=discord.ButtonStyle.danger, custom_id="dashboard_send_werewolf")
    async def send_werewolf(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.open_action_modal(interaction, "werewolf")

    @discord.ui.button(label="日付を進める", style=discord.ButtonStyle.secondary, custom_id="dashboard_start_day")
    async def start_day(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.start_day(interaction)

    @discord.ui.button(label="ダッシュボード更新", style=discord.ButtonStyle.secondary, custom_id="dashboard_refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self.cog.refresh_dashboard(interaction)


class DashboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.view = GMDashboardView(self)
        self.bot.add_view(self.view)

    async def cog_load(self) -> None:  # type: ignore[override]
        await storage.Storage.ensure_loaded()
        if self.bot.is_ready():
            for guild in self.bot.guilds:
                await self.ensure_dashboard_message(guild)
                await self.restore_vote_views(guild)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self.ensure_dashboard_message(guild)
            await self.restore_vote_views(guild)

    async def is_gm(self, member: discord.Member) -> bool:
        state = await storage.Storage.read_state()
        gm_role_id = state["roles"].get("gm_role_id")
        gm_role = member.guild.get_role(gm_role_id) if gm_role_id else None
        if gm_role is None:
            return False
        return gm_role in member.roles

    async def get_assignment(self, ho_name: str) -> Optional[dict]:
        state = await storage.Storage.read_state()
        return state["game"].get("ho_assignments", {}).get(ho_name)

    async def ensure_dashboard_message(self, guild: discord.Guild) -> None:
        await utils.ensure_gm_environment(guild)
        state = await storage.Storage.read_state()
        control_channel_id = state["gm"].get("control_channel_id")
        if not control_channel_id:
            return
        channel = guild.get_channel(control_channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(control_channel_id)
            except discord.NotFound:
                return
        if not isinstance(channel, discord.TextChannel):
            return
        await self.refresh_target_options(guild)
        embed = await self.build_dashboard_embed(guild)
        message_id = state["gm"].get("dashboard_message_id")
        message: Optional[discord.Message] = None
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                message = None
        if message is None:
            message = await channel.send(embed=embed, view=self.view)

            def mutate(data):
                data["gm"]["dashboard_message_id"] = message.id

            await storage.Storage.update_state(mutate)
        else:
            await message.edit(embed=embed, view=self.view)

    async def refresh_target_options(self, guild: discord.Guild) -> None:
        state = await storage.Storage.read_state()
        assignments = state["game"].get("ho_assignments", {})
        options: List[discord.SelectOption] = []
        for ho_name, info in sorted(assignments.items(), key=lambda item: item[0]):
            member_id = info.get("member_id")
            member = guild.get_member(member_id) if member_id else None
            label = ho_name
            description = None
            if member:
                label = f"{ho_name} - {member.display_name}"
                description = f"@{member.display_name}"
            options.append(discord.SelectOption(label=label, value=ho_name, description=description))
        self.view.update_target_options(options)

    async def build_dashboard_embed(self, guild: discord.Guild) -> discord.Embed:
        state = await storage.Storage.read_state()
        game_state = state["game"]
        current_day = game_state.get("current_day", 0)
        embed = discord.Embed(
            title=config.GM_DASHBOARD_EMBED_TITLE,
            description=config.GM_DASHBOARD_EMBED_DESCRIPTION,
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="現在の日付", value=f"{current_day}日目", inline=False)
        assignments = game_state.get("ho_assignments", {})
        if assignments:
            lines = []
            for ho_name, info in sorted(assignments.items(), key=lambda item: item[0]):
                member = guild.get_member(info.get("member_id")) if info.get("member_id") else None
                mention = member.mention if member else f"ID:{info.get('member_id')}"
                lines.append(f"{ho_name}: {mention}")
            embed.add_field(name="HO割り当て", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="HO割り当て", value="まだゲームが開始されていません。", inline=False)
        votes = game_state.get("votes", {})
        if votes:
            latest_day = str(current_day)
            day_votes = votes.get(latest_day, {})
            if day_votes:
                vote_lines = []
                for voter_id, target_id in day_votes.items():
                    voter_member = guild.get_member(int(voter_id))
                    target_member = guild.get_member(int(target_id))
                    voter_name = voter_member.display_name if voter_member else voter_id
                    target_name = target_member.display_name if target_member else target_id
                    vote_lines.append(f"{voter_name} → {target_name}")
                embed.add_field(name=f"{latest_day}日目の投票", value="\n".join(vote_lines), inline=False)
        return embed

    async def open_action_modal(self, interaction: discord.Interaction, action_key: str) -> None:
        target_ho = self.view.selected_targets.get(action_key)
        if not target_ho:
            await interaction.response.send_message("対象を選択してください。", ephemeral=True)
            return
        assignment = await self.get_assignment(target_ho)
        if not assignment:
            await interaction.response.send_message("該当するHOが見つかりません。", ephemeral=True)
            return
        await interaction.response.send_modal(RoleActionModal(self, action_key, target_ho))

    async def process_role_action(
        self,
        interaction: discord.Interaction,
        action_key: str,
        target_ho: str,
        message_content: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return
        assignment = await self.get_assignment(target_ho)
        if not assignment:
            await interaction.response.send_message("チャンネルが見つかりません。", ephemeral=True)
            return
        channel = interaction.guild.get_channel(assignment.get("channel_id"))
        if channel is None:
            try:
                channel = await interaction.guild.fetch_channel(assignment.get("channel_id"))
            except discord.NotFound:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("チャンネルが見つかりません。", ephemeral=True)
            return
        await channel.send(f"【{ACTION_LABELS[action_key]}】\n{message_content}")
        await self.send_log(interaction.guild, f"{ACTION_LABELS[action_key]} → {target_ho}: {message_content}")
        await interaction.response.send_message("結果を送信しました。", ephemeral=True)

    async def start_day(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return
        state = await storage.Storage.read_state()
        assignments = state["game"].get("ho_assignments", {})
        if not assignments:
            await interaction.response.send_message("ゲームが開始されていません。", ephemeral=True)
            return
        next_day = state["game"].get("current_day", 0) + 1

        def mutate(data):
            data["game"]["current_day"] = next_day
            votes = data["game"].setdefault("votes", {})
            votes[str(next_day)] = {}
            data["game"].setdefault("vote_messages", {}).pop(str(next_day), None)

        await storage.Storage.update_state(mutate)
        await self.send_log(interaction.guild, f"{next_day}日目を開始しました。投票を受け付けます。")
        await interaction.response.send_message(f"{next_day}日目を開始しました。", ephemeral=True)
        await self.dispatch_vote_requests(interaction.guild, next_day)
        await self.ensure_dashboard_message(interaction.guild)

    async def refresh_dashboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return
        await self.ensure_dashboard_message(interaction.guild)
        await interaction.response.send_message("ダッシュボードを更新しました。", ephemeral=True)

    async def dispatch_vote_requests(self, guild: discord.Guild, day: int) -> None:
        state = await storage.Storage.read_state()
        assignments = state["game"].get("ho_assignments", {})
        if not assignments:
            return
        vote_messages: Dict[str, Dict[str, int]] = state["game"].setdefault("vote_messages", {})
        vote_messages[str(day)] = {}

        options = await self.vote_options(guild)
        for ho_name, info in assignments.items():
            channel = guild.get_channel(info.get("channel_id"))
            if channel is None:
                try:
                    channel = await guild.fetch_channel(info.get("channel_id"))
                except discord.NotFound:
                    await self.send_log(
                        guild,
                        f"{ho_name} のチャンネルが見つからないため投票UIを送信できませんでした。",
                    )
                    continue
            if not isinstance(channel, discord.TextChannel):
                await self.send_log(
                    guild,
                    f"{ho_name} のチャンネルがテキストチャンネルではないため投票UIを送信できませんでした。",
                )
                continue
            view = VoteView(self, ho_name, day, options)
            embed = discord.Embed(
                title=f"{day}日目の投票",
                description=config.VOTE_PROMPT_TEXT,
                colour=discord.Colour.gold(),
            )
            message = await channel.send(embed=embed, view=view)
            self.bot.add_view(view, message_id=message.id)
            vote_messages[str(day)][ho_name] = message.id

        def mutate(data):
            data["game"]["vote_messages"] = vote_messages

        await storage.Storage.update_state(mutate)

    async def vote_options(self, guild: discord.Guild) -> List[discord.SelectOption]:
        state = await storage.Storage.read_state()
        assignments = state["game"].get("ho_assignments", {})
        options: List[discord.SelectOption] = []
        for ho_name, info in sorted(assignments.items(), key=lambda item: item[0]):
            member_id = info.get("member_id")
            member = guild.get_member(member_id) if member_id else None
            label = ho_name
            description = None
            if member:
                label = f"{ho_name} - {member.display_name}"
                description = f"@{member.display_name}"
            options.append(discord.SelectOption(label=label, value=ho_name, description=description))
        return options

    async def submit_vote(
        self,
        interaction: discord.Interaction,
        ho_name: str,
        day: int,
        target_ho: str,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return
        assignment = await self.get_assignment(ho_name)
        if not assignment or assignment.get("member_id") != interaction.user.id:
            await interaction.response.send_message("あなたはこの投票を行えません。", ephemeral=True)
            return
        target_assignment = await self.get_assignment(target_ho)
        if not target_assignment:
            await interaction.response.send_message("対象のHOが見つかりません。", ephemeral=True)
            return

        def mutate(data):
            votes = data["game"].setdefault("votes", {})
            day_votes = votes.setdefault(str(day), {})
            day_votes[str(interaction.user.id)] = target_assignment.get("member_id")

        await storage.Storage.update_state(mutate)
        await self.send_log(
            interaction.guild,
            f"{day}日目 投票: {interaction.user.display_name} → {target_ho}",
        )
        await interaction.response.send_message("投票を受け付けました。", ephemeral=True)
        await self.ensure_dashboard_message(interaction.guild)

    async def restore_vote_views(self, guild: discord.Guild) -> None:
        state = await storage.Storage.read_state()
        vote_messages = state["game"].get("vote_messages", {})
        options = await self.vote_options(guild)
        for day, mapping in vote_messages.items():
            for ho_name, message_id in mapping.items():
                channel_id = state["game"].get("ho_assignments", {}).get(ho_name, {}).get("channel_id")
                channel = guild.get_channel(channel_id) if channel_id else None
                if channel is None and channel_id:
                    try:
                        channel = await guild.fetch_channel(channel_id)
                    except discord.NotFound:
                        continue
                if not isinstance(channel, discord.TextChannel):
                    continue
                view = VoteView(self, ho_name, int(day), options)
                self.bot.add_view(view, message_id=message_id)

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

    @commands.Cog.listener()
    async def on_ensure_dashboard(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild:
            await self.ensure_dashboard_message(guild)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardCog(bot))
