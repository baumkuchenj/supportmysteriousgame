# cogs/entry_manager.py
import discord
import asyncio
from discord import app_commands
from discord.ext import commands
from typing import List

from config import ENTRY_TITLE, ENTRY_DESCRIPTION, PRIVATE_CATEGORY_NAME, GM_ROLE_NAME
from storage import Storage
from utils.helpers import ensure_gm_environment, ensure_player_role, is_member_spirit, has_gm_or_manage_guild


def build_participants_embed(guild_id: int) -> discord.Embed:
    names = Storage.get_participant_names(guild_id)
    value = "\n".join(names) if names else "（まだ参加者はいません）"
    embed = discord.Embed(title=ENTRY_TITLE, description=ENTRY_DESCRIPTION, color=discord.Color.blurple())
    embed.add_field(name="メンバー", value=value, inline=False)
    return embed


async def _upsert_dashboard_panel(guild: discord.Guild) -> None:
    """Edit the existing dashboard panel message if possible, else send and remember it."""
    _, dash, _ = await ensure_gm_environment(guild)
    embed = build_participants_embed(guild.id)
    view = EntryManageView(guild)
    msg_id = Storage.get_dashboard_message(guild.id)
    if msg_id:
        try:
            msg = await dash.fetch_message(msg_id)
            await msg.edit(content="🧩 参加者管理パネル", embed=embed, view=view)
            return
        except discord.NotFound:
            pass
    msg = await dash.send("🧩 参加者管理パネル", embed=embed, view=view)
    Storage.set_dashboard_message(guild.id, msg.id)


async def _gm_log(guild: discord.Guild, content: str) -> None:
    """Send a GM-only log line to gm-log under GM専用."""
    _, _, log = await ensure_gm_environment(guild)
    await log.send(content)


async def _gm_log_interaction(interaction: discord.Interaction, content: str) -> None:
    user = interaction.user
    await _gm_log(interaction.guild, f"[GM Action] {user.mention} {content}")


class AddPlayerSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild):
        options: List[discord.SelectOption] = []
        seen = {int(m.id) for m in guild.members if m.bot}
        members = [m for m in guild.members if not m.bot]
        # show up to 25 selectable options
        for m in members[:25]:
            options.append(discord.SelectOption(label=m.display_name, value=str(m.id)))
        if not options:
            options = [discord.SelectOption(label="候補なし", value="none")]
        super().__init__(placeholder="追加するメンバーを選択", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            if not interaction.response.is_done():
                try:
                    await interaction.response.defer(ephemeral=True, thinking=False)
                except Exception:
                    pass
            return
        gid = interaction.guild.id
        val = self.values[0]
        if val == "none":
            if not interaction.response.is_done():
                try:
                    await interaction.response.defer(ephemeral=True, thinking=False)
                except Exception:
                    pass
            await _gm_log_interaction(interaction, "追加候補がありませんでした")
            return
        member = interaction.guild.get_member(int(val))
        if member is None:
            if not interaction.response.is_done():
                try:
                    await interaction.response.defer(ephemeral=True, thinking=False)
                except Exception:
                    pass
            await _gm_log_interaction(interaction, f"メンバーが見つかりません: {val}")
            return
        Storage.add_participant(gid, member)
        # 参加者ロール付与
        try:
            player_role = await ensure_player_role(interaction.guild)
            if player_role and player_role not in member.roles:
                await member.add_roles(player_role, reason="Add as werewolf participant")
        except discord.Forbidden:
            pass
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        await _gm_log_interaction(interaction, f"参加者追加: {member.display_name} ({member.id})")
        # Repost panel
        await _upsert_dashboard_panel(interaction.guild)


class RemovePlayerSelect(discord.ui.Select):
    def __init__(self, guild_id: int):
        parts = Storage.get_participants(guild_id)
        options = [discord.SelectOption(label=p["name"], value=str(p["id"])) for p in parts]
        if not options:
            options = [discord.SelectOption(label="候補なし", value="none")]
        super().__init__(placeholder="削除するメンバーを選択", min_values=1, max_values=1, options=options)
        self._guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        gid = self._guild_id
        val = self.values[0]
        if val == "none":
            if not interaction.response.is_done():
                try:
                    await interaction.response.defer(ephemeral=True, thinking=False)
                except Exception:
                    pass
            await _gm_log_interaction(interaction, "削除候補がありませんでした")
            return
        Storage.remove_participant(gid, int(val))
        # 参加者ロール剥奪
        if interaction.guild:
            member = interaction.guild.get_member(int(val))
            if member is not None:
                try:
                    player_role = await ensure_player_role(interaction.guild)
                    if player_role in member.roles:
                        await member.remove_roles(player_role, reason="Remove from werewolf participants")
                except discord.Forbidden:
                    pass
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        if interaction.guild and member is not None:
            await _gm_log_interaction(interaction, f"参加者削除: {member.display_name} ({member.id})")
        if interaction.guild:
            await _upsert_dashboard_panel(interaction.guild)


def _has_ho_assigned(guild_id: int) -> bool:
    for p in Storage.get_participants(guild_id):
        if p.get("ho"):
            return True
    return False


def _build_tally_text(guild_id: int) -> str:
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


async def _upsert_vote_tally(guild: discord.Guild) -> None:
    """Edit existing vote_night tally message or create it if missing."""
    _, gm_dash, _ = await ensure_gm_environment(guild)
    gm_category = gm_dash.category
    # 既存が別カテゴリにある場合は移動、なければ作成
    vote_channel = discord.utils.get(guild.text_channels, name="vote_night")
    if vote_channel is None:
        vote_channel = await guild.create_text_channel("vote_night", category=gm_category)
    elif gm_category and vote_channel.category_id != gm_category.id:
        try:
            await vote_channel.edit(category=gm_category)
        except discord.Forbidden:
            pass
    text = _build_tally_text(guild.id)
    msg_id = Storage.get_gm_vote_message(guild.id)
    try:
        if msg_id:
            msg = await vote_channel.fetch_message(msg_id)
            await msg.edit(content=text)
        else:
            msg = await vote_channel.send(text)
            Storage.set_gm_vote_message(guild.id, msg.id)
    except discord.NotFound:
        msg = await vote_channel.send(text)
        Storage.set_gm_vote_message(guild.id, msg.id)


class GMFlowButton(discord.ui.Button):
    def __init__(self, guild: discord.Guild):
        self._guild = guild
        label = self._compute_label()
        super().__init__(label=label, style=discord.ButtonStyle.primary)

    def _compute_label(self) -> str:
        gid = self._guild.id
        # 1) まだHO未割当なら 締め切り
        if not _has_ho_assigned(gid):
            return "参加者を締め切る"
        # 2) フェーズで分岐
        Storage.ensure_game(gid)
        phase = Storage.data["game"][str(gid)]["phase"]
        day = Storage.data["game"][str(gid)]["day"]
        if phase == "night":
            return "翌日に進む"
        # phase == day
        if day == 1:
            return "翌日に進む"
        return "夜に移行する"

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        gid = interaction.guild.id
        # 長処理や内部での返信の有無に関わらず、早期にdeferしておく
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass
        label = self._compute_label()
        if label == "参加者を締め切る":
            await _do_close_entry(interaction)
        elif label == "翌日に進む":
            await _do_next_day(interaction)
        elif label == "夜に移行する":
            await _do_night_phase(interaction)
        # 再掲（既存メッセージを編集 or 新規）
        await _upsert_dashboard_panel(interaction.guild)
        try:
            await interaction.followup.send("✅ 実行しました", ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message("✅ 実行しました", ephemeral=True)


class EntryManageView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        frozen = _has_ho_assigned(guild.id)
        add_select = AddPlayerSelect(guild)
        rem_select = RemovePlayerSelect(guild.id)
        if frozen:
            add_select.disabled = True
            add_select.placeholder = "参加者募集は締め切られています"
            rem_select.disabled = True
            rem_select.placeholder = "参加者募集は締め切られています"
        self.add_item(add_select)
        self.add_item(rem_select)
        self.add_item(GMFlowButton(guild))


class EntryManagerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="entry", description="GM用: 参加者管理パネルをgm-dashboardに表示")
    async def entry(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        await Storage.ensure_loaded()
        guild = interaction.guild
        # 参加者ロールも用意
        # 長処理になる可能性があるため、先にdeferして Unknown interaction を回避
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        await ensure_player_role(guild)
        await _upsert_dashboard_panel(guild)
        try:
            await interaction.followup.send("✅ 参加者管理パネルを配置しました。", ephemeral=True)
        except Exception:
            # 応答が未送信であれば直接送信を試行
            if not interaction.response.is_done():
                try:
                    await interaction.response.send_message("✅ 参加者管理パネルを配置しました。", ephemeral=True)
                except Exception:
                    pass
        await _gm_log_interaction(interaction, "参加者管理パネルを設置/更新")

    @app_commands.command(name="close_entry", description="参加者募集を締め切り、HO個別ロールとチャンネルを作成")
    async def close_entry(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        await Storage.ensure_loaded()
        await _do_close_entry(interaction)

    @commands.Cog.listener()
    async def on_ready(self):
        # 再起動時に保存済みパネルを復旧（編集）
        await Storage.ensure_loaded()
        for guild in self.bot.guilds:
            msg_id = Storage.get_dashboard_message(guild.id)
            if msg_id:
                try:
                    await _upsert_dashboard_panel(guild)
                except Exception:
                    pass
            # vote_night 集計メッセージも復旧（存在する場合）
            try:
                # 夜投票は行わないため、night_actions または既存メッセージIDがあれば復旧
                has_msg = bool(Storage.get_gm_vote_message(guild.id))
                has_actions = bool(Storage.get_night_actions(guild.id))
                if has_msg or has_actions:
                    await _upsert_vote_tally(guild)
            except Exception:
                pass
            # 永続コンポーネントのViewを再登録
            try:
                self.bot.add_view(_build_role_send_phase_view(guild.id))
                self.bot.add_view(_build_role_action_phase_view(guild.id))
                self.bot.add_view(_build_hint_buttons_view(guild.id))
            except Exception:
                pass

    @app_commands.command(name="sync_players", description="playerロール保持者から参加者リストを再構築")
    async def sync_players(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        await Storage.ensure_loaded()
        guild = interaction.guild
        role = await ensure_player_role(guild)
        # 既存のHOを維持するため、id→ho を控える
        existing = {int(p.get("id")): p.get("ho") for p in Storage.get_participants(guild.id)}
        members = [m for m in guild.members if role in m.roles and not m.bot]
        participants = []
        for m in members:
            participants.append({
                "id": int(m.id),
                "name": str(m.display_name),
                "ho": existing.get(int(m.id)),
            })
        Storage.set_participants(guild.id, participants)
        # パネル再掲
        _, dash, _ = await ensure_gm_environment(guild)
        await dash.send("🧩 参加者管理パネル", embed=build_participants_embed(guild.id), view=EntryManageView(guild))
        await interaction.response.send_message(f"🔄 playerロールから参加者を同期しました（{len(participants)}名）", ephemeral=True)

    @app_commands.command(name="repost_role_ui", description="役職UIを再掲（フェーズ変更なし・復旧用）")
    @app_commands.describe(phase="再掲するUIを選択: send=役職送信フェーズ / action=役職行動フェーズ")
    @app_commands.choices(
        phase=[
            app_commands.Choice(name="役職送信フェーズ", value="send"),
            app_commands.Choice(name="役職行動フェーズ", value="action"),
        ]
    )
    async def repost_role_ui(self, interaction: discord.Interaction, phase: app_commands.Choice[str]):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        await Storage.ensure_loaded()
        guild = interaction.guild
        # 先に静かにdefer
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        # 権限チェック: GMロール or Manage Guild
        gm_role = discord.utils.get(guild.roles, name=GM_ROLE_NAME)
        perms_ok = interaction.user.guild_permissions.manage_guild
        if gm_role and gm_role in getattr(interaction.user, 'roles', []):
            perms_ok = True
        if not perms_ok:
            try:
                await interaction.followup.send("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            except Exception:
                pass
            return
        # ダッシュボードに再掲
        _, dash, _ = await ensure_gm_environment(guild)
        try:
            if phase.value == "send":
                view = _build_role_send_phase_view(guild.id)
                content = (
                    "役職送信フェーズ: 役職/対象/送る内容を選んで送信してください\n"
                    "- この投稿は復旧のために再掲されています"
                )
            else:
                view = _build_role_action_phase_view(guild.id)
                content = (
                    "役職行動フェーズ: 役職/対象/送る内容を選んで送信してください\n"
                    "- 送信ボタンと翌日に進むボタンが利用可能です\n"
                    "- この投稿は復旧のために再掲されています"
                )
            await dash.send(content, view=view)
            try:
                await interaction.followup.send("🔁 役職UIを再掲しました", ephemeral=True)
            except Exception:
                pass
            await _gm_log_interaction(interaction, f"役職UI再掲 ({phase.value})")
        except Exception:
            try:
                await interaction.followup.send("❌ 再掲に失敗しました", ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="post_hint_buttons", description="ダッシュボードにヒントボタンを表示（ヒント1→ヒント/ 2-4→霊界）")
    async def post_hint_buttons(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        guild = interaction.guild
        await Storage.ensure_loaded()
        # ダッシュボードへ投稿
        _, dash, _ = await ensure_gm_environment(guild)
        try:
            await dash.send("🔎 ヒントボタン", view=_build_hint_buttons_view(guild.id))
            try:
                await interaction.followup.send("🧩 ヒントボタンを表示しました", ephemeral=True)
            except Exception:
                pass
            await _gm_log_interaction(interaction, "ヒントボタンをダッシュボードに掲示")
        except Exception:
            try:
                await interaction.followup.send("❌ ヒントボタンの表示に失敗しました", ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="send_intro_messages", description="HO個別チャンネルに役職説明を送信。任意で特定HOに上書き送信可")
    @app_commands.describe(target_ho="特定のHOにのみ送る（例: HO3）", text="そのHOに送るカスタム文面（未指定ならデフォルト文）")
    async def send_intro_messages(self, interaction: discord.Interaction, target_ho: str | None = None, text: str | None = None):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        guild = interaction.guild
        await Storage.ensure_loaded()
        parts = Storage.get_participants(guild.id)
        wolf_hos = {"HO1", "HO4", "HO10"}
        wolf_text = (
            "あなたは【寿司狼】です。\n"
            "この回転寿司屋の安っぽいレーンで流されている寿司たちに、かつては海を自由に泳いでいた魚としての誇りを思い出させるため、あなたは襲撃を行います。\n"
            "能力:毎晩一人を指名し、襲撃を行う\n"
            "寿司たち(村人達)は記憶を失っており、自分たちが寿司であることすら忘れています。\n"
        )
        sharer_hos5 = {"HO5"}
        sharer_text5 = (
            "あなたは【親子】です。\n"
            "8番とは親子関係だったことを記憶しており、お互いに村人陣営の味方であることを知っています。"
        )
        sharer_hos8 = {"HO8"}
        sharer_text8 = (
            "あなたは【親子】です。\n"
            "5番とは親子関係だったことを記憶しており、お互いに村人陣営の味方であることを知っています。"
        )

        other_text = (
            "あなたは何も思い出せない。\n"
        )

        # 対象HOの決定
        targets = []
        if target_ho:
            th = target_ho.upper()
            for p in parts:
                if str(p.get("ho") or "").upper() == th:
                    targets.append(p)
                    break
        else:
            targets = [p for p in parts if p.get("ho")]

        sent = []
        for p in targets:
            ho = str(p.get("ho") or "").upper()
            if not ho:
                continue
            member = guild.get_member(int(p.get("id", 0)))
            # 霊界は対象外
            if member and is_member_spirit(member):
                continue
            channel = discord.utils.get(guild.text_channels, name=ho.lower())
            if channel is None:
                continue
            if text and target_ho:
                body = text
            else:
                if ho in wolf_hos:
                    body = wolf_text
                elif ho in sharer_hos5:
                    body = sharer_text5
                elif ho in sharer_hos8:
                    body = sharer_text8
                else:
                    body = other_text
            try:
                await channel.send(body)
                sent.append(ho)
            except discord.Forbidden:
                pass
        if not interaction.response.is_done():
            await interaction.response.send_message(f"📨 送信済み: {', '.join(sorted(sent)) if sent else '(なし)'}", ephemeral=True)
        await _gm_log_interaction(interaction, f"役職説明を送信（対象: {', '.join(sorted(sent)) if sent else '(なし)'}）")


# ===== 内部アクション =====
async def _do_close_entry(interaction: discord.Interaction):
    guild = interaction.guild
    gm_role, dash, _ = await ensure_gm_environment(guild)
    # まず参加者割当を行い、0件なら即時返信
    participants = Storage.assign_ho_sequential(guild.id)
    if not participants:
        if not interaction.response.is_done():
            await interaction.response.send_message("参加者がいません。", ephemeral=True)
        else:
            await interaction.followup.send("参加者がいません。", ephemeral=True)
        return

    # 長処理に入るため、未応答なら先にdefer（表示は出さない）
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=True, thinking=False)
        except Exception:
            pass

    # 個別チャンネル用の専用カテゴリを使用/作成
    category = discord.utils.get(guild.categories, name=PRIVATE_CATEGORY_NAME)
    if category is None:
        category = await guild.create_category(PRIVATE_CATEGORY_NAME, reason="Create private HO category")

    created_channels = []
    for p in participants:
        uid = int(p["id"])
        ho = str(p.get("ho") or "").upper()
        if not ho:
            continue
        member = guild.get_member(uid)
        if member is None:
            continue
        # HOロール
        ho_role = discord.utils.get(guild.roles, name=ho)
        if ho_role is None:
            try:
                ho_role = await guild.create_role(name=ho, reason="HO private role")
            except discord.Forbidden:
                continue
        try:
            await member.add_roles(ho_role, reason="Assign HO private role")
        except discord.Forbidden:
            pass
        # チャンネル
        ch_name = ho.lower()
        # 既存が別カテゴリにある場合は移動、なければ作成
        channel = discord.utils.get(guild.text_channels, name=ch_name)
        if channel is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                gm_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
                ho_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
            }
            me = getattr(guild, "me", None)
            if me is not None:
                overwrites[me] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)
            try:
                channel = await guild.create_text_channel(ch_name, category=category, overwrites=overwrites, reason="Create HO private channel")
            except discord.Forbidden:
                continue
        elif channel.category_id != (category.id if category else None):
            try:
                await channel.edit(category=category)
            except discord.Forbidden:
                pass
        created_channels.append(channel.mention if channel else ho)

    summary = "、".join(created_channels) if created_channels else "(なし)"
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=True, thinking=False)
        except Exception:
            pass
    # ゲーム進行カテゴリと player 可視の2チャンネル（連絡/ヒント）を用意
    try:
        player_role = await ensure_player_role(guild)
    except Exception:
        player_role = None
    progress_cat = discord.utils.get(guild.categories, name="ゲーム進行")
    if progress_cat is None:
        try:
            progress_cat = await guild.create_category("ゲーム進行")
        except discord.Forbidden:
            progress_cat = None
    def _ensure_text_channel(name: str) -> None:
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch is None and progress_cat is not None and player_role is not None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                gm_role or guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
                player_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
            }
            try:
                return asyncio.create_task(guild.create_text_channel(name, category=progress_cat, overwrites=overwrites))
            except Exception:
                return None
        elif ch is not None and progress_cat is not None and ch.category_id != progress_cat.id:
            try:
                return asyncio.create_task(ch.edit(category=progress_cat))
            except Exception:
                return None
        return None
    _ensure_text_channel("連絡")
    _ensure_text_channel("ヒント")
    await _gm_log_interaction(interaction, f"参加者募集を締め切り。作成/準備したチャンネル: {summary}")


async def _do_next_day(interaction: discord.Interaction):
    Storage.ensure_game(interaction.guild.id)
    Storage.data["game"][str(interaction.guild.id)]["day"] += 1
    Storage.data["game"][str(interaction.guild.id)]["phase"] = "day"
    Storage.save()
    day = Storage.data["game"][str(interaction.guild.id)]["day"]
    await _gm_log_interaction(interaction, f"翌日に進行。現在 {day} 日目")
    # 翌日に進んだら、GMダッシュボードに役職送信フェーズUIを掲示（朝に配布する連絡を選べる）
    _, gm_dash, _ = await ensure_gm_environment(interaction.guild)
    new_msg = await gm_dash.send(
        "役職送信フェーズ: 役職/対象/送る内容を選んで送信してください",
        view=_build_role_send_phase_view(interaction.guild.id),
    )
    try:
        await _disable_old_role_message_ui(interaction.guild, keep_id=new_msg.id)
    except Exception:
        pass


async def _do_night_phase(interaction: discord.Interaction):
    guild = interaction.guild
    Storage.ensure_game(guild.id)
    Storage.data["game"][str(guild.id)]["phase"] = "night"
    # 旧夜UIは廃止。占い/狩人のアクション入力に切替
    # 既存の投票データは初期化し、night_actions もクリア
    parts = Storage.get_participants(guild.id)
    ho_list = [p.get("ho") for p in parts if p.get("ho")]
    # 夜投票は完全停止
    # Storage.init_votes(guild.id, ho_list)
    # Storage.set_voting_open(guild.id, True)
    Storage.clear_night_actions(guild.id)
    Storage.save()
    await _gm_log_interaction(interaction, "夜フェーズに移行（夜投票は行わない）")
    # GM tally message
    _, gm_dash, _ = await ensure_gm_environment(guild)
    gm_category = gm_dash.category
    # 既存が別カテゴリにある場合は移動、なければ作成
    vote_channel = discord.utils.get(guild.text_channels, name="vote_night")
    if vote_channel is None:
        vote_channel = await guild.create_text_channel("vote_night", category=gm_category)
    elif gm_category and vote_channel.category_id != gm_category.id:
        try:
            await vote_channel.edit(category=gm_category)
        except discord.Forbidden:
            pass
    # 夜アクション/投票の初期集計を掲示（以後はHO側UIの送信により更新）
    text = _build_tally_text(guild.id)
    msg = await vote_channel.send(text)
    Storage.set_gm_vote_message(guild.id, msg.id)
    # 夜開始時に役職送信フェーズUIをダッシュボードに掲示（過去UIは無効化）
    new_msg = await gm_dash.send("役職送信フェーズ: 役職/対象/送る内容を選んで送信してください", view=_build_role_send_phase_view(guild.id))
    try:
        await _disable_old_role_message_ui(guild, keep_id=new_msg.id)
    except Exception:
        pass


async def _do_close_vote(interaction: discord.Interaction):
    guild = interaction.guild
    Storage.set_voting_open(guild.id, False)
    # update GM tally with closed header
    _, gm_dash, _ = await ensure_gm_environment(guild)
    gm_category = gm_dash.category
    vote_channel = None
    if gm_category:
        vote_channel = discord.utils.get(gm_category.text_channels, name="vote_night")
    text = "🗳️ 夜の投票は締め切られました。集計結果:\n" + _build_tally_text(guild.id)
    if vote_channel is not None:
        msg_id = Storage.get_gm_vote_message(guild.id)
        try:
            if msg_id:
                msg = await vote_channel.fetch_message(msg_id)
                await msg.edit(content=text)
            else:
                msg = await vote_channel.send(text)
                Storage.set_gm_vote_message(guild.id, msg.id)
        except discord.NotFound:
            msg = await vote_channel.send(text)
            Storage.set_gm_vote_message(guild.id, msg.id)
    # 役職連絡用のUIは gm-dashboard に掲載（新規を最新とし、過去UIは一括無効化）
    # 夜投票は使わないため、役職行動フェーズUIを提示
    new_msg = await gm_dash.send("役職行動フェーズ: 役職/対象/送る内容を選んで送信してください\n- 送信ボタンと翌日に進むボタンが利用可能です", view=_build_role_action_phase_view(guild.id))
    try:
        await _disable_old_role_message_ui(guild, keep_id=new_msg.id)
    except Exception:
        pass
    await _gm_log_interaction(interaction, "夜の投票を締め切り。集計確定＆役職連絡UIを表示")


def _build_vote_view(guild: discord.Guild, voter_ho: str) -> discord.ui.View:
    # 夜投票は行わないため未使用
    return discord.ui.View(timeout=None)


def _build_hint_buttons_view(guild_id: int) -> discord.ui.View:
    class HintButtonsView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.add_item(self.Hint1())
            self.add_item(self.Hint2())
            self.add_item(self.Hint3())
            self.add_item(self.Hint4())

        async def _ensure_progress_channels(self, guild: discord.Guild) -> tuple[discord.TextChannel | None, discord.TextChannel | None]:
            from utils.helpers import ensure_player_role
            # カテゴリと「連絡」「ヒント」
            progress_cat = discord.utils.get(guild.categories, name="ゲーム進行")
            if progress_cat is None:
                try:
                    progress_cat = await guild.create_category("ゲーム進行")
                except discord.Forbidden:
                    progress_cat = None
            try:
                player_role = await ensure_player_role(guild)
            except Exception:
                player_role = None
            def _perm_overwrites():
                return {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    discord.utils.get(guild.roles, name=GM_ROLE_NAME) or guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
                    player_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True) if player_role else None,
                }
            def _cleanup_overwrites(ow: dict):
                return {k: v for k, v in ow.items() if k is not None}
            contact = discord.utils.get(guild.text_channels, name="連絡")
            hint = discord.utils.get(guild.text_channels, name="ヒント")
            if hint is None and progress_cat is not None:
                try:
                    hint = await guild.create_text_channel("ヒント", category=progress_cat, overwrites=_cleanup_overwrites(_perm_overwrites()))
                except Exception:
                    hint = None
            if contact is None and progress_cat is not None:
                try:
                    contact = await guild.create_text_channel("連絡", category=progress_cat, overwrites=_cleanup_overwrites(_perm_overwrites()))
                except Exception:
                    contact = None
            # カテゴリ不一致なら移動
            if progress_cat is not None:
                for ch in (hint, contact):
                    if ch is not None and ch.category_id != progress_cat.id:
                        try:
                            await ch.edit(category=progress_cat)
                        except Exception:
                            pass
            return contact, hint

        async def _ensure_spirit_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
            # 霊界チャンネル（個別カテゴリ配下）。霊界ロールに可視。
            category = discord.utils.get(guild.categories, name=PRIVATE_CATEGORY_NAME)
            if category is None:
                try:
                    category = await guild.create_category(PRIVATE_CATEGORY_NAME, reason="Create private HO category")
                except discord.Forbidden:
                    category = None
            spirit_role = discord.utils.get(guild.roles, name="霊界")
            if spirit_role is None:
                try:
                    spirit_role = await guild.create_role(name="霊界", reason="Spirit role for afterlife chat")
                except discord.Forbidden:
                    spirit_role = None
            channel = discord.utils.get(guild.text_channels, name="霊界")
            if channel is None and category is not None and spirit_role is not None:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    discord.utils.get(guild.roles, name=GM_ROLE_NAME) or guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
                    spirit_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
                }
                try:
                    channel = await guild.create_text_channel("霊界", category=category, overwrites=overwrites, reason="Create shared spirit channel")
                except discord.Forbidden:
                    channel = None
            return channel

        async def _send_hint(self, interaction: discord.Interaction, idx: int):
            if not interaction.guild:
                return
            guild = interaction.guild
            # 1はヒントへ、2-4は霊界へ
            target_channel: discord.TextChannel | None = None
            if idx == 1:
                _, hint_ch = await self._ensure_progress_channels(guild)
                target_channel = hint_ch
            else:
                target_channel = await self._ensure_spirit_channel(guild)
            if target_channel is not None:
                try:
                    texts = {
                        1: "①あなたたちは何も思い出せない\n どうやら、狼三匹と特殊な狂人いるようだ\n*特殊な狂人:この狂人がなんらかの(村側の)能力の対象となった場合、その能力者は翌朝死亡します。",
                        2: "②この村には親子が一組いるようだ\n毎朝見える景色が変わっている気がする",
                        3: "③あなたたちは魚だ。\nそしてこの村の狼は寿司狼である\n勝利条件\n村：寿司狼の全滅\n狼：寿司狼の人数が人間と同数以下になる",
                        4: "④ここは回転寿司屋のようだ。\n役職が回っている、但し寿司狼、親子は回らない",
                    }
                    body = texts.get(idx, f"[仮] ヒント{idx}の本文")
                    await target_channel.send(body)
                except Exception:
                    pass
            # エフェメラル応答
            if not interaction.response.is_done():
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
            try:
                await interaction.followup.send(f"✅ ヒント{idx}を送信しました", ephemeral=True)
            except Exception:
                pass
            try:
                await _gm_log_interaction(interaction, f"ヒント{idx}を送信")
            except Exception:
                pass

        class Hint1(discord.ui.Button):
            def __init__(self):
                super().__init__(label="ヒント①", style=discord.ButtonStyle.secondary, custom_id="hint_btn_1")
            async def callback(self, interaction: discord.Interaction):
                view: HintButtonsView = self.view
                await view._send_hint(interaction, 1)

        class Hint2(discord.ui.Button):
            def __init__(self):
                super().__init__(label="ヒント②", style=discord.ButtonStyle.secondary, custom_id="hint_btn_2")
            async def callback(self, interaction: discord.Interaction):
                view: HintButtonsView = self.view
                await view._send_hint(interaction, 2)

        class Hint3(discord.ui.Button):
            def __init__(self):
                super().__init__(label="ヒント③", style=discord.ButtonStyle.secondary, custom_id="hint_btn_3")
            async def callback(self, interaction: discord.Interaction):
                view: HintButtonsView = self.view
                await view._send_hint(interaction, 3)

        class Hint4(discord.ui.Button):
            def __init__(self):
                super().__init__(label="ヒント④", style=discord.ButtonStyle.secondary, custom_id="hint_btn_4")
            async def callback(self, interaction: discord.Interaction):
                view: HintButtonsView = self.view
                await view._send_hint(interaction, 4)

    return HintButtonsView()

def _build_role_send_phase_view(guild_id: int) -> discord.ui.View:
    roles = ["占い", "狩人"]
    parts = Storage.get_participants(guild_id)
    wolf_hos = {"HO1", "HO4", "HO10"}
    ho_options = []
    for p in parts:
        if not p.get("ho"):
            continue
        ho = str(p.get("ho"))
        name = str(p.get("name", ""))
        wolf_tag = "（人狼）" if ho in wolf_hos else ""
        label = f"{ho} {name}{wolf_tag}".strip()
        ho_options.append(discord.SelectOption(label=label, value=ho))
    if not ho_options:
        ho_options = [discord.SelectOption(label="対象なし", value="none")]

    class RoleSendPhaseView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.selected_dest_ho: str | None = None
            self.selected_role: str | None = None
            self.selected_target_ho: str | None = None
            self.role_select = self.RoleSelect(self)
            self.dest_select = self.DestinationSelect(self)
            self.send_button = self.SendButton(self)
            self.to_action_button = self.ToActionButton(self)
            self.add_item(self.dest_select)
            self.add_item(self.role_select)
            self.add_item(self.send_button)
            self.add_item(self.to_action_button)

        def _compute_text(self) -> str | None:
            role = self.selected_role
            if not role:
                return None
            if role == "占い":
                return "貴方は占い師です。\n今晩占いたい相手を一人指名してください。"
            if role == "狩人":
                return "貴方は狩人です。\n護衛したい人を一人指名してください。"
            return None

        def _summary_text(self) -> str:
            dest = self.selected_dest_ho or "未選択"
            role = self.selected_role or "未選択"
            target = self.selected_target_ho or "未選択"
            preview = "(役職/対象未選択)"
            text = self._compute_text()
            if text:
                preview = text
            dest_display = dest
            if isinstance(dest, str) and dest in {"HO1", "HO4", "HO10"}:
                dest_display = f"{dest}（人狼）"
            return (
                "役職送信フェーズ: 役職/対象を選んで送信してください\n"
                f"- 送信先HO: {dest_display}\n"
                f"- 役職: {role}\n"
                f"- 対象HO: {target}\n"
                f"- プレビュー:\n{preview}"
            )

        class RoleSelect(discord.ui.Select):
            def __init__(self, parent: 'RoleSendPhaseView'):
                super().__init__(placeholder="役職を選択", min_values=1, max_values=1,
                                 options=[discord.SelectOption(label=r, value=r) for r in roles],
                                 custom_id="rolemsg_role")

            async def callback(self, interaction: discord.Interaction):
                pv: 'RoleSendPhaseView' = self.view
                pv.selected_role = self.values[0]
                await interaction.response.edit_message(content=pv._summary_text(), view=pv)

        class DestinationSelect(discord.ui.Select):
            def __init__(self, parent: 'RoleSendPhaseView'):
                super().__init__(placeholder="送信先HOの個別チャンネルを選択", min_values=1, max_values=1, options=ho_options,
                                 custom_id="rolemsg_dest")

            async def callback(self, interaction: discord.Interaction):
                pv: 'RoleSendPhaseView' = self.view
                pv.selected_dest_ho = self.values[0]
                await interaction.response.edit_message(content=pv._summary_text(), view=pv)

        class SendButton(discord.ui.Button):
            def __init__(self, parent: 'RoleSendPhaseView'):
                super().__init__(label="送信", style=discord.ButtonStyle.success, custom_id="rolemsg_send")

            async def callback(self, interaction: discord.Interaction):
                pv: 'RoleSendPhaseView' = self.view
                role = pv.selected_role
                dest = pv.selected_dest_ho
                if not role or not dest or dest == "none":
                    if not interaction.response.is_done():
                        await interaction.response.edit_message(content=pv._summary_text(), view=pv)
                    return
                text = pv._compute_text()
                if not text:
                    if not interaction.response.is_done():
                        await interaction.response.edit_message(content=pv._summary_text(), view=pv)
                    return
                channel = discord.utils.get(interaction.guild.text_channels, name=str(dest).lower())
                if channel is None:
                    if not interaction.response.is_done():
                        await interaction.response.edit_message(content=pv._summary_text(), view=pv)
                    return
                view = _build_action_view(interaction.guild, role, str(dest))
                try:
                    await channel.send(text, view=view)
                except discord.Forbidden:
                    try:
                        me = getattr(interaction.guild, "me", None)
                        if me is not None:
                            await channel.set_permissions(me, view_channel=True, read_message_history=True, send_messages=True)
                            await channel.send(text, view=view)
                        else:
                            raise
                    except discord.Forbidden:
                        if not interaction.response.is_done():
                            try:
                                await interaction.response.defer(ephemeral=True)
                            except Exception:
                                pass
                        try:
                            await interaction.followup.send("❌ 送信先チャンネルにアクセスできません。Botの権限を確認してください。", ephemeral=True)
                        except Exception:
                            pass
                        await _gm_log_interaction(interaction, f"[WARN] 役職連絡送信失敗（権限不足）: {role} → {dest}")
                        return
                if not interaction.response.is_done():
                    try:
                        await interaction.response.defer(ephemeral=True)
                    except Exception:
                        pass
                try:
                    await interaction.followup.send("✅ 送信しました", ephemeral=True)
                except Exception:
                    pass
                await _gm_log_interaction(interaction, f"役職連絡送信: {role} → {dest}")

        class ToActionButton(discord.ui.Button):
            def __init__(self, parent: 'RoleSendPhaseView'):
                super().__init__(label="役職行動", style=discord.ButtonStyle.primary, custom_id="rolemsg_to_action")

            async def callback(self, interaction: discord.Interaction):
                v = _build_role_action_phase_view(guild_id)
                try:
                    await interaction.message.edit(content="役職行動フェーズ: 役職/対象を選んで送信してください\n- 送信ボタンと翌日に進むボタンが利用可能です", view=v)
                except Exception:
                    pass
                if not interaction.response.is_done():
                    try:
                        await interaction.response.defer(ephemeral=True)
                    except Exception:
                        pass
                try:
                    await interaction.followup.send("🔁 役職行動フェーズに切り替えました", ephemeral=True)
                except Exception:
                    pass
                await _gm_log_interaction(interaction, "役職行動フェーズへ切替")

    return RoleSendPhaseView()


def _build_role_action_phase_view(guild_id: int) -> discord.ui.View:
    roles = [
        "占い結果",
        "霊能",
        "狂人",
    ]
    parts = Storage.get_participants(guild_id)
    wolf_hos = {"HO1", "HO4", "HO10"}
    ho_options = []
    for p in parts:
        if not p.get("ho"):
            continue
        ho = str(p.get("ho"))
        name = str(p.get("name", ""))
        wolf_tag = "（人狼）" if ho in wolf_hos else ""
        label = f"{ho} {name}{wolf_tag}".strip()
        ho_options.append(discord.SelectOption(label=label, value=ho))
    if not ho_options:
        ho_options = [discord.SelectOption(label="対象なし", value="none")]

    class RoleActionPhaseView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.selected_dest_ho: str | None = None
            self.selected_role: str | None = None
            self.selected_target_ho: str | None = None
            self.role_select = self.RoleSelect(self)
            self.dest_select = self.DestinationSelect(self)
            self.template_select = self.TemplateSelect(self)
            self.send_button = self.SendButton(self)
            self.nextday_button = self.NextDayButton(self)
            self.add_item(self.dest_select)
            self.add_item(self.role_select)
            self.add_item(self.template_select)
            self.add_item(self.send_button)
            self.add_item(self.nextday_button)

        def _compute_texts(self) -> tuple[str, str] | None:
            role = self.selected_role
            ho = self.selected_target_ho
            if not role:
                return None
            name = None
            if ho and ho != "none":
                for p in Storage.get_participants(guild_id):
                    if p.get("ho") == ho:
                        name = p.get("name")
                        break
            disp = f"{ho}（{name}）" if (ho and name) else (ho or "")
            if role == "占い結果":
                return (f"指名した相手は狼です。", f"指名した相手は狼ではないようだ。")
            if role == "霊能":
                return (f"貴方は霊能者です。吊られた人は狼です。", f"貴方は霊能者です。吊られた人は狼ではないようだ。")
            if role == "狂人":
                return (
                    f"あなたの思考は何者かに乗っ取られてしまいました。あなたは今日、なんだか無性に寿司狼の味方をしなければならない気がしている。\nあなたは狼陣営です。\n今夜あなたがなんらかの能力の対象となった場合、その能力者は翌朝死亡します。",
                    f"あなたは正気を取り戻しました。\n以降あなたは村人陣営の味方であり、なんらかの能力の対象となっても、その能力者は死亡しません。",
                )
            return (f"{disp} へ連絡", f"{disp} へ連絡（別案）")

        async def _refresh_template_options(self, interaction: discord.Interaction):
            texts = self._compute_texts()
            if not texts:
                self.template_select.options = [
                    discord.SelectOption(label="役職と対象を先に選択してください", value="none")
                ]
            else:
                a, b = texts
                def _shorten(s: str) -> str:
                    base = s or ""
                    if len(base) > 90:
                        base = base[:90] + "…"
                    return base or "(内容なし)"

                self.template_select.options = [
                    discord.SelectOption(label=_shorten(a), value="A"),
                    discord.SelectOption(label=_shorten(b), value="B"),
                ]
            await interaction.response.edit_message(content=self._summary_text(), view=self)

        def _summary_text(self) -> str:
            dest = self.selected_dest_ho or "未選択"
            role = self.selected_role or "未選択"
            target = self.selected_target_ho or "未選択"
            choice_value = self.template_select.values[0] if self.template_select.values else None
            choice = "未選択"
            if choice_value:
                for opt in self.template_select.options:
                    if opt.value == choice_value:
                        choice = opt.label or choice_value
                        break
            preview = "(役職/対象未選択)"
            texts = self._compute_texts()
            if texts:
                a, b = texts
                preview = f"{a}\n---\n{b}"
            dest_display = dest
            if isinstance(dest, str) and dest in {"HO1", "HO4", "HO10"}:
                dest_display = f"{dest}（人狼）"
            return (
                "役職行動フェーズ: 役職/対象/送る内容を選んで送信してください\n"
                f"- 送信先HO: {dest_display}\n"
                f"- 役職: {role}\n"
                f"- 対象HO: {target}\n"
                f"- 選択: {choice}\n"
                f"- プレビュー:\n{preview}"
            )

        class RoleSelect(discord.ui.Select):
            def __init__(self, parent: 'RoleActionPhaseView'):
                super().__init__(placeholder="役職を選択", min_values=1, max_values=1,
                                 options=[discord.SelectOption(label=r, value=r) for r in roles],
                                 custom_id="rolemsg_role")

            async def callback(self, interaction: discord.Interaction):
                pv: 'RoleActionPhaseView' = self.view
                pv.selected_role = self.values[0]
                await pv._refresh_template_options(interaction)

        class DestinationSelect(discord.ui.Select):
            def __init__(self, parent: 'RoleActionPhaseView'):
                super().__init__(placeholder="送信先HOの個別チャンネルを選択", min_values=1, max_values=1, options=ho_options,
                                 custom_id="rolemsg_dest")

            async def callback(self, interaction: discord.Interaction):
                pv: 'RoleActionPhaseView' = self.view
                pv.selected_dest_ho = self.values[0]
                await interaction.response.edit_message(content=pv._summary_text(), view=pv)

        class TemplateSelect(discord.ui.Select):
            def __init__(self, parent: 'RoleActionPhaseView'):
                super().__init__(placeholder="送る内容を選択", min_values=1, max_values=1,
                                 options=[discord.SelectOption(label="役職と対象を先に選択してください", value="none")],
                                 custom_id="rolemsg_tmpl")

            async def callback(self, interaction: discord.Interaction):
                pv: 'RoleActionPhaseView' = self.view
                await interaction.response.edit_message(content=pv._summary_text(), view=pv)

        class SendButton(discord.ui.Button):
            def __init__(self, parent: 'RoleActionPhaseView'):
                super().__init__(label="送信", style=discord.ButtonStyle.success, custom_id="rolemsg_send")

            async def callback(self, interaction: discord.Interaction):
                pv: 'RoleActionPhaseView' = self.view
                role = pv.selected_role
                dest = pv.selected_dest_ho
                if not role or not dest or dest == "none" or not pv.template_select.values:
                    if not interaction.response.is_done():
                        await interaction.response.edit_message(content=pv._summary_text(), view=pv)
                    return
                choice_value = pv.template_select.values[0]
                texts = pv._compute_texts()
                if not texts:
                    if not interaction.response.is_done():
                        await interaction.response.edit_message(content=pv._summary_text(), view=pv)
                    return
                text = texts[0] if choice_value == "A" else texts[1]
                channel = discord.utils.get(interaction.guild.text_channels, name=str(dest).lower())
                if channel is None:
                    if not interaction.response.is_done():
                        await interaction.response.edit_message(content=pv._summary_text(), view=pv)
                    return
                await channel.send(text)
                if not interaction.response.is_done():
                    try:
                        await interaction.response.defer(ephemeral=True)
                    except Exception:
                        pass
                try:
                    await interaction.followup.send("✅ 送信しました", ephemeral=True)
                except Exception:
                    pass
                choice_label = choice_value
                for opt in pv.template_select.options:
                    if opt.value == choice_value:
                        choice_label = opt.label or choice_value
                        break
                await _gm_log_interaction(interaction, f"役職連絡送信: {role} → {dest} （選択: {choice_label}）")

        class NextDayButton(discord.ui.Button):
            def __init__(self, parent: 'RoleActionPhaseView'):
                super().__init__(label="翌日に進む", style=discord.ButtonStyle.primary, custom_id="rolemsg_next")

            async def callback(self, interaction: discord.Interaction):
                await _do_next_day(interaction)
                await _upsert_dashboard_panel(interaction.guild)
                if not interaction.response.is_done():
                    try:
                        await interaction.response.defer(ephemeral=True)
                    except Exception:
                        pass
                try:
                    await interaction.followup.send("⏭️ 翌日に進みました", ephemeral=True)
                except Exception:
                    pass
                await _gm_log_interaction(interaction, "翌日に進む（役職行動フェーズ）")

    return RoleActionPhaseView()

# Backward compatibility for modules importing the old builder
_build_role_message_view = _build_role_send_phase_view



async def _disable_old_role_message_ui(guild: discord.Guild, keep_id: int) -> None:
    """Disable components on older RoleMessage UI messages in gm-dashboard, keeping only the latest active.
    Messages are identified by content prefix "役職連絡:". Components are removed by editing view=None.
    """
    _, gm_dash, _ = await ensure_gm_environment(guild)
    async for msg in gm_dash.history(limit=100):
        if int(msg.id) == int(keep_id):
            continue
        # remove components for old role message UIs
        try:
            text = msg.content or ""
        except Exception:
            text = ""
        if isinstance(text, str) and (text.startswith("役職送信フェーズ:") or text.startswith("役職行動フェーズ:") or text.startswith("役職連絡:")):
            if getattr(msg, "components", None):
                try:
                    await msg.edit(content=msg.content, view=None)
                except Exception:
                    pass


def _build_action_view(guild: discord.Guild, role: str, voter_ho: str) -> discord.ui.View:
    guild_id = guild.id
    parts = Storage.get_participants(guild_id)
    options = []
    for p in parts:
        ho = str(p.get("ho") or "")
        if not ho or ho == voter_ho:
            continue
        # 霊界は対象外
        member = guild.get_member(int(p.get("id", 0)))
        if member and is_member_spirit(member):
            continue
        label = f"{ho} {p.get('name','')}"
        options.append(discord.SelectOption(label=label, value=str(ho)))
    if not options:
        options = [discord.SelectOption(label="候補なし", value="none")]

    class _Select(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="対象を選択", min_values=1, max_values=1, options=options)
            self._selected = None

        async def callback(self, interaction: discord.Interaction):
            # 既に同一HOからの選択が確定している場合は拒否
            try:
                existing = Storage.get_night_actions(guild_id).get(role, {}).get(voter_ho)
            except Exception:
                existing = None
            if existing:
                await interaction.response.send_message("この役職の選択は既に確定しています", ephemeral=True)
                return
            self._selected = self.values[0]
            await interaction.response.send_message("✅ 選択を一時保存しました。送信で確定します。", ephemeral=True)

    class _Submit(discord.ui.Button):
        def __init__(self, select: _Select):
            super().__init__(label="送信", style=discord.ButtonStyle.primary)
            self._select = select

        async def callback(self, interaction: discord.Interaction):
            # 二重送信防止（既に記録があればブロック）
            try:
                existing = Storage.get_night_actions(guild_id).get(role, {}).get(voter_ho)
            except Exception:
                existing = None
            if existing:
                await interaction.response.send_message("この役職の選択は既に確定しています", ephemeral=True)
                # 可能ならビューを無効化
                try:
                    v = self.view
                    if v:
                        for child in v.children:
                            child.disabled = True
                        await interaction.message.edit(view=v)
                except Exception:
                    pass
                return
            target = self._select._selected
            if not target or target == "none":
                await interaction.response.send_message("対象を選択してください", ephemeral=True)
                return
            Storage.set_night_action(guild_id, role, voter_ho, target)
            # Update GM tally (reuse existing channel/message if any)
            from utils.helpers import ensure_gm_environment as _egm
            gm_role, gm_dash, _ = await _egm(interaction.guild)
            gm_category = gm_dash.category
            vote_channel = None
            if gm_category:
                vote_channel = discord.utils.get(gm_category.text_channels, name="vote_night")
            if vote_channel is None:
                vote_channel = await interaction.guild.create_text_channel("vote_night", category=gm_category)
            text = _build_tally_text(guild_id)
            try:
                msg_id = Storage.get_gm_vote_message(interaction.guild.id)
                if msg_id:
                    msg = await vote_channel.fetch_message(msg_id)
                    await msg.edit(content=text)
                else:
                    msg = await vote_channel.send(text)
                    Storage.set_gm_vote_message(interaction.guild.id, msg.id)
            except discord.NotFound:
                msg = await vote_channel.send(text)
                Storage.set_gm_vote_message(interaction.guild.id, msg.id)
            # 送信後、このビューは無効化して再選択を防止
            try:
                v = self.view
                if v:
                    for child in v.children:
                        child.disabled = True
                    await interaction.message.edit(view=v)
            except Exception:
                pass
            await interaction.response.send_message("📨 送信しました", ephemeral=True)

    view = discord.ui.View(timeout=None)
    select = _Select()
    view.add_item(select)
    view.add_item(_Submit(select))
    return view

    class TargetSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="対象HOを選択", min_values=1, max_values=1, options=ho_options)
            self._value = None

        async def callback(self, interaction: discord.Interaction):
            self._value = self.values[0]
            await interaction.response.send_message("対象を選択しました", ephemeral=True)

    class TemplateSelect(discord.ui.Select):
        def __init__(self):
            opts = [
                discord.SelectOption(label="テンプレA", value="A"),
                discord.SelectOption(label="テンプレB", value="B"),
            ]
            super().__init__(placeholder="テンプレを選択", min_values=1, max_values=1, options=opts)
            self._value = None

        async def callback(self, interaction: discord.Interaction):
            self._value = self.values[0]
            await interaction.response.send_message("テンプレを選択しました", ephemeral=True)

    def render_message(role: str, ho: str) -> str:
        # HO→名前辞書
        name = None
        for p in Storage.get_participants(guild_id):
            if p.get("ho") == ho:
                name = p.get("name")
                break
        disp = f"{ho}（{name}）" if name else ho
        # 役職ごとの2択テンプレ（仮）
        templates = {
            "占い": {
                "A": f"投票した人（{disp}）は「村人」です。",
                "B": f"投票した人（{disp}）は「狼」です。",
            },
            "霊能": {
                "A": f"{disp} の霊能結果は『白』でした。",
                "B": f"{disp} の霊能結果は『黒』でした。",
            },
            "狩人": {
                "A": f"今夜は {disp} を護衛します。",
                "B": f"今夜は {disp} を護衛しません。",
            },
            "狂人": {
                "A": f"{disp} へ作戦連絡: 村に溶け込め。",
                "B": f"{disp} へ作戦連絡: 狼を支援せよ。",
            },
            "人狼": {
                "A": f"{disp} へ連絡: 今夜は潜伏。",
                "B": f"{disp} へ連絡: 今夜は積極的に動け。",
            },
        }
        # デフォルト
        return templates.get(role, {}).get("A", f"{disp} へ連絡")

    class Submit(discord.ui.Button):
        def __init__(self, role_select: RoleSelect, target_select: TargetSelect, tmpl_select: TemplateSelect):
            super().__init__(label="送信", style=discord.ButtonStyle.success)
            self._r = role_select
            self._t = target_select
            self._x = tmpl_select

        async def callback(self, interaction: discord.Interaction):
            role = getattr(self._r, "_value", None)
            ho = getattr(self._t, "_value", None)
            tmpl = getattr(self._x, "_value", None)
            if not role or not ho or ho == "none" or not tmpl:
                await interaction.response.send_message("役職/対象/テンプレを選択してください", ephemeral=True)
                return
            # メッセージ作成
            textA = render_message(role, ho)
            textB = textA  # 簡易: 上でA/B両方を用意済み
            # 本当にA/B分ける
            name = None
            for p in Storage.get_participants(guild_id):
                if p.get("ho") == ho:
                    name = p.get("name")
                    break
            disp = f"{ho}（{name}）" if name else ho
            if role == "占い":
                textA = f"天啓：「村人」です。"
                textB = f"天啓：「人狼」です。"
            elif role == "霊能":
                textA = f"天啓：「村人」です。"
                textB = f"天啓：「人狼」です。"
            elif role == "狂人":
                textA = f"天啓：あなたは今日、なんだか無性に寿司狼の味方をしなければならない気がしている。\nあなたは狼陣営です。"
                textB = f"天啓：あなたは正気を取り戻しました。\n以降あなたは村人陣営の味方です。"
            final = textA if tmpl == "A" else textB
            # 送信先は対象HOの個別チャンネル
            channel = discord.utils.get(interaction.guild.text_channels, name=ho.lower())
            if channel is None:
                await interaction.response.send_message("対象チャンネルが見つかりません", ephemeral=True)
                return
            await channel.send(final)
            await interaction.response.send_message("📩 送信しました", ephemeral=True)

    view = discord.ui.View(timeout=None)
    rs = RoleSelect()
    ts = TargetSelect()
    xs = TemplateSelect()
    view.add_item(rs)
    view.add_item(ts)
    view.add_item(xs)
    view.add_item(Submit(rs, ts, xs))
    return view


async def setup(bot: commands.Bot):
    await bot.add_cog(EntryManagerCog(bot))
