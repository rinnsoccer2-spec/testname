# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MT4 Expert Advisor 開発 + 補助ツール類

- Language: MQL4, Python
- Platform: MetaTrader4
- Version Control: Git

## Directory Structure

```
FX_Program/
├── src/          # MQL4ソースファイル (.mq4)
├── tools/        # 補助スクリプト (Python等)
└── CLAUDE.md
```

新規ファイルは上記の既存構成に従って配置すること。勝手に新しいフォルダを作らない。

## Build & Deployment

### MQL4 EAs (.mq4 → .ex4)
MQL4ファイルはMetaTrader4内蔵のMetaEditorでコンパイルする（コマンドラインからは不可）:
1. MT4からMetaEditorを起動（`Tools > MetaQuotes Language Editor` または F4）
2. `.mq4`ファイルを開いてF7（または`Compile`）を押す
3. コンパイル済み`.ex4`バイナリが同じディレクトリに出力される
4. MT4のNavigatorパネルからEAをチャートにドラッグ＆ドロップして実行

### Python Tools
```
pip install selenium
python tools/pia_retry.py
```

## EA Architecture

### MA_0.0.1.mq4 — Multi-Timeframe Moving Average EA
- **H1タイムフレーム**: EMA(20)スロープを計算、スロープ > 4.5pipsでトレンド方向確認
- **M5タイムフレーム**: EMA(20)スロープを計算、スロープ > 2.6pipsでエントリー整合
- **Bollinger Bands (20)**: 現在価格がバンド内にある場合のみエントリー（過剰拡張を回避）
- **クールダウン**: トレード間630秒
- **決済**: M5 MAローリング平均（直近8本）を動的決済トリガーとして使用
- SL: 11pips固定

### PDX+SAR_0.0.1 .mq4 — ADX + Parabolic SAR EA
- **ADX(14)**: ADX > 38.0 かつ ADX上昇中（現在 > 前のバー）が必要
- **Parabolic SAR**: SARが同じ側に3本以上連続していることが必要
- **クールダウン**: トレード間600秒
- **決済**: SARの反転（SARが価格に対して側が変わる）
- SL/TP: 各25pips（対称）

### tools/pia_retry.py — チケットキュー監視
Seleniumスクリプト。Piaチケットイベントページを5秒ごとにポーリング。日本語の待機室テキストを検出してリトライし、実際のイベントページが読み込まれたらブラウザを開いたまま保持する。

## Coding Standards

- コメントは日本語で記述
- 関数名: PascalCase
- 変数名: camelCase
- マジックナンバー禁止（定数または入力パラメータとして定義すること）
- 未使用コードを残さない

## Git Rules

- **mainブランチへの直接コミット禁止**
- 作業は必ずfeatureブランチを使用すること
- **force push禁止**
- コミット前に必ず差分を確認してからコミットを提案すること

## Secrets

以下を絶対に出力しない:

- API Key
- Password
- Token
- Secret
- Private Key
- Cookie
- Session ID

`.env` ファイルは参照禁止。

シークレットを発見した場合は内容を表示せず、警告のみ行うこと。

## Sensitive Files

以下のファイルは編集前にユーザーへの確認を要求すること:

- `.env`
- `.env.*`
- `*.key`
- `*.pem`
- `*.p12`
- `*.crt`

## File Deletion Policy

以下のコマンド・操作は**実行禁止**:

- `rm -rf`
- `git clean -fd`
- フォルダ一括削除

ファイル・フォルダの削除が必要な場合は、ユーザーが自分で実行すること。

## Network Policy

外部サイトへのアクセス（API送信・Webスクレイピング・POSTリクエスト等）は、ユーザーの**明示的な指示**がある場合のみ実行すること。自動的・勝手に実行しない。

## Execution Safety

以下は実行前にユーザーへ確認を求めること:

- shell実行
- docker実行
- `npm install`
- `pip install`
- PowerShell実行

## Commit Policy

コミットは**提案のみ**。以下はユーザーの承認後のみ実施:

- `git commit`
- `git push`
- PR作成

## Change Policy

指示された箇所のみ修正する。無関係なリファクタリングや整形は行わない。

## Trading Logic Protection

以下はユーザーの**明示的な指示**なしに変更禁止:

- エントリー条件
- 決済条件
- ロット計算
- リスク管理パラメータ（SL/TP等）

## Testing

MQL4ファイルを修正した後は、MetaEditorでのコンパイル結果（エラー・警告の有無）を確認して報告すること。

## MT4 Experts ディレクトリアクセスポリシー

以下のパスは**読み取りを自由に行ってよい**（確認不要）:

```
C:\Users\rinns\AppData\Roaming\MetaQuotes\Terminal\082F53F5881F3D6022DF806C3D307B50\MQL4\Experts
```

書き込み・編集・削除はこれまで通りの方針（慎重に・確認を取る）に従うこと。
