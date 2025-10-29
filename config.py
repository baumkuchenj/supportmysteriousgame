"""Configuration constants for the Werewolf support bot."""
from __future__ import annotations

PLAYER_ROLE_NAME: str = "Player"
GM_ROLE_NAME: str = "GM"
GM_CATEGORY_NAME: str = "GM専用"
GM_CONTROL_CHANNEL_NAME: str = "gm-control"
GM_LOG_CHANNEL_NAME: str = "gm-dashboard-log"
ENTRY_EMBED_TITLE: str = "人狼ゲーム参加者募集"
ENTRY_EMBED_DESCRIPTION: str = (
    "ボタンを押して参加またはキャンセルができます。\n"
    "参加者は自動的に Player ロールが付与されます。"
)
ENTRY_JOIN_BUTTON_ID: str = "entry_join"
ENTRY_LEAVE_BUTTON_ID: str = "entry_leave"
ENTRY_JOIN_BUTTON_LABEL: str = "参加"
ENTRY_LEAVE_BUTTON_LABEL: str = "キャンセル"
ENTRY_MESSAGE_COLOR: int = 0x9B59B6

GM_DASHBOARD_EMBED_TITLE: str = "GM ダッシュボード"
GM_DASHBOARD_EMBED_DESCRIPTION: str = (
    "各種アクションを選択し、結果を送信してください。\n"
    "占い・狩人・霊能・人狼のメッセージは対応する HO チャンネルに投稿されます。"
)

HO_CHANNEL_PREFIX: str = "ho"
HO_ROLE_PREFIX: str = "HO"
HO_CATEGORY_NAME: str = "HO"

VOTE_VIEW_CUSTOM_ID: str = "vote_selector"
VOTE_SUBMIT_BUTTON_ID: str = "vote_submit"

STORAGE_FILE_NAME: str = "game_state.json"
