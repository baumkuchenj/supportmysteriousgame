# storage.py
from __future__ import annotations
import os
import json
import asyncio
import requests
from typing import Any, Dict, List, Optional, Union

Json = Dict[str, Any]


class Storage:
    data_file: str = os.getenv("DATA_FILE", "data.json")
    _loaded: bool = False
    _backend: str = os.getenv("STORAGE_BACKEND", "file").lower()
    _upstash_url: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
    _upstash_token: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
    _upstash_key: str = os.getenv("STORAGE_KEY", "werewolf:data")

    data: Json = {
        "participants": {},           # {guild_id: [ {id:int, name:str, ho: Optional[str]} ]}
        "game": {},                   # {guild_id: {"day": int, "phase": str}}
        "votes": {},                  # {guild_id: { voter_ho: target_ho|None, ... }}
        "voting_open": {},            # {guild_id: bool}
        "gm_vote_message_id": {},     # {guild_id: int}
        "dashboard_message_id": {},   # {guild_id: int}
        "spirit_reverse_used": {},    # {guild_id: bool}
    }

    # ---------- IO ----------
    @classmethod
    async def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        if cls._backend == "upstash" and cls._upstash_url and cls._upstash_token:
            try:
                raw = await cls._kv_get()
                if not isinstance(raw, dict) or not raw:
                    cls._fresh()
                else:
                    for k, v in cls.data.items():
                        if k not in raw:
                            raw[k] = v
                    cls.data = raw
            except Exception:
                cls._fresh()
        else:
            if os.path.exists(cls.data_file):
                try:
                    with open(cls.data_file, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    for k, v in cls.data.items():
                        if k not in raw:
                            raw[k] = v
                    cls.data = raw
                except Exception:
                    cls._fresh()
            else:
                cls._fresh()
        cls._loaded = True

    @classmethod
    def _fresh(cls) -> None:
        cls.data = {"participants": {}, "game": {}, "votes": {}, "voting_open": {}, "gm_vote_message_id": {}, "dashboard_message_id": {}, "spirit_reverse_used": {}}
        cls.save()

    @classmethod
    def save(cls) -> None:
        try:
            if cls._backend == "upstash" and cls._upstash_url and cls._upstash_token:
                try:
                    json_str = json.dumps(cls.data, ensure_ascii=False)
                except Exception as e:
                    print(f"[Storage] serialize failed: {e}")
                    return
                try:
                    headers = {"Authorization": f"Bearer {cls._upstash_token}"}
                    url = cls._upstash_url.rstrip("/") + f"/set/{cls._upstash_key}"
                    resp = requests.post(url, headers=headers, json={"value": json_str}, timeout=10)
                    if resp.status_code >= 300:
                        print(f"[Storage] kv set failed: {resp.status_code} {resp.text}")
                except Exception as e:
                    print(f"[Storage] kv save failed: {e}")
            else:
                with open(cls.data_file, "w", encoding="utf-8") as f:
                    json.dump(cls.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Storage] save failed: {e}")

    @classmethod
    async def _kv_get(cls) -> Json:
        def _do() -> Json:
            headers = {"Authorization": f"Bearer {cls._upstash_token}"}
            url = cls._upstash_url.rstrip("/") + f"/get/{cls._upstash_key}"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code >= 300:
                return {}
            try:
                payload = resp.json()
                val = payload.get("result")
                if not val:
                    return {}
                return json.loads(val)
            except Exception:
                return {}
        return await asyncio.to_thread(_do)

    # ---------- helpers ----------
    @classmethod
    def _g(cls, guild_id: int) -> str:
        return str(guild_id)

    # ---------- participants ----------
    @classmethod
    def get_participants(cls, guild_id: int) -> List[Json]:
        return list(cls.data["participants"].get(cls._g(guild_id), []))

    @classmethod
    def set_participants(cls, guild_id: int, participants: List[Json]) -> None:
        gid = cls._g(guild_id)
        cls.data["participants"][gid] = participants
        cls.save()

    @classmethod
    def get_participant_names(cls, guild_id: int) -> List[str]:
        return [str(p.get("name", "")) for p in cls.get_participants(guild_id)]

    @classmethod
    def add_participant(cls, guild_id: int, user: Union[Any, Dict[str, Any]]) -> None:
        gid = cls._g(guild_id)
        cls.data["participants"].setdefault(gid, [])
        if hasattr(user, "id") and hasattr(user, "display_name"):
            uid = int(user.id)
            name = str(user.display_name)
        else:
            uid = int(user["id"])  # type: ignore[index]
            name = str(user["name"])  # type: ignore[index]
        for p in cls.data["participants"][gid]:
            if int(p["id"]) == uid:
                return
        cls.data["participants"][gid].append({"id": uid, "name": name, "ho": None})
        cls.save()

    @classmethod
    def remove_participant(cls, guild_id: int, user_id: int) -> None:
        gid = cls._g(guild_id)
        arr = cls.data["participants"].get(gid, [])
        arr = [p for p in arr if int(p["id"]) != int(user_id)]
        cls.data["participants"][gid] = arr
        cls.save()

    @classmethod
    def assign_ho_sequential(cls, guild_id: int) -> List[Json]:
        """
        参加順に HO1, HO2, ... を割り当てて保存して返す
        """
        gid = cls._g(guild_id)
        arr = cls.data["participants"].get(gid, [])
        for i, p in enumerate(arr, start=1):
            p["ho"] = f"HO{i}"
        cls.data["participants"][gid] = arr
        cls.save()
        return arr

    # ---------- game ----------
    @classmethod
    def ensure_game(cls, guild_id: int) -> None:
        gid = cls._g(guild_id)
        cls.data["game"].setdefault(gid, {"day": 0, "phase": "day"})
        cls.save()

    @classmethod
    def reset_guild(cls, guild_id: int) -> None:
        gid = cls._g(guild_id)
        cls.data["participants"][gid] = []
        cls.data["game"][gid] = {"day": 0, "phase": "day"}
        cls.data["votes"][gid] = {}
        cls.data["voting_open"][gid] = False
        cls.data["gm_vote_message_id"].pop(gid, None)
        cls.data["dashboard_message_id"].pop(gid, None)
        cls.data["spirit_reverse_used"][gid] = False
        cls.save()

    # ---------- night vote ----------
    @classmethod
    def init_votes(cls, guild_id: int, participants_ho: List[str]) -> None:
        gid = cls._g(guild_id)
        cls.data["votes"].setdefault(gid, {})
        cls.data["votes"][gid] = {voter: None for voter in participants_ho}
        cls.save()

    @classmethod
    def set_vote(cls, guild_id: int, voter_ho: str, target_ho: Optional[str]) -> None:
        gid = cls._g(guild_id)
        cls.data["votes"].setdefault(gid, {})
        cls.data["votes"][gid][voter_ho] = target_ho
        cls.save()

    @classmethod
    def get_votes(cls, guild_id: int) -> Dict[str, Optional[str]]:
        return dict(cls.data["votes"].get(cls._g(guild_id), {}))

    @classmethod
    def set_voting_open(cls, guild_id: int, is_open: bool) -> None:
        cls.data["voting_open"][cls._g(guild_id)] = bool(is_open)
        cls.save()

    @classmethod
    def is_voting_open(cls, guild_id: int) -> bool:
        return bool(cls.data["voting_open"].get(cls._g(guild_id), False))

    @classmethod
    def set_gm_vote_message(cls, guild_id: int, message_id: int) -> None:
        cls.data["gm_vote_message_id"][cls._g(guild_id)] = int(message_id)
        cls.save()

    @classmethod
    def get_gm_vote_message(cls, guild_id: int) -> Optional[int]:
        return cls.data["gm_vote_message_id"].get(cls._g(guild_id))

    # ---------- dashboard panel message ----------
    @classmethod
    def set_dashboard_message(cls, guild_id: int, message_id: int) -> None:
        cls.data["dashboard_message_id"][cls._g(guild_id)] = int(message_id)
        cls.save()

    @classmethod
    def get_dashboard_message(cls, guild_id: int) -> Optional[int]:
        return cls.data["dashboard_message_id"].get(cls._g(guild_id))

    # ---------- spirit reverse ----------
    @classmethod
    def is_spirit_reverse_used(cls, guild_id: int) -> bool:
        return bool(cls.data.get("spirit_reverse_used", {}).get(cls._g(guild_id), False))

    @classmethod
    def set_spirit_reverse_used(cls, guild_id: int, used: bool) -> None:
        gid = cls._g(guild_id)
        cls.data.setdefault("spirit_reverse_used", {})
        cls.data["spirit_reverse_used"][gid] = bool(used)
        cls.save()
