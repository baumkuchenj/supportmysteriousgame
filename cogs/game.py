# cogs/game.py
import discord
from discord import app_commands
from discord.ext import commands

from storage import Storage


class GameCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="reset_game", description="ゲーム進行データを初期化")
    async def reset_game(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        await Storage.ensure_loaded()
        Storage.reset_guild(interaction.guild.id)
        await interaction.response.send_message("🔁 このサーバーのゲームデータをリセットしました", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GameCog(bot))
