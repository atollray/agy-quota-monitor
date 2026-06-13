# 要件定義書：Antigravity クォータ監視タスクバー常駐アプリ

> **バージョン**: 1.0  
> **作成日**: 2026-06-14  
> **ステータス**: レビュー待ち

---

## 1. 目的・背景

Antigravity CLI（`agy`）の5時間ローリング制限（Sprint）および週間制限（Marathon）は、AIエージェントの利用頻度をコントロールする上で重要な指標である。しかし、現在の残量を確認するには、都度 `agy` のインタラクティブセッション内で `/usage` コマンドを実行する必要があり、作業中の視認性が低い。

本システムは、Windowsのタスクバー（システムトレイ）に現在のクォータ残量を常時、かつ動的に表示することで、ユーザーがリソース制限を意識せずに快適に開発・作業に集中できる環境を提供することを目的とする。

---

## 2. システム概要

本システムは、Windowsのバックグラウンドで常駐するPythonベースの軽量デスクトップアプリケーションである。Antigravityのローカル Language Server API（`GetUserStatus` エンドポイント）を定期的にポーリングし、構造化されたJSON形式のクォータ情報を取得・パースして、タスクバーの通知領域（システムトレイ）に情報を反映する。

### 2.1. アーキテクチャ概要

```
┌───────────────────────────────────────────┐
│  Antigravity Language Server              │
│  (language_server_windows_x64.exe)        │
│  https://127.0.0.1:<dynamic_port>         │
│                                           │
│  Endpoint: GetUserStatus                  │
│  Protocol: Connect RPC over HTTPS         │
│  Auth: X-Codeium-Csrf-Token header        │
│  Response: JSON (remainingFraction, etc.) │
└────────────────────┬──────────────────────┘
                     │ HTTPS POST (self-signed cert)
                     ▼
┌───────────────────────────────────────────┐
│  Quota Checker (本アプリ)                 │
│                                           │
│  1. ProcessFinder: プロセス検出            │
│     └ language_server プロセスの           │
│       コマンドラインから port, csrf_token  │
│       を抽出                              │
│  2. QuotaFetcher: API呼び出し             │
│     └ GetUserStatus → JSON parse          │
│  3. TrayManager: システムトレイ表示        │
│     └ pystray + Pillow で動的アイコン生成  │
└───────────────────────────────────────────┘
```

> **下書きからの重要な変更点**: 下書きでは `agy` CLIを `subprocess` 等で起動し `/usage` の出力テキストをスクレイピングする方式を想定していたが、以下の理由によりローカルAPI方式を採用する。
> 
> 1. `/usage` はインタラクティブセッション内のスラッシュコマンドであり、`agy -p "/usage"` のような非対話的呼び出しでは実行できない
> 2. `agy` の起動はインタラクティブTUIを伴い、`subprocess` での制御が困難
> 3. ローカル Language Server API は、既存の Antigravity IDE拡張（`ag-quota` 等）でも採用されている正規のデータ取得方法であり、構造化JSONを直接取得可能
> 4. テキストスクレイピングと異なりフォーマット変更に強い

---

## 3. 機能要件

### 3.1. プロセス検出機能（ProcessFinder）

Antigravity Language Server プロセスを自動検出し、接続に必要な情報を抽出する。SDK、デスクトップアプリ2、およびIDEのすべてで動作するよう、以下の仕様を実装する。

* **複数プロセス名への対応 (プロセス探索)**:
  * **IDE (VS Code拡張版)**: `language_server_windows_x64.exe` (または `_arm64.exe`)
  * **デスクトップアプリ / SDK**: `language_server.exe`
  * 上記のいずれのプロセス名でも PowerShell（`Get-CimInstance Win32_Process`）で探索可能とする。
* **Antigravityプロセスの識別**:
  * 複数の `language_server` プロセスが存在する場合（Codeium等と共存時）、コマンドライン引数（CommandLine）に `--app_data_dir` として `antigravity` または `antigravity-ide` が含まれていること、あるいはパスに `\antigravity` または `\Antigravity IDE` が含まれているプロセスを選別する。
* **接続情報の抽出**:
  * **CSRFトークン**: コマンドライン引数から `--csrf_token <TOKEN>` を正規表現で抽出する。
  * **ポート番号**: 
    * コマンドライン引数に `--extension_server_port <PORT>` が存在する場合は、その値を優先使用する。
    * コマンドライン引数にポート番号がない場合（デスクトップアプリで `--https_server_port 0` 等が指定されランダムポートが割り当てられている場合）、抽出したPIDがリスニング中のTCPポートをすべて走査する（後述）。
* **リスニングポートの特定（疎通確認）**:
  * 検出したPIDに対して `Get-NetTCPConnection -OwningProcess <PID> -State Listen`（または `netstat -ano`）を実行し、リスニング中のすべてのローカルポートを列挙する。
  * 列挙されたポートに対し、`GetUnleashData` エンドポイント（`https://127.0.0.1:<PORT>/exa.language_server_pb.LanguageServerService/GetUnleashData`）へのHTTPS POSTリクエストを送信する（ヘッダに `X-Codeium-Csrf-Token: <TOKEN>` を付与）。
  * ステータスコード `200 OK` かつ正常なJSONが返却されたポートを「稼働中のAPIポート」として特定し、クォータ取得に使用する。
* **複数インスタンス（IDEとデスクトップアプリ等）の同時起動対応**:
  * 複数の有効な Antigravity プロセスが同時に見つかった場合、以下のポリシーで接続先を選択する。
    1. 前回の接続に成功していたプロセスがまだ動いていればそれを維持する。
    2. 新規検出時は、最初に応答があったプロセスに自動接続する。
    3. 右クリックメニューから、現在接続可能なインスタンス（プロセスIDや種別：IDE/SDKなど）を手動で切り替えられるようにする。
* **フォールバック**: PowerShell が使用できない環境では、`wmic` + `netstat` を用いた代替コマンドで検出を試みる。

### 3.2. データ取得・パース機能（QuotaFetcher）

ローカル Language Server API を呼び出し、クォータ情報を構造化データとして取得する。

* **APIエンドポイント**: `POST https://127.0.0.1:<port>/exa.language_server_pb.LanguageServerService/GetUserStatus`
* **リクエストヘッダ**:
  * `Content-Type: application/json`
  * `Connect-Protocol-Version: 1`
  * `X-Codeium-Csrf-Token: <token>`
* **リクエストボディ**:
  ```json
  {
    "metadata": {
      "ideName": "antigravity",
      "extensionName": "antigravity",
      "locale": "en"
    }
  }
  ```
* **SSL検証**: Language Server は自己署名証明書を使用するため、SSL検証を無効化（`verify=False`）する。
* **レスポンスのパース**: JSON レスポンスから以下のデータを抽出する。

| フィールドパス | 説明 | 型 |
|---|---|---|
| `userStatus.cascadeModelConfigData.clientModelConfigs[].label` | モデルの表示名（例: `"Gemini 2.5 Pro"`, `"Claude Sonnet 4"`） | `string` |
| `userStatus.cascadeModelConfigData.clientModelConfigs[].quotaInfo.remainingFraction` | クォータ残量（0.0〜1.0） | `float` |
| `userStatus.cascadeModelConfigData.clientModelConfigs[].quotaInfo.resetTime` | リセット時刻（ISO 8601） | `string` |
| `userStatus.planStatus.planInfo.monthlyPromptCredits` | 月間プロンプトクレジット上限 | `number` |
| `userStatus.planStatus.availablePromptCredits` | 利用可能プロンプトクレジット残量 | `number` |

> **クォータ構造の補足**: APIレスポンスには「5時間」「週間」といった時間区分のフィールドは明示されない。各モデル設定に付随する `remainingFraction` と `resetTime` の値から、リセットまでの残り時間を算出して区分を推定するか、モデルラベルをそのまま表示する方式とする。

### 3.3. タスクバー（システムトレイ）表示機能

* **動的アイコン生成**: 最も重要視されるモデル（デフォルト: 利用可能なモデルのうち最も残量が少ないもの、またはユーザー指定のモデル）の残量パーセンテージ（`remainingFraction × 100`）を、`Pillow` を用いてリアルタイムに画像化し、システムトレイに常駐させる。
  * フォント: 視認性の高い太字フォント（例: `Segoe UI Bold`、`Consolas Bold`）
  * サイズ: 16×16px または 32×32px（DPIスケーリング対応）
* **色分け表示**:

| 残量 | アイコン色 | 意味 |
|---|---|---|
| 50%超 | 白（または緑） | 正常 |
| 20%〜50% | 黄色 | 注意 |
| 20%未満 | 赤色 | 残量低下警告 |
| 0%（枯渇） | 赤色 + 点滅 or 取り消し線 | 使用不可 |
| エラー時 | `!!` or `?` アイコン | 接続エラー |

* **ツールチップ（ホバー表示）**: ユーザーがタスクバーのアイコンにマウスカーソルを合わせた際、全モデルの詳細なステータスをポップアップ表示する。

```text
【Antigravity Quota】
Gemini 2.5 Pro: 67% (Resets in 4h 8m)
Claude Sonnet 4: 80% (Resets in 4h 48m)
Claude Opus 4: 91% (Resets in 157h 8m)
───
Credits: 8,420 / 10,000 (84%)
Last updated: 00:15:30
```

> **ツールチップ文字数制限**: Windows APIにはツールチップの最大文字数制限（最大128文字前後）があるため、モデル数が多い場合は主要モデルのみに絞り、残りはコンテキストメニューから確認できる設計とする。ツールチップ文字数制限を超える場合の挙動（切り詰め or 省略）は実装時に調整する。

### 3.4. コンテキストメニュー（右クリックメニュー）機能

アイコンを右クリックした際に、以下のメニューを表示・実行できること。

* **全モデル詳細表示 (Show Details)**: 全モデルのクォータ残量・リセット時刻を一覧表示するサブメニューまたはダイアログ。
* **今すぐ更新 (Refresh Now)**: 定期実行のタイミングを待たずに、即座にAPIを呼び出してデータを更新する。
* **表示対象の切り替え (Display Model)**: タスクバーの数値アイコンに表示するモデルを選択するサブメニュー。
  * 「最低残量モデルを自動選択」（デフォルト）
  * 個別モデル名（例: `Gemini 2.5 Pro`, `Claude Sonnet 4` 等、動的に生成）
* **接続先インスタンスの切り替え (Select Instance)**: 複数の Antigravity プロセスが同時稼働している場合、接続先（例: 「IDE - PID 40028」や「SDK/App - PID 44772」）を選択・切り替えるサブメニュー（検出されたプロセスが1つの場合は非表示、またはグレーアウト）。
* **更新間隔の変更 (Polling Interval)**: `1分` / `3分` / `5分` / `10分` / `15分` / `30分` から選択可能。
* **区切り線**
* **アプリの終了 (Exit)**: 常駐アプリケーションを安全に終了する。

### 3.5. タイマー実行（ポーリング）機能

* デフォルト15分間隔（設定可能: 1分〜30分）で自動的にデータ取得処理をバックグラウンドで走らせ、表示を更新する。
* ポーリングは `threading.Timer` または同等のメカニズムで実装し、メインスレッド（GUIスレッド）をブロックしないこと。

### 3.6. プロセス再検出機能

* Antigravity IDE の再起動などにより Language Server プロセスが変わった場合、ポーリング時のAPI呼び出し失敗をトリガーとして自動的にプロセス再検出を行う。
* 再検出のリトライ間隔は段階的に延長する（バックオフ: 30秒 → 1分 → 5分）。

---

## 4. 非機能要件

### 4.1. 動作環境・技術スタック

| 項目 | 内容 |
|---|---|
| **対象OS** | Windows 10 / Windows 11 |
| **開発言語** | Python 3.10 以上 |
| **依存ライブラリ** | `pystray`（システムトレイ常駐用）、`Pillow (PIL)`（動的アイコン生成用） |
| **標準ライブラリのみで対応** | `subprocess`（プロセス検出）、`urllib.request` / `http.client` + `ssl`（HTTPS API呼び出し）、`json`（レスポンスパース）、`threading`（タイマー実行）、`re`（正規表現） |

> 外部ライブラリの依存を `pystray` と `Pillow` の2つに限定することで、セットアップの容易さとメンテナンス性を確保する。HTTP通信はPython標準の `urllib.request` + `ssl`（`SSLContext` で検証無効化）を使用し、`requests` ライブラリへの依存を不要とする。

### 4.2. パフォーマンス・リソース制約

* **低負荷設計**: 常駐アプリであるため、待機時のCPU使用率は 0.1% 未満、メモリ消費量は 50MB 以下に抑えること。
* **トークン消費の抑制**: `GetUserStatus` API 自体の呼び出しはアカウントのクォータ（Work Done）を消費しない前提とするが、Language Server への過剰な負荷を避けるため、自動更新の最小間隔は1分とする。

### 4.3. エラーハンドリング・堅牢性

| エラーケース | 対応 |
|---|---|
| **Language Server 未起動** | タスクバーアイコンを `!!` 表示、ツールチップに `"Antigravity not running. Waiting for process..."` と表示。バックオフ付きでプロセス再検出をリトライ。 |
| **プロセスは存在するがAPI応答なし** | タスクバーアイコンを `?` 表示、ツールチップに `"API connection failed. Retrying..."` と表示。次回ポーリングで再試行。 |
| **APIレスポンスのフォーマット変更** | JSONパースが部分的に失敗した場合も、取得できたデータは表示しつつ、パース失敗箇所をログに記録。プログラム全体がクラッシュしない設計。 |
| **CSRFトークン失効** | 401/403レスポンス受信時にプロセス再検出を自動実行（トークンはプロセス再起動で変わるため）。 |
| **ネットワーク/タイムアウト** | リクエストタイムアウトは5秒。タイムアウト時は前回の有効なデータを保持し表示を維持。 |

### 4.4. ログ出力

* ログファイル: `%USERPROFILE%\.quotachecker\quotachecker.log`
* ログレベル: `DEBUG` / `INFO` / `WARNING` / `ERROR`（デフォルト: `INFO`）
* ローテーション: 最大 1MB、バックアップ 3 世代
* 機密情報（CSRFトークン全文）はログに出力しない（先頭8文字のみマスク表示）。

### 4.5. セキュリティ・倫理的配慮（配布・公開仕様）

* **ソースコードの透明性**: 全てのソースコードをパブリックリポジトリで完全公開し、外部への通信（テレメトリや利用統計収集など）は一切行わず、通信先はローカルループバック（`127.0.0.1`）のみとする。
* **プロセス・トークン読み取りの開示**: WMIやPowerShellを用いて他プロセスのコマンドライン引数（CSRFトークン）を抽出する挙動について、マルウェアとの誤認を防ぐため、リポジトリの `README.md` に技術的仕組みを詳細に明記・開示する。
* **GitHub Actionsによる自動ビルド**: ローカルビルド（手動でのコード混入など）に対するユーザーの懸念を排除するため、配布用のWindows用実行ファイル（`.exe`）は GitHub Actions 上で自動ビルド（PyInstallerを使用）し、GitHub Release に成果物を自動デプロイする構成とする。
* **ソース実行・ビルド再現性の推奨**: ユーザーが自分でソースコードを監査・実行できるよう、Python環境での直接実行手順と `PyInstaller` を用いたセルフビルド手順を README に明記する。

---

## 5. インターフェース・外観イメージ

| 状態 | タスクバー（システムトレイ）アイコン | マウスホバー（ツールチップ） |
|---|---|---|
| **正常時（50%超）** | `[ 67 ]`（白色フォント） | 全モデルの残量とリセット時間をテキスト表示 |
| **注意（20%〜50%）** | `[ 35 ]`（黄色フォント） | 同上 |
| **残量低下時（20%未満）** | `[ 18 ]`（赤色フォント） | 同上（警告を視覚的に強調） |
| **枯渇（0%）** | `[ 0! ]`（赤色フォント＋背景変更） | "EXHAUSTED: Resets in Xh Ym" |
| **エラー時** | `[ !! ]` or `[ ? ]` | "Error: Cannot connect to Antigravity Language Server." |
| **初期接続中** | `[ .. ]`（グレー） | "Connecting to Antigravity..." |

---

## 6. ファイル構成（想定）

```
quotachecker/
├── docs/
│   └── requirements.md          ← 本ドキュメント
├── src/
│   ├── __init__.py
│   ├── main.py                  ← エントリポイント
│   ├── process_finder.py        ← Language Server プロセス検出
│   ├── quota_fetcher.py         ← GetUserStatus API呼び出し＆パース
│   ├── tray_manager.py          ← システムトレイ制御＆動的アイコン生成
│   └── config.py                ← 設定管理（ポーリング間隔、表示モデル等）
├── assets/
│   └── icon_base.png            ← ベースアイコン（右クリックメニュー等で使用）
├── requirements.txt             ← pystray, Pillow
└── README.md
```

---

## 7. 将来的な拡張の余地（スコープ外）

以下はv1.0のスコープには含めないが、設計上拡張しやすい構造を意識する。

* **Windows通知連携**: 残量が閾値（例: 10%）を下回った際にWindowsトースト通知を発行する機能。
* **設定ファイル**: JSON/TOML形式の設定ファイルによる永続的な設定保存。
* **Windows起動時の自動起動**: スタートアップフォルダまたはレジストリへの登録。
* **プロンプトクレジット残量表示**: 月間プロンプトクレジットの残量をアイコンまたはメニューに表示。

---

## 付録A：下書きからの変更履歴

| # | 項目 | 下書きの記述 | 変更内容と理由 |
|---|---|---|---|
| 1 | **データ取得方式** | `agy` CLIを `subprocess` / `wexpect` で起動し `/usage` の出力をスクレイピング | `/usage` はインタラクティブセッション内のスラッシュコマンドであり非対話的に実行不可。ローカル Language Server API（`GetUserStatus`）を直接呼び出す方式に変更 |
| 2 | **技術スタック** | `wexpect` を依存ライブラリに含む | 不要。API直接呼び出しにより `wexpect` / `pexpect` 系の依存を排除 |
| 3 | **クォータの区分** | 「5時間制限」「週間制限」を明示的に分離 | APIレスポンスにはこの区分が明示されない。モデルごとの `remainingFraction` + `resetTime` で管理する設計に変更 |
| 4 | **エラーハンドリング** | 「`agy` 未認証・未起動」のみ | Language Server未起動、CSRFトークン失効、APIタイムアウト、フォーマット変更等、より具体的なエラーケースを網羅 |
| 5 | **プロセス検出** | 記載なし | Language Server プロセスの検出ロジック（PowerShell / WMI）、Antigravityプロセスの識別、ポート・トークン抽出、ヘルスチェックの手順を新規追加 |
| 6 | **セキュリティ** | 記載なし | CSRFトークンのログ出力マスク、自己署名証明書のSSL検証無効化について追記 |
| 7 | **ツールチップ文字数制限** | 制限に関する記述なし | Windows APIの128文字制限について注記追加 |
| 8 | **プロセス再検出** | 記載なし | IDE再起動時の自動再接続メカニズムを新規追加 |
| 9 | **Gemini / Claude / GPT の固定列挙** | モデル名を固定で記載 | APIから動的にモデル一覧を取得する設計に変更（将来のモデル追加に対応） |

---

## 付録B：参考実装・リソース

| リソース | 説明 |
|---|---|
| `henrikdev.ag-quota` 拡張機能 | Antigravity IDE向けクォータ監視拡張。`GetUserStatus` APIの呼び出し方、プロセス検出ロジック、レスポンス型定義の参考実装として利用 |
| `agy-hud` プラグイン | ターミナル上のHUD表示。`remainingFraction` / `resetTime` のデータモデルの参考 |
