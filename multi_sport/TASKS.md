# マルチスポーツ対応 タスクリスト

- 仕様書: [MULTI_SPORT_SPEC.md](MULTI_SPORT_SPEC.md)
- 起点コミット: 8c9fe6b
- 最終更新: 2026-08-12

## 進捗サマリ

| Phase | 内容 | 状態 | 進捗 |
|---|---|---|---|
| 0 | 設計 | ✅ 完了 | 4/4 |
| 1 | MVP（プロファイル機構と kite の等価性確保） | ✅ 完了 | 6/6 |
| 2 | 競技追加（windsurf / wing） | ⬜ 未着手 | 0/5 |
| 3 | GUI・配布 | ⬜ 未着手 | 0/7 |
| 4 | 実データ運用（コード変更なし） | ⬜ 未着手 | 0/8 |

状態記号: ⬜ 未着手 / 🟡 作業中 / ✅ 完了 / ⛔ ブロック中 / ⏭ 見送り

---

## Phase 0: 設計

- [x] 0-1 既存コードのスポーツ依存範囲を調査（`detect_kite_candidate` / `has_kite_candidate` / モデル・データセットのパス）
- [x] 0-2 方式決定（fork ではなくプロファイル切り替え）
- [x] 0-3 仕様書作成（MULTI_SPORT_SPEC.md）
- [x] 0-4 作業フォルダ `multi_sport/` の設置

---

## Phase 1: MVP（プロファイル機構と kite の等価性確保）

**完了条件**: `--sport kite` の `logs/result.csv` が変更前とバイト一致すること。

- [x] 1-0 **等価性確認用のベースライン取得**（実装着手前に必須） — 2026-08-12 完了
      → `multi_sport/baseline/result_before.csv`（20行 / SHA256 `278b985a...`）
      取得条件・再現手順・カバー範囲は [baseline/README.md](baseline/README.md) を参照。
      ユーザーの作業フォルダは変更せず、スクラッチパッド上の隔離ワークスペースで `--root` 実行した。
      `has_kite_candidate` は True 17 / False 3 で**両分岐をカバー**している。
- [x] 1-1 `sport_profile.py` を新規作成（dataclass + `load_profile` + `list_profiles` + `detect_equipment_candidate` ディスパッチ） — 2026-08-12 完了
      → 配置はリポジトリルート（製品コード。`multi_sport/` ではない）。他プロジェクトモジュールを import しない末端モジュールとして実装。
- [x] 1-2 `profiles/kite.json` を作成し、現行ハードコード値を転記 — 2026-08-12 完了
      → 配置はリポジトリルート `profiles/`。`kite_clipper.py` の `parse_args()` と `frame_scorer.py` の `DEFAULT_SEGMENT_*` 定数から現物確認して転記。
- [x] 1-3 `kite_clipper.py` に `--sport` を追加し、cv/segment の既定値をプロファイル由来にする — 2026-08-12 完了
      → 明示指定 > プロファイル値 > 従来の既定値 の優先順位を守る。該当引数は `default=None` にして `resolve_sport_profile()` でパース後に解決。
- [x] 1-4 `kite_clipper.py:342` の `detect_kite_candidate()` 呼び出しを `equipment_detector` 分岐に変更 — 2026-08-12 完了
      → アルゴリズム本体（`kite_clipper.py:415`）は無変更。`sport_profile.detect_equipment_candidate(image, profile, kite_detector=detect_kite_candidate)` 経由で分岐。
- [x] 1-5 **等価性確認**: 1-0 のベースラインと `logs/result.csv` を比較し差分ゼロを確認（成功条件1 / V-1） — 2026-08-12 完了
      → 3パターンとも隔離ワークスペースで確認済み。詳細はこのファイル末尾の実装ログを参照。

---

## Phase 2: 競技追加

- [ ] 2-1 `profiles/windsurf.json` / `profiles/wing.json` を作成（`equipment_detector` は `"none"`、しきい値は kite と同値の暫定値）
- [ ] 2-2 `record_labels.py` に `--sport` を追加し、出力先を `paths.dataset` に
- [ ] 2-3 `frame_scorer.py` の train / predict に `--sport` を追加
- [ ] 2-4 混在ガード実装（`logs/active_profile.json` + `--allow-profile-switch`、終了コード2）
- [ ] 2-5 検証: 既存 pkl が `--sport kite` で読める（V-2）／ `--sport windsurf` が通し実行できる（V-3）／ 混在ガードが働く（V-4）

---

## Phase 3: GUI・配布

- [ ] 3-0 `sport_profile.list_profiles()` の堅牢化を検討
      現在は1つでも壊れたJSONがあると `ProfileError` で全体が落ちる実装。CLI では正しい挙動だが、
      GUIのドロップダウン生成でこれをそのまま使うと**1ファイルの不備でアプリが起動不能になる**。
      GUI側では「読めたものだけ列挙し、読めなかったものはログに警告」する経路を用意するか判断する。
- [ ] 3-1 GUI に競技ドロップダウンを追加し、`logs/gui_settings.json` で永続化
- [ ] 3-2 `kite_clipper_gui.py:46` の `FRAME_SCORER_MODEL_PATH` ハードコードを廃し、選択中プロファイル参照に（`:1369` のチェックも）
- [ ] 3-3 `frame_scorer_launcher.py` の `build_predict_command()` に `--sport` を渡す
- [ ] 3-4 `kite_clipper_gui.spec` の `datas` に `profiles/` を追加
- [ ] 3-5 README.md / WORKFLOW.md に競技切り替え手順を追記
- [ ] 3-6 検証: GUI 切り替えの永続化（V-5）／ PyInstaller ビルド後の exe で競技一覧が出る（V-6）

---

## Phase 4: 実データ運用（コード変更なし）

**目標データ量（実用ライン）**: 動画15〜25本 / 総ラベル2,000〜4,000枚 / うち正例300〜600枚
根拠と最小ラインは MULTI_SPORT_SPEC.md 6章を参照。

### windsurf

- [ ] 4-1 動画の収集（**5セッション以上に分散**。同一セッション偏重は交差検証を楽観的にする）
- [ ] 4-2 フレーム抽出とラベリング（`record_labels.py --sport windsurf`）
- [ ] 4-3 `frame_scorer.py train --sport windsurf` を実行し PR-AUC を確認
- [ ] 4-4 `segment_simulator.py` で enter/exit/split_gap を実測し、`profiles/windsurf.json` の暫定値を確定値に更新
      → 測定結果は `multi_sport/tuning_windsurf.md` に残す

### wing

- [ ] 4-5 動画の収集
- [ ] 4-6 フレーム抽出とラベリング
- [ ] 4-7 学習と PR-AUC 確認
- [ ] 4-8 しきい値の実測と確定（`multi_sport/tuning_wing.md`）
      → ジャンプが短く1.0秒間隔では取りこぼす可能性あり。`--frame-interval 0.5` の要否もここで判断

---

## 実装担当への引き継ぎメモ

実装は Sonnet が担当する想定。着手時に読むべき順序:

1. `multi_sport/MULTI_SPORT_SPEC.md` の 1章（要件）と 4章（設計）
2. 本ファイルの該当 Phase
3. 特に **1.4 制約 C-1〜C-5**（ここを破ると既存のカイト資産が壊れる）

必ず守ること:

- `has_kite_candidate` という**文字列は変更しない**（CSV列名・`FEATURE_NAMES` の要素・`PhotoAnalysis` のフィールド）
- `FEATURE_NAMES` の要素数と順序を変えない（既存 pkl との互換）
- kite のデータセット/モデルのパスは現行のまま据え置く（`dataset/labels.csv` / `models/frame_scorer.pkl`）
- `segment_simulator.py` は変更しない（コード内コメントで変更禁止と明記されている）
- `frame_scorer.py` は `kite_clipper.py` / `record_labels.py` を import しない（既存の設計方針）
- 未知の `sport_id` や JSON パースエラーは、推測でフォールバックせず明示エラーで停止する

Phase 1 は既存挙動を1ミリも変えないのが目的。1-0 のベースライン取得を飛ばさないこと。

---

## 決定ログ

| 日付 | 決定 | 理由 |
|---|---|---|
| 2026-08-12 | アプリを fork せず、プロファイル切り替え方式にする | スポーツ依存コードが `detect_kite_candidate()` と数個のパラメータのみ。CLIP埋め込み以降の約6,000行は競技非依存。fork すると以後の不具合修正が2箇所メンテになる |
| 2026-08-12 | windsurf / wing は器材検出を使わない（`"none"`） | 現行アルゴリズムは探索範囲が画像上部65%、ブロブ面積上限が画像の8%。セイル/ウィングはライダーと同じ高さかつ画面の数十%を占めるため、いずれの条件も満たさない |
| 2026-08-12 | `has_kite_candidate` の列名は据え置く | result.csv → labels.csv → pickle化モデルの特徴量順序 まで貫通しており、改名すると既存資産が無言で壊れる。汎用化は移行スクリプトを伴う別タスク |
| 2026-08-12 | 作業フォルダは共有し、`active_profile.json` で混在ガード | 競技別ワークスペース分離は全パス定数に手が入り、regression リスクが目的（無事故の移行）と衝突する |

---

## Phase 1 実装ログ（2026-08-12）

- 新規: `sport_profile.py`（リポジトリルート） / `profiles/kite.json`
- 変更: `kite_clipper.py`
  - `import sport_profile` を追加
  - `--sport` を追加（既定 `None` → `resolve_sport_profile()` で `sport_profile.DEFAULT_SPORT_ID`("kite") に解決）
  - `--blur-threshold` / `--dark-threshold` / `--min-width` / `--min-height` / `--similarity-threshold` /
    `--frame-interval` / `--split-gap` / `--pre-roll` / `--post-roll` の `default` を `None` に変更し、
    `resolve_sport_profile()` でパース後に「CLI明示 > プロファイル値 > 旧ハードコード既定値
    （`DEFAULT_BLUR_THRESHOLD` 等のモジュール定数）」の順で解決するようにした
  - `resolve_sport_profile()` を新設。`app_base_dir()`（exeの設置場所 or スクリプト直下）を基準に
    `profiles/<sport>.json` を読み込む。`--root`（作業フォルダ）とは別物であることに注意
  - `analyze_frame()` / `analyze_frame_task()` / `analyze_frames()` に `profile` 引数を追加し、
    `detect_kite_candidate(image)` の直接呼び出しを
    `sport_profile.detect_equipment_candidate(image, profile, kite_detector=detect_kite_candidate)` に置換。
    `detect_kite_candidate()` 本体（`kite_clipper.py:415`付近）は無変更。`ProcessPoolExecutor.map` にも
    `profile` を追加でブロードキャストしている（`SportProfile` は frozen dataclass で pickle 可能）
  - `main()` の冒頭で `resolve_sport_profile()` を呼び、`sport_profile.ProfileError` は
    メッセージを表示して終了コード2で停止（推測フォールバックはしない）

### 検証結果（3パターン、隔離ワークスペース使用）

いずれもユーザーの `input/` `review/` `selected_*` `logs/` は変更していない
（スクラッチパッド上の隔離ワークスペースに `input/videos/*.mp4` のみコピーし `--root` 指定で実行）。

1. `--sport` なし → `diff logs/result.csv multi_sport/baseline/result_before.csv` は差分ゼロ（`echo`終了コード0を確認）
2. `--sport kite` → 同上、差分ゼロ
3. `--sport kite --blur-threshold 5 --dark-threshold 200 --min-width 99999` →
   baseline（既定値: blur_threshold 120 / dark_threshold 70 / min_width 1280）と比較すると
   フラグ分布が明確に変化することを確認した:
   - `is_blurry`: 既定値では 20/20 True → 明示値(5)では 0/20 True（全て False）
   - `is_dark`: 既定値では 0/20 True → 明示値(200)では 20/20 True
   - `is_low_resolution`: 既定値では 0/20 True → 明示値(99999)では 20/20 True
   → プロファイル値（既定 120/70/1280）に上書きされず、CLI明示値がそのまま効いていることを確認

## 未確定事項

MULTI_SPORT_SPEC.md 8章と同期。決まり次第、決定ログへ移す。

1. windsurf / wing の enter/exit/split_gap の適正値 → 4-4 / 4-8 で確定
2. フレーム抽出間隔（wing で1.0秒が粗すぎるか） → 4-8 で判断
3. 作業フォルダの競技別分離の要否 → Phase 3 まで運用してから再検討
4. `has_kite_candidate` 列名の汎用化 → 本タスクでは実施しない
