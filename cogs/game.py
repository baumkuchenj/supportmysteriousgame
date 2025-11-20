# cogs/game.py
import discord
from discord import app_commands
from discord.ext import commands

from storage import Storage
from config import GM_ROLE_NAME, GM_CATEGORY_NAME, PRIVATE_CATEGORY_NAME, PLAYER_ROLE_NAME
from utils.helpers import ensure_gm_environment, ensure_player_role, has_gm_or_manage_guild


class GameCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="reset_game", description="ゲーム進行データを初期化")
    @app_commands.default_permissions(manage_guild=True)
    async def reset_game(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        guild = interaction.guild
        await Storage.ensure_loaded()
        # 先に静かにdefer（UIに通知を出さない）
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass

        # 1) 役職ロールの削除（GM除く、@everyone除く、Managed除く）
        for role in list(guild.roles):
            name = str(role.name)
            if name == GM_ROLE_NAME or name == "@everyone":
                continue
            # HO系 or playerロールなどゲーム用ロールを対象にする
            if name.startswith("HO") or name == PLAYER_ROLE_NAME or name == "霊界":
                if role.managed:
                    continue
                try:
                    await role.delete(reason="reset_game: cleanup game roles")
                except discord.Forbidden:
                    pass
                except Exception:
                    pass

        # 2) GM専用カテゴリ内のチャンネルを削除（カテゴリ自体は残す）
        gm_category = discord.utils.get(guild.categories, name=GM_CATEGORY_NAME)
        if gm_category is not None:
            for ch in list(gm_category.text_channels):
                try:
                    await ch.delete(reason="reset_game: cleanup GM専用 channels")
                except discord.Forbidden:
                    pass
                except Exception:
                    pass

        # 3) 個別チャンネルカテゴリを削除（配下のチャンネルも削除）
        private_category = discord.utils.get(guild.categories, name=PRIVATE_CATEGORY_NAME)
        if private_category is not None:
            for ch in list(private_category.text_channels):
                try:
                    await ch.delete(reason="reset_game: cleanup 個別チャンネル")
                except discord.Forbidden:
                    pass
                except Exception:
                    pass
            try:
                await private_category.delete(reason="reset_game: cleanup 個別チャンネル category")
            except discord.Forbidden:
                pass
            except Exception:
                pass

        # 4) ストレージを初期化
        Storage.reset_guild(guild.id)

        # 最後に必ずエフェメラルで完了通知
        try:
            await interaction.followup.send("ゲーム状態を初期化しました", ephemeral=True)
        except Exception:
            pass

    @app_commands.command(name="sync_commands", description="スラッシュコマンドを手動同期（既定: このギルドのみ/高速）")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(global_sync="Trueでグローバル同期（反映に時間がかかる）")
    async def sync_commands(self, interaction: discord.Interaction, global_sync: bool = False):
        # ギルド外では権限確認が難しいためギルド必須
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        guild = interaction.guild
        # 事前権限チェック
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
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
        # 同期実行
        try:
            if global_sync:
                synced = await self.bot.tree.sync()
                msg = f"🌍 グローバル同期完了: {len(synced)} 件（反映まで時間がかかる場合があります）"
            else:
                guild_obj = discord.Object(id=int(guild.id))
                synced = await self.bot.tree.sync(guild=guild_obj)
                msg = f"🧪 ギルド同期完了: {len(synced)} 件（このサーバーに即時反映）"
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            try:
                await interaction.followup.send(f"❌ 同期に失敗しました: {e}", ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="add_spirit", description="死亡者を霊界に移動（役職\"霊界\"付与＆霊界チャンネル作成/入室）")
    @app_commands.default_permissions(manage_guild=True)
    async def add_spirit(self, interaction: discord.Interaction, member: discord.Member):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        guild = interaction.guild
        await Storage.ensure_loaded()
        # 静かにdefer（UIに通知は出さない）
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        # playerロール保持者のみ対象
        player_role = await ensure_player_role(guild)
        if player_role and player_role not in member.roles:
            # 何も表示しない方針のため、単に終了（gm-logに残す）
            try:
                _, _, log = await ensure_gm_environment(guild)
                await log.send(f"[GM Action] {interaction.user.mention} 霊界付与失敗（対象がplayerロール未保持）: {member.display_name} ({member.id})")
            except Exception:
                pass
            return
        # 霊界ロールの用意
        spirit_role = discord.utils.get(guild.roles, name="霊界")
        if spirit_role is None:
            try:
                spirit_role = await guild.create_role(name="霊界", reason="Spirit role for afterlife chat")
            except discord.Forbidden:
                spirit_role = None
        # 付与
        if spirit_role is not None:
            try:
                if spirit_role not in member.roles:
                    await member.add_roles(spirit_role, reason="Move to spirit (afterlife)")
            except discord.Forbidden:
                pass
        # 霊界チャンネルの用意（個別チャンネルカテゴリ配下）
        gm_role = discord.utils.get(guild.roles, name=GM_ROLE_NAME)
        category = discord.utils.get(guild.categories, name=PRIVATE_CATEGORY_NAME)
        if category is None:
            try:
                category = await guild.create_category(PRIVATE_CATEGORY_NAME, reason="Create private HO category")
            except discord.Forbidden:
                category = None
        channel = discord.utils.get(guild.text_channels, name="霊界")
        if channel is None and category is not None and spirit_role is not None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                gm_role or guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
                spirit_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
            }
            try:
                channel = await guild.create_text_channel("霊界", category=category, overwrites=overwrites, reason="Create shared spirit channel")
            except discord.Forbidden:
                channel = None
        # ログ
        try:
            _, _, log = await ensure_gm_environment(guild)
            await log.send(f"[GM Action] {interaction.user.mention} 霊界付与: {member.display_name} ({member.id})")
        except Exception:
            pass

        # 個別チャンネル（HOチャンネル）へ文面を送信
        try:
            parts = Storage.get_participants(guild.id)
            ho = ""
            for p in parts:
                try:
                    if int(p.get("id", 0)) == int(member.id):
                        ho = str(p.get("ho") or "").upper()
                        break
                except Exception:
                    continue
            label_map = {
                "HO1": "味噌汁",
                "HO2": "マグロ",
                "HO3": "えび",
                "HO4": "茶碗蒸し",
                "HO5": "サーモン",
                "HO6": "つぶ貝",
                "HO7": "鯛",
                "HO8": "イクラ",
                "HO9": "ぶり",
                "HO10": "うどん",
                "HO11": "ハマチ",
                "HO12": "イカ",
                "HO13": "タコ",
                "HO14": "コハダ",
            }
            label = label_map.get(ho, "")
            if ho:
                ch = discord.utils.get(guild.text_channels, name=ho.lower())
                if ch is not None:
                    body = (
                        "【あなたは死にました】\n"
                        "あなたは死にましたが、処刑時の投票以外のすべての能力が使えます。昼の会議にも参加可能です。 \n霊界チャンネルが解放されました。 また、あなたは生前【" + label + "】であったことを思い出しました。"
                    )
                    try:
                        await ch.send(body)
                    except Exception:
                        try:
                            _, _, log = await ensure_gm_environment(guild)
                            await log.send(f"[WARN] HOチャンネルへの送信に失敗: {ho.lower()} → {member.display_name} ({member.id})")
                        except Exception:
                            pass
                else:
                    try:
                        _, _, log = await ensure_gm_environment(guild)
                        await log.send(f"[WARN] HOチャンネル未検出: {ho.lower()}（{member.display_name}）")
                    except Exception:
                        pass
            else:
                try:
                    _, _, log = await ensure_gm_environment(guild)
                    await log.send(f"[WARN] 対象メンバーのHO未登録: {member.display_name} ({member.id})")
                except Exception:
                    pass
        except Exception:
            pass
        # 最終確認（エフェメラル）
        try:
            await interaction.followup.send("✅ 対象を霊界に移動し、必要な通知を送信しました", ephemeral=True)
        except Exception:
            pass

    @app_commands.command(name="spirit_reverse_button", description="霊界に逆回転ボタンを表示（1回限り）")
    @app_commands.default_permissions(manage_guild=True)
    async def spirit_reverse_button(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        await Storage.ensure_loaded()
        gid = interaction.guild.id
        channel = interaction.channel
        # 霊界チャンネル限定
        if not isinstance(channel, discord.TextChannel) or channel.name != "霊界":
            await interaction.response.send_message("霊界チャンネルで実行してください", ephemeral=True)
            return
        used = Storage.is_spirit_reverse_used(gid)

        class ReverseButton(discord.ui.Button):
            def __init__(self, guild_id: int):
                label = "逆回転"
                super().__init__(label=label, style=discord.ButtonStyle.danger)
                self._gid = guild_id
                if Storage.is_spirit_reverse_used(guild_id):
                    self.disabled = True

            async def callback(self, interaction: discord.Interaction):
                # 二重実行の防止と応答の安定化
                if Storage.is_spirit_reverse_used(self._gid):
                    if not interaction.response.is_done():
                        await interaction.response.send_message("このボタンは既に使用されています", ephemeral=True)
                    else:
                        try:
                            await interaction.followup.send("このボタンは既に使用されています", ephemeral=True)
                        except Exception:
                            pass
                    return
                if not interaction.response.is_done():
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except Exception:
                        pass
                Storage.set_spirit_reverse_used(self._gid, True)
                # ボタンを無効化して編集
                v = discord.ui.View(timeout=None)
                b = ReverseButton(self._gid)
                b.disabled = True
                v.add_item(b)
                try:
                    await interaction.message.edit(view=v)
                except Exception:
                    pass
                # 霊界チャンネルへ告知
                try:
                    await interaction.channel.send(f"🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀\n ｷｭｲﾝｷｭｲﾝｷｭｲﾝ!! \n 逆回転、開始！！ \n 押したのは：{interaction.user.mention}  \n🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀")
                except Exception:
                    pass
                # ログ
                try:
                    _, _, log = await ensure_gm_environment(interaction.guild)
                    await log.send(f"[GM Action] {interaction.user.mention} 霊界で逆回転を実行")
                except Exception:
                    pass
                # 応答（エフェメラル）
                try:
                    await interaction.followup.send("✅ 実行しました", ephemeral=True)
                except Exception:
                    pass

        view = discord.ui.View(timeout=None)
        view.add_item(ReverseButton(gid))
        try:
            await channel.send("🌀 逆回転ボタン 🌀\nこのボタンを押すと、役職の流れる向きが反対になります。\n霊界から誰でも押せますが、ゲーム全体を通じて一度しか押せません。", view=view)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message("送信に失敗しました", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.send_message("✅ 逆回転ボタンを設置しました", ephemeral=True)

    @app_commands.command(name="end_game", description="ゲームを終了し、解説チャンネルを設定")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel_name="解説チャンネル名（既定: 解説）")
    async def end_game(self, interaction: discord.Interaction, channel_name: str = "解説"):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください", ephemeral=True)
            return
        if not has_gm_or_manage_guild(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません (GM または サーバーの管理が必要)", ephemeral=True)
            return
        guild = interaction.guild
        await Storage.ensure_loaded()
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass
        gm_role, gm_dash, gm_log = await ensure_gm_environment(guild)
        gm_category = gm_dash.category
        ch_name_lower = str(channel_name).lower()
        explanation_channel = discord.utils.get(guild.text_channels, name=ch_name_lower)
        player_role = await ensure_player_role(guild)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            gm_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
            player_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
        }
        if explanation_channel is None:
            try:
                explanation_channel = await guild.create_text_channel(channel_name, category=gm_category, overwrites=overwrites, reason="ゲーム終了時の解説チャンネル")
            except discord.Forbidden:
                try:
                    await interaction.followup.send("チャンネルの作成に失敗しました（権限不足）", ephemeral=True)
                except Exception:
                    pass
                return
            except Exception as e:
                try:
                    await interaction.followup.send(f"チャンネルの作成に失敗しました: {e}", ephemeral=True)
                except Exception:
                    pass
                return
            try:
                await explanation_channel.send("ゲーム終了。ここで振り返りや解説を行ってください。")
            except Exception:
                pass
        else:
            try:
                if explanation_channel.category_id != gm_category.id:
                    await explanation_channel.edit(category=gm_category)
                await explanation_channel.edit(overwrites=overwrites)
            except discord.Forbidden:
                # フォールバック: 新規に解説チャンネルを作成
                try:
                    new_ch = await guild.create_text_channel(channel_name, category=gm_category, overwrites=overwrites, reason="ゲーム終了時の解説チャンネル（既存編集不可のため新規作成）")
                    explanation_channel = new_ch
                except Exception as e2:
                    try:
                        await interaction.followup.send(f"既存チャンネルの設定変更に失敗し、新規作成も失敗しました: {e2}", ephemeral=True)
                    except Exception:
                        pass
                    return
            except Exception as e:
                # その他の失敗もフォールバックで新規作成を試みる
                try:
                    new_ch = await guild.create_text_channel(channel_name, category=gm_category, overwrites=overwrites, reason=f"ゲーム終了時の解説チャンネル（移動/編集失敗: {e}）")
                    explanation_channel = new_ch
                except Exception as e2:
                    try:
                        await interaction.followup.send(f"チャンネルの設定変更に失敗し、新規作成も失敗しました: {e2}", ephemeral=True)
                    except Exception:
                        pass
                    return
        try:
            await interaction.followup.send(f"ゲームを終了しました。解説チャンネル: {explanation_channel.mention}", ephemeral=True)
        except Exception:
            pass
        try:
            await gm_log.send(f"[GM Action] {interaction.user.mention} ゲーム終了 / 解説チャンネル: {explanation_channel.mention}")
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(GameCog(bot))
