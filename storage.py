"""Persistent storage management for the Werewolf support bot."""
from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, MutableMapping, Optional

import config

logger = logging.getLogger(__name__)


class Storage:
    """Small helper around a JSON file used to persist game state."""

    _state: Optional[MutableMapping[str, Any]] = None
    _lock: asyncio.Lock = asyncio.Lock()
    _file_path: Path = Path(__file__).resolve().parent / config.STORAGE_FILE_NAME

    @classmethod
    def _default_state(cls) -> MutableMapping[str, Any]:
        return {
            "entry": {
                "message_id": None,
                "channel_id": None,
                "players": {},
            },
            "roles": {
                "player_role_id": None,
                "gm_role_id": None,
                "ho_roles": {},
            },
            "gm": {
                "category_id": None,
                "control_channel_id": None,
                "log_channel_id": None,
                "dashboard_message_id": None,
            },
            "game": {
                "started": False,
                "current_day": 0,
                "ho_category_id": None,
                "ho_assignments": {},
                "votes": {},
                "vote_messages": {},
            },
        }

    @classmethod
    async def ensure_loaded(cls) -> None:
        async with cls._lock:
            if cls._state is not None:
                return
            if cls._file_path.exists():
                try:
                    cls._state = json.loads(cls._file_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    logger.error("Failed to load state file: %s", exc)
                    cls._state = cls._default_state()
            else:
                cls._file_path.write_text(json.dumps(cls._default_state(), ensure_ascii=False, indent=2), encoding="utf-8")
                cls._state = cls._default_state()

    @classmethod
    async def read_state(cls) -> MutableMapping[str, Any]:
        await cls.ensure_loaded()
        async with cls._lock:
            assert cls._state is not None
            return deepcopy(cls._state)

    @classmethod
    async def update_state(
        cls, mutator: Callable[[MutableMapping[str, Any]], None]
    ) -> MutableMapping[str, Any]:
        await cls.ensure_loaded()
        async with cls._lock:
            assert cls._state is not None
            mutator(cls._state)
            cls._file_path.write_text(
                json.dumps(cls._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return deepcopy(cls._state)

    @classmethod
    async def reset_state(cls) -> None:
        async with cls._lock:
            cls._state = cls._default_state()
            cls._file_path.write_text(
                json.dumps(cls._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


async def get_state() -> MutableMapping[str, Any]:
    return await Storage.read_state()
