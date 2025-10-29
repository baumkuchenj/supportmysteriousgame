"""Utility helpers for guild setup and Discord specific helpers."""
from __future__ import annotations

import logging
from typing import Tuple

import discord

import config
import storage

logger = logging.getLogger(__name__)


async def ensure_gm_environment(
    guild: discord.Guild,
) -> Tuple[discord.CategoryChannel, discord.TextChannel, discord.TextChannel]:
    """Create the GM category and its control/log channels if needed."""
    state = await storage.Storage.read_state()

    category = (
        guild.get_channel(state["gm"].get("category_id"))
        if state["gm"].get("category_id")
        else None
    )
    if category is None:
        category = await guild.create_category(
            name=config.GM_CATEGORY_NAME,
            reason="Create GM management category",
        )
        logger.info("Created GM category %s", category.id)

    control_channel = (
        guild.get_channel(state["gm"].get("control_channel_id"))
        if state["gm"].get("control_channel_id")
        else None
    )
    if control_channel is None:
        control_channel = await category.create_text_channel(
            name=config.GM_CONTROL_CHANNEL_NAME,
            reason="Create GM control channel",
        )
        logger.info("Created GM control channel %s", control_channel.id)

    log_channel = (
        guild.get_channel(state["gm"].get("log_channel_id"))
        if state["gm"].get("log_channel_id")
        else None
    )
    if log_channel is None:
        log_channel = await category.create_text_channel(
            name=config.GM_LOG_CHANNEL_NAME,
            reason="Create GM dashboard log channel",
        )
        logger.info("Created GM log channel %s", log_channel.id)

    def mutate(data):
        gm_block = data["gm"]
        gm_block["category_id"] = category.id
        gm_block["control_channel_id"] = control_channel.id
        gm_block["log_channel_id"] = log_channel.id

    await storage.Storage.update_state(mutate)
    return category, control_channel, log_channel
