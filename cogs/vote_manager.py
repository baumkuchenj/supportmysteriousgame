# cogs/vote_manager.py
import discord
from discord import app_commands
from discord.ext import commands

from storage import Storage
from utils.helpers import ensure_gm_environment

class VoteManagerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="start_vote", description="投票を開始（雛形）")
    async def start_vote(self, interaction: discord.Interaction):
        await interaction.response.send_message("🗳️ 投票開始（雛形）", ephemeral=True)

    @app_commands.command(name="close_vote", description="夜の投票を締め切る（以降の投票は無効）")
    async def close_vote(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        await Storage.ensure_loaded()
        Storage.set_voting_open(interaction.guild.id, False)
        # GM集計メッセージを更新
        gm_role, gm_dash, _ = await ensure_gm_environment(interaction.guild)
        gm_category = gm_dash.category
        vote_channel = None
        if gm_category:
            vote_channel = discord.utils.get(gm_category.text_channels, name="vote_night")
        text = "🗳️ 夜の投票は締め切られました。集計結果:\n"
        # 軽い最終集計（DayProgressCogのロジックに依存しないよう簡易表示）
        parts = Storage.get_participants(interaction.guild.id)
        name_by_ho = {str(p.get("ho")): p.get("name") for p in parts if p.get("ho")}
        votes = Storage.get_votes(interaction.guild.id)
        lines = []
        for ho in sorted(name_by_ho.keys()):
            target = votes.get(ho)
            if target:
                tname = name_by_ho.get(target, target)
                lines.append(f"{ho} → {target} ({tname})")
            else:
                lines.append(f"{ho} → 未投票")
        text += "\n".join(lines)
        if vote_channel is not None:
            msg_id = Storage.get_gm_vote_message(interaction.guild.id)
            try:
                if msg_id:
                    msg = await vote_channel.fetch_message(msg_id)
                    await msg.edit(content=text)
                else:
                    msg = await vote_channel.send(text)
                    Storage.set_gm_vote_message(interaction.guild.id, msg.id)
            except discord.NotFound:
                msg = await vote_channel.send(text)
                Storage.set_gm_vote_message(interaction.guild.id, msg.id)
        await interaction.response.send_message("⛔ 夜の投票を締め切りました", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoteManagerCog(bot))
