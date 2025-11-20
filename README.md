# 不可思議の人狼 BOT 概要

このリポジトリは Discord 上で進行する「不可思議の人狼」用 GM 支援 BOT です。GM 用のダッシュボード、参加者管理、HO 個別チャンネル生成、役職送信/行動 UI、ゲーム終了処理などを提供します。

## 実行環境
- 言語: Python 3.13 系（Render ランタイム）
- ホスティング: Render Web Service（Free プラン想定）
  - buildCommand: `pip install -r requirements.txt`
  - startCommand: `python main.py`
  - healthCheckPath: `/healthz`
  - サービス URL 例: `https://supportmysteriousgame.onrender.com/`

## 主要ライブラリ（requirements.txt）
- discord.py
- aiohttp
- requests
- python-dotenv

## ストレージ（永続化）
`storage.py` の `Storage` クラスが抽象化。以下の 2 方式をサポートします。
- ファイル: `data.json`
- Upstash Redis（推奨・デプロイを跨いでも保持）
  - 必要な環境変数:
    - `STORAGE_BACKEND=upstash`
    - `UPSTASH_REDIS_REST_URL`
    - `UPSTASH_REDIS_REST_TOKEN`
    - `STORAGE_KEY=werewolf:data`（任意のキーで OK）

## Discord Bot 権限
- OAuth2 スコープ: `bot`, `applications.commands`
- 権限（最低限）:
  - Manage Channels（チャンネル作成/移動/編集）
  - Manage Roles（HO ロール作成/付与/剥奪）
  - View Channels / Send Messages / Read Message History
  - （任意）Embed Links / Attach Files
- ロール階層: Bot ロールは HO ロールや Player/GM ロールより上位に配置してください。

## コマンド（抜粋）
- `/entry` … GM ダッシュボードに参加者管理パネルを掲示
- `/close_entry` … 参加者募集を締切り、HO ロール割当と HO 個別チャンネルを作成
- `/rebuild_participants` … player ロールと HO ロールから参加者一覧を復元（HO 割当も復元）
- `/repost_role_ui` … 役職 UI を再掲（復旧用）
  - `phase=send | action`
- `/post_hint_buttons` … ヒントボタンをダッシュボードに掲示
- `/send_intro_messages` … HO 個別チャンネルに役職説明を送信（対象/文面を個別指定可）
- `/reset_game` … ゲーム進行データを初期化（参加者一覧を含めギルド単位で初期化）
- `/end_game` … ゲームを終了し、ゲーム進行カテゴリに「解説」チャンネルを用意（参加者が閲覧・送信可）
- `/sync_commands` … スラッシュコマンド同期（管理者/GM 向け）

いずれも GM またはサーバー管理者のみ実行可能です（`@app_commands.default_permissions(manage_guild=True)` 付与、実行時にもチェック）。

## 画面/UI の流れ（概要）
1. GM が `/entry` を実行し、ダッシュボードに管理パネルを掲示
2. パネルで参加者を追加 → `/close_entry` または 「参加者を締め切る」ボタンで HO 割当＆個別チャンネル作成
3. 「役職送信フェーズ」で GM が各 HO へ連絡（固定テンプレを選択）
4. 「翌日に進む」を押すと、次の日の「役職送信フェーズ」UI が掲示
5. 必要に応じて「役職行動フェーズ」UI（占い結果/霊能/狂人）からメッセージを送信
6. 終了時は `/end_game` で解説チャンネルを用意し、参加者に公開

## チャンネル/カテゴリ運用
- GM 専用カテゴリ（`GM_CATEGORY_NAME`）
  - ダッシュボード（`DASHBOARD_CHANNEL_NAME`）
  - ログ（`LOG_CHANNEL_NAME`）
- 個別チャンネルカテゴリ（`PRIVATE_CATEGORY_NAME`）
  - `ho1`, `ho2`, … 各参加者の個別チャンネル
- ゲーム進行カテゴリ（固定名: `ゲーム進行`）
  - 参加者が閲覧可能な `連絡` / `ヒント` / （終了時）`解説`

## よくある権限エラーの対処
- 403 Missing Access（送信失敗）
  - Bot に対象チャンネル閲覧/送信/履歴権限が不足
  - 対策: カテゴリ/チャンネルの Permission Overwrites で Bot（または Bot ロール）に明示許可
  - 実装側の補助: 新規作成時は Bot に権限付与、失敗時は権限付与→再送のフォールバックあり

## スリープ対策（Render Free）
- UptimeRobot などの外部監視から 5–10 分おきに `GET https://<service>.onrender.com/` を実行
- 軽量な `/healthz` を用意済み

## 環境変数（例）
- Discord
  - `DISCORD_TOKEN`, `APPLICATION_ID`
- Storage（Upstash）
  - `STORAGE_BACKEND=upstash`
  - `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, `STORAGE_KEY`
- その他
  - `DEBUG_MODE=false`

## デプロイ（render.yaml 抜粋）
```yaml
services:
  - type: web
    name: werewolf-bot
    env: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: python main.py
    healthCheckPath: /healthz
    autoDeploy: true
    envVars:
      - key: DISCORD_TOKEN
        sync: false
      - key: APPLICATION_ID
        sync: false
      - key: STORAGE_BACKEND
        value: upstash
      - key: UPSTASH_REDIS_REST_URL
        sync: false
      - key: UPSTASH_REDIS_REST_TOKEN
        sync: false
      - key: STORAGE_KEY
        value: werewolf:data
      - key: DEBUG_MODE
        value: "false"
```

## トラブルシュート
- 503 Service Unavailable
  - 起動直後/クラッシュ時の一時的応答。Render ログで Traceback を確認
- 参加者が消えた
  - `/reset_game` 実行時は消える仕様
  - デプロイ跨ぎには Upstash を使用（ファイルだと初期化される場合あり）
  - `/rebuild_participants` で player/HO ロールから復元可能

## ライセンス
- 本プロジェクトのライセンスはリポジトリの方針に従います（未定義の場合はクローズド運用を想定）。
