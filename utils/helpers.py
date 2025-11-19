# utils/helpers.py
import discord
from typing import Tuple
from config import (
    GM_CATEGORY_NAME,
    GM_ROLE_NAME,
    DASHBOARD_CHANNEL_NAME,
    LOG_CHANNEL_NAME,
    PLAYER_ROLE_NAME,
)


async def ensure_gm_environment(guild: discord.Guild) -> Tuple[discord.Role, discord.TextChannel, discord.TextChannel]:
    """Ensure GM role/category/channels and return (gm_role, dashboard, log)."""
    # role
    gm_role = discord.utils.get(guild.roles, name=GM_ROLE_NAME)
    if gm_role is None:
        try:
            gm_role = await guild.create_role(name=GM_ROLE_NAME, reason="GM role for Werewolf")
        except discord.Forbidden:
            gm_role = discord.utils.get(guild.roles, name='@everyone')  # fallback

    # category
    gm_category = discord.utils.get(guild.categories, name=GM_CATEGORY_NAME)
    if gm_category is None:
        gm_category = await guild.create_category(GM_CATEGORY_NAME)

    # channels
    # まずはギルド全体から既存チャンネルを探す
    dash = discord.utils.get(guild.text_channels, name=DASHBOARD_CHANNEL_NAME)
    if dash is None:
        dash = await guild.create_text_channel(DASHBOARD_CHANNEL_NAME, category=gm_category)
    else:
        # 所属カテゴリが違えば移動
        if dash.category_id != gm_category.id:
            try:
                await dash.edit(category=gm_category)
            except discord.Forbidden:
                pass

    log = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if log is None:
        log = await guild.create_text_channel(LOG_CHANNEL_NAME, category=gm_category)
    else:
        if log.category_id != gm_category.id:
            try:
                await log.edit(category=gm_category)
            except discord.Forbidden:
                pass

    return gm_role, dash, log


def has_gm_or_manage_guild(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    gm_role = discord.utils.get(interaction.guild.roles, name=GM_ROLE_NAME)
    if gm_role and gm_role in getattr(interaction.user, 'roles', []):
        return True
    return bool(interaction.user.guild_permissions.manage_guild)


async def ensure_player_role(guild: discord.Guild) -> discord.Role:
    """Ensure the player role exists and return it."""
    role = discord.utils.get(guild.roles, name=PLAYER_ROLE_NAME)
    if role is None:
        try:
            role = await guild.create_role(name=PLAYER_ROLE_NAME, reason="Player role for Werewolf")
        except discord.Forbidden:
            # フォールバック: @everyone を返す（機能制限）
            role = discord.utils.get(guild.roles, name='@everyone')
    return role


def is_member_spirit(member: discord.Member) -> bool:
    for r in member.roles:
        if str(r.name) == "霊界":
            return True
    return False
