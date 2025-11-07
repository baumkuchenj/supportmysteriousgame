# cogs/day_progress.py
import discord
from discord import app_commands
from discord.ext import commands

from storage import Storage
from utils.helpers import ensure_gm_environment


class DayProgressCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="next_day", description="翌日に進む（Day+1 / Phase=day）")
    async def next_day(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        await Storage.ensure_loaded()
        Storage.ensure_game(interaction.guild.id)
        # simple impl: reset phase to day
        Storage.data["game"][str(interaction.guild.id)]["day"] += 1
        Storage.data["game"][str(interaction.guild.id)]["phase"] = "day"
        Storage.save()
        await interaction.response.send_message("🌅 翌日に進みました", ephemeral=True)

    @app_commands.command(name="night_phase", description="夜に進行（Phase=night）")
    async def night_phase(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        await Storage.ensure_loaded()
        guild = interaction.guild
        Storage.ensure_game(guild.id)
        Storage.data["game"][str(guild.id)]["phase"] = "night"
        
        # 準備: 参加者とHO一覧
        participants = Storage.get_participants(guild.id)
        ho_list = [p.get("ho") for p in participants if p.get("ho")]
        # 投票初期化とオープン
        Storage.init_votes(guild.id, ho_list)
        Storage.set_voting_open(guild.id, True)
        Storage.save()

        # GM集計チャンネル (vote_night) をGMカテゴリに用意し、集計メッセージを送信
        gm_role, gm_dash, _ = await ensure_gm_environment(guild)
        gm_category = gm_dash.category
        vote_channel = None
        if gm_category:
            vote_channel = discord.utils.get(gm_category.text_channels, name="vote_night")
        if vote_channel is None:
            vote_channel = await guild.create_text_channel("vote_night", category=gm_category)

        # 初期集計の投稿
        tally = self._build_tally_text(guild.id)
        gm_msg = await vote_channel.send(tally)
        Storage.set_gm_vote_message(guild.id, gm_msg.id)

        # 各HOチャンネルに投票UIを設置
        # 候補は「自分以外のHO」
        for p in participants:
            ho = str(p.get("ho") or "").upper()
            if not ho:
                continue
            channel = discord.utils.get(guild.text_channels, name=ho.lower())
            if channel is None:
                continue
            # ビュー生成
            view = self._build_vote_view(guild.id, ho)
            await channel.send("誰か一人を選択してください", view=view)

        await interaction.response.send_message("🌙 夜に移行し、各個別チャンネルに投票UIを配置しました", ephemeral=True)

    # ===== 内部ユーティリティ =====
    def _build_tally_text(self, guild_id: int) -> str:
        votes = Storage.get_votes(guild_id)
        parts = Storage.get_participants(guild_id)
        name_by_ho = {str(p.get("ho")): p.get("name") for p in parts if p.get("ho")}
        lines = ["🗳️ 夜の投票状況"]
        for ho in sorted(name_by_ho.keys()):
            target = votes.get(ho)
            if target:
                tname = name_by_ho.get(target, target)
                lines.append(f"{ho} → {target} ({tname})")
            else:
                lines.append(f"{ho} → 未投票")
        return "\n".join(lines)

    def _build_vote_view(self, guild_id: int, voter_ho: str) -> discord.ui.View:
        parts = Storage.get_participants(guild_id)
        options = []
        for p in parts:
            ho = p.get("ho")
            if not ho or ho == voter_ho:
                continue
            label = f"{ho} {p.get('name','')}"
            options.append(discord.SelectOption(label=label, value=str(ho)))
        if not options:
            options = [discord.SelectOption(label="候補なし", value="none")]

        parent = self

        class NightTargetSelect(discord.ui.Select):
            def __init__(self):
                super().__init__(placeholder="投票先を選択", min_values=1, max_values=1, options=options)

            async def callback(self, interaction: discord.Interaction):
                if not Storage.is_voting_open(guild_id):
                    await interaction.response.send_message("投票は締め切られています", ephemeral=True)
                    return
                parent._selected_target = self.values[0]
                await interaction.response.send_message("✅ 選択を一時保存しました。送信で確定します。", ephemeral=True)

        class SubmitVote(discord.ui.Button):
            def __init__(self):
                super().__init__(label="送信", style=discord.ButtonStyle.primary)

            async def callback(self, interaction: discord.Interaction):
                if not Storage.is_voting_open(guild_id):
                    await interaction.response.send_message("投票は締め切られています", ephemeral=True)
                    return
                target = getattr(parent, "_selected_target", None)
                if not target or target == "none":
                    await interaction.response.send_message("投票先を選択してください", ephemeral=True)
                    return
                Storage.set_vote(guild_id, voter_ho, target)
                # GM集計メッセージ更新
                await parent._update_gm_tally(interaction.guild)
                await interaction.response.send_message("🗳️ 投票を受け付けました", ephemeral=True)

        view = discord.ui.View(timeout=None)
        view.add_item(NightTargetSelect())
        view.add_item(SubmitVote())
        return view

    async def _update_gm_tally(self, guild: discord.Guild):
        gm_role, gm_dash, _ = await ensure_gm_environment(guild)
        gm_category = gm_dash.category
        vote_channel = None
        if gm_category:
            vote_channel = discord.utils.get(gm_category.text_channels, name="vote_night")
        if vote_channel is None:
            vote_channel = await guild.create_text_channel("vote_night", category=gm_category)
        msg_id = Storage.get_gm_vote_message(guild.id)
        text = self._build_tally_text(guild.id)
        try:
            if msg_id:
                msg = await vote_channel.fetch_message(msg_id)
                await msg.edit(content=text)
            else:
                msg = await vote_channel.send(text)
                Storage.set_gm_vote_message(guild.id, msg.id)
        except discord.NotFound:
            # 再投稿
            msg = await vote_channel.send(text)
            Storage.set_gm_vote_message(guild.id, msg.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(DayProgressCog(bot))
