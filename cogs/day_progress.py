# cogs/day_progress.py
import discord
from discord import app_commands
from discord.ext import commands

from storage import Storage
from utils.helpers import ensure_gm_environment, is_member_spirit


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
        # GM操作は表示せず、gm-logへ記載
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        from utils.helpers import ensure_gm_environment as _egm
        _, _, log = await _egm(interaction.guild)
        await log.send(f"[GM Action] {interaction.user.mention} 翌日に進行")

    @app_commands.command(name="night_phase", description="夜に進行（Phase=night）")
    async def night_phase(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        await Storage.ensure_loaded()
        guild = interaction.guild
        Storage.ensure_game(guild.id)
        Storage.data["game"][str(guild.id)]["phase"] = "night"
        
        # 夜投票は行わない。夜アクションのみに切替
        Storage.clear_night_actions(guild.id)
        Storage.save()

        # GM集計チャンネル (vote_night) をGMカテゴリに用意し、集計メッセージを送信
        gm_role, gm_dash, _ = await ensure_gm_environment(guild)
        gm_category = gm_dash.category
        vote_channel = None
        if gm_category:
            vote_channel = discord.utils.get(gm_category.text_channels, name="vote_night")
        if vote_channel is None:
            vote_channel = await guild.create_text_channel("vote_night", category=gm_category)

        # 初期集計の投稿（夜アクションのみ）
        tally = self._build_tally_text(guild.id)
        gm_msg = await vote_channel.send(tally)
        Storage.set_gm_vote_message(guild.id, gm_msg.id)

        # GM操作は表示せず、gm-logへ記載
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        from utils.helpers import ensure_gm_environment as _egm
        _, _, log = await _egm(guild)
        await log.send(f"[GM Action] {interaction.user.mention} 夜フェーズへ移行（夜投票は行わない）")

    # ===== 内部ユーティリティ =====
    def _build_tally_text(self, guild_id: int) -> str:
        parts = Storage.get_participants(guild_id)
        name_by_ho = {str(p.get("ho")): p.get("name") for p in parts if p.get("ho")}
        lines = ["🌓 夜の行動状況"]
        # 占い/狩人の夜アクション状況
        na = Storage.get_night_actions(guild_id)
        for role in ("占い", "狩人"):
            role_map = na.get(role, {})
            for voter_ho, target in sorted(role_map.items()):
                if not voter_ho:
                    continue
                if target:
                    tname = name_by_ho.get(target, target)
                    lines.append(f"{role}: {voter_ho} → {target} ({tname})")
                else:
                    lines.append(f"{role}: {voter_ho} → 未選択")
        return "\n".join(lines)

    def _build_vote_view(self, guild_id: int, voter_ho: str) -> discord.ui.View:
        parts = Storage.get_participants(guild_id)
        options = []
        for p in parts:
            ho = p.get("ho")
            if not ho or ho == voter_ho:
                continue
            # 霊界は投票先の対象外
            # interaction.guild がないのでメンバー取得は実行時に行えないため、
            # ここでは候補構築時点では除外できないケースがある。送信側で除外済み。
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
                # 霊界は投票対象外のため最終チェック（もし存在するなら弾く）
                if interaction.guild:
                    # target は HO名。対応メンバーが霊界なら拒否
                    parts_local = Storage.get_participants(interaction.guild.id)
                    member = None
                    for pp in parts_local:
                        if str(pp.get("ho") or "").upper() == str(target).upper():
                            member = interaction.guild.get_member(int(pp.get("id", 0)))
                            break
                    if member and is_member_spirit(member):
                        await interaction.response.send_message("その対象は指定できません", ephemeral=True)
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
