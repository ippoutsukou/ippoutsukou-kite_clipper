# kite_clipper Webアプリ化 要件定義・仕様書

作成日: 2026-07-17
ステータス: ドラフト(ユーザー承認待ち)

## 1. 要件定義

### 目的

フレーム選別〜結合の一連の操作を、iPad / iPhone のブラウザ(Safari)から行えるようにする。
重い処理(ffmpeg・AI採点)は自宅の Windows PC 上で実行し、iPad は操作画面に徹する。

### 対象(MVPスコープ)

現行ワークフロー(WORKFLOW.md)のうち、以下を Web 画面から実行できるようにする。

| 手順 | 内容 | Web画面での操作 |
|------|------|----------------|
| 2 | フレーム抽出 | 間隔(1 / 0.5 / 0.25秒)を選んで実行、進捗表示 |
| 2.5 | AI採点(任意) | 実行ボタン、スコアバッジ・区間バンド表示 |
| 3 | フレーム選別 | サムネイル一覧をタップで採用/解除、動画ごとの絞り込み、AI区間絞り込み、AI提案一括採用、範囲適用 |
| 4 | 切り出し | pre-roll / post-roll / split-gap を指定して実行 |
| 6 | 結合 | クリップの並べ替え(ドラッグ)、最終サイズ・表示方法の選択、実行、final動画の再生・確認 |

### 非対象(MVPに含めない)

- iPadからの動画アップロード(元動画は今まで通りPCで `input/videos` に入れる)
- 複数ユーザー対応・ログイン認証
- 外出先(自宅Wi-Fi外)からのアクセス
- 手順4.5(ラベル記録)・4.6(再学習)・5(クリーンアップ)・7(review整理)
  → 当面は既存のTkinter GUIまたはCLIで実施(将来拡張候補)
- 写真整理機能

### 制約

- 利用場所: 自宅Wi-Fi(LAN)内のみ。iPadから `http://<PCのIPまたはホスト名>:8000` でアクセス
- 既存のCLI・Tkinter GUI・フォルダ構成(`input/` `review/` `selected_frames/` `selected_clips/` `final/` `logs/` `work/`)は変更しない
  - Webアプリは既存パイプラインの「薄いフロントエンド」として追加する
  - 採用状態の真実は今まで通り `selected_frames/` のファイル有無(Tkinter GUIと共存可能)
- 対象ブラウザ: iPadOS / iOS の Safari(最新)。PCブラウザでも動作すること
- 新規依存の追加は最小限(FastAPI + uvicorn を想定。追加前に確認)

### 成功条件

1. PCで `input/videos` に動画を入れてWebサーバーを起動した後、iPadのSafariだけで
   「抽出 → (AI採点) → 選別 → 切り出し → 並べ替え → 結合 → final動画の再生確認」が完了する
2. サムネイル選別がタップで快適に操作できる(1動画数百フレームで実用的な表示速度)
3. 既存のTkinter GUI・CLIが今まで通り動作する(共存)

## 2. 仕様概要

### 画面構成(ステッパー形式・現行GUI踏襲)

1. **ホーム / 動画一覧**: `input/videos` の動画一覧、フレーム間隔選択、「抽出実行」
2. **フレーム選別**: サムネイルグリッド
   - タップで採用/解除(採用 = `selected_frames/` へコピー、解除 = コピー削除)
   - 動画絞り込み・サムネイルサイズ切替・NG候補隠し・♪バッジ・AIスコアバッジ・区間バンド
   - 「AI採点を実行」「AI提案を採用」ボタン
   - 選択枚数・推定クリップ本数・推定合計時間の常時表示
3. **切り出し**: pre-roll / post-roll / split-gap 指定 → 実行 → 結果一覧
4. **結合**: `selected_clips` 一覧(時間表示・合計時間)、ドラッグで並べ替え、
   最終サイズ(vertical/horizontal/square/original)と表示方法(contain/cover)選択 → 実行
5. **完成**: `final/` の動画一覧、ブラウザ内再生、(iPadへの保存はSafariのダウンロード機能)

### API設計(概要)

- `GET  /api/videos` — input/videos の一覧
- `GET  /api/frames?video=...` — 抽出済みフレーム一覧(採用状態・AIスコア・区間・NG・♪含む)
- `GET  /thumbs/{frame}` — サムネイル画像(work/frame_thumbs キャッシュ流用)
- `POST /api/frames/select` / `POST /api/frames/deselect` — 採用/解除(範囲指定対応)
- `POST /api/jobs/extract | score | cut | concat` — 長時間処理をバックグラウンドジョブとして開始
- `GET  /api/jobs/{id}` — 進捗ポーリング(状態・ログ末尾)
- `GET  /api/clips` / `POST /api/clips/order` — selected_clips 一覧と並び順
- `GET  /final/{name}` — 完成動画の配信(Range対応でSafari再生)

### 長時間処理の扱い

- 抽出・採点・切り出し・結合は subprocess で既存CLI(`kite_clipper.py` / `frame_scorer.py`)を呼び出す
- 同時実行は1ジョブに制限(実行中は他ジョブの開始ボタンを無効化)
- 進捗はジョブAPIのポーリングで表示

## 3. 技術スタック

| 層 | 選定 | 理由 |
|----|------|------|
| バックエンド | Python + FastAPI + uvicorn | 既存Pythonコードをそのまま呼べる。依存追加が最小 |
| フロントエンド | HTML + CSS + vanilla JS(ビルド工程なし) | 規模的にフレームワーク不要。npm等の依存を増やさない |
| 動画再生 | HTML5 `<video>`(H.264/mp4はSafariネイティブ再生) | 追加ライブラリ不要 |
| PWA | manifest + ホーム画面追加対応 | 「アプリっぽく」起動できる(オフライン動作は非対象) |

新規依存: `fastapi`, `uvicorn` の2つのみ(インストール前に確認を取る)。

## 4. フォルダ構成(追加分)

```text
webapp/
  server.py          # FastAPI アプリ(API + 静的配信)
  jobs.py            # バックグラウンドジョブ管理
  static/
    index.html
    app.js
    style.css
    manifest.json
```

起動: `.\.venv\Scripts\python.exe -m uvicorn webapp.server:app --host 0.0.0.0 --port 8000`

## 5. タスクリスト

### MVP

1. FastAPI土台: 静的配信・動画一覧API・サムネイル配信(既存キャッシュ流用)
2. フレーム選別画面: サムネイルグリッド、タップ採用/解除、動画絞り込み、選択状況表示
3. ジョブ基盤: 抽出/採点/切り出し/結合のジョブAPI+進捗表示UI
4. AI表示: スコアバッジ・区間バンド・AI区間絞り込み・AI提案一括採用
5. 結合画面: クリップ並べ替え・正規化オプション・final再生
6. PWA化+iPad実機確認(同一Wi-Fi)

### 安全版(MVP後)

- 入力検証(パス外参照の遮断など)・エラー表示の整備・ログ出力
- ジョブの多重実行ガードの強化・中断機能
- (将来)簡易認証・HTTPS化・外出先アクセス(Tailscale等)・仲間への公開
