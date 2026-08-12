# マルチスポーツ対応 タスクリスト

- 仕様書: [MULTI_SPORT_SPEC.md](MULTI_SPORT_SPEC.md)
- 起点コミット: 8c9fe6b
- 最終更新: 2026-08-12

## 進捗サマリ

| Phase | 内容 | 状態 | 進捗 |
|---|---|---|---|
| 0 | 設計 | ✅ 完了 | 4/4 |
| 1 | MVP（プロファイル機構と kite の等価性確保） | ✅ 完了 | 6/6 |
| 2 | 競技追加（windsurf / wing） | ✅ 完了 | 5/5 |
| 3 | GUI・配布 | ⬜ 未着手 | 0/8 |
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

- [x] 2-1 `profiles/windsurf.json` / `profiles/wing.json` を作成（`equipment_detector` は `"none"`、しきい値は kite と同値の暫定値） — 2026-08-12 完了
- [x] 2-2 `record_labels.py` に `--sport` を追加し、出力先を `paths.dataset` に — 2026-08-12 完了
- [x] 2-3 `frame_scorer.py` の train / predict に `--sport` を追加 — 2026-08-12 完了
- [x] 2-4 混在ガード実装（`logs/active_profile.json` + `--allow-profile-switch`、終了コード2） — 2026-08-12 完了
- [x] 2-5 検証: 既存 pkl が `--sport kite` で読める（V-2）／ `--sport windsurf` が通し実行できる（V-3）／ 混在ガードが働く（V-4） — 2026-08-12 完了

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
- [ ] 3-7 CLIPモデルパスの解決基準の不整合を検討（**既存の挙動。Phase 2 で作り込んだものではない**）
      `frame_scorer.py:720` の `clip_model_path = root / CLIP_MODEL_RELATIVE_PATH` は `--root`（作業フォルダ）基準。
      一方 `profiles/` と `paths.model` は `app_base_dir()`（アプリ設置場所）基準になった。
      通常運用では両者が同じフォルダなので顕在化しないが、`--root` を別の場所にすると
      CLIPモデルだけ見つからなくなる。GUI改修時に基準を揃えるか判断する。
      挙動変更を伴うため、揃える場合は単独で回帰確認すること。

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

## Phase 2 実装ログ（2026-08-12）

- 新規: `profiles/windsurf.json` / `profiles/wing.json`
  - `equipment_detector` は `"none"`、cv/segment のしきい値は kite と同値の暫定値（推測での別値は入れていない）
  - `paths.dataset` は `dataset/windsurf/labels.csv` / `dataset/wing/labels.csv`、`paths.model` は
    `models/windsurf/frame_scorer.pkl` / `models/wing/frame_scorer.pkl`
  - `display_name` は「ウィンドサーフィン」「ウィングフォイル」
- 変更: `sport_profile.py`
  - 混在ガードの共通関数を新設（3ファイルへのコピペを避けるため）。
    `check_profile_switch(root, sport_id, allow_switch)` が `ProfileSwitchGuardError` を送出、
    `write_active_profile(root, sport_id)` が `logs/active_profile.json` を書き出す
  - ガード対象は `review/frames` / `selected_frames`（再帰的にファイル数をカウント）と
    `logs/ai_scores.csv` / `logs/ai_segments.csv`（ヘッダを除いたデータ行数をカウント）
  - `active_profile.json` が存在しない・壊れている場合はガード判定をスキップする
    （導入前のワークフォルダや初回実行を誤ってブロックしないため。仕様書に明記はないが、
    「推測でフォールバックしない」原則とは別に、安全側に倒す実装判断として追記）
- 変更: `kite_clipper.py`
  - `--allow-profile-switch` を追加
  - `main()` の冒頭（`--cleanup-review` 以外）で `sport_profile.check_profile_switch()` を呼び、
    `ProfileSwitchGuardError` はメッセージを表示して終了コード2で停止
  - `--cleanup-review` はガード対象から除外（ガードが示す対処法そのものが実行できなくなるため）
  - メインパイプライン（フレーム抽出・分析）が成功した最後に `sport_profile.write_active_profile()` を呼ぶ
    （`--cut-from-*` / `--cleanup-after-cut` / `--concat-selected-clips` 等の補助コマンドでは書き込まない。
    理由: これらは `--sport` を明示しなくても既定値 kite に解決されてしまうため、書き込むと
    実際には触れていない競技で `active_profile.json` を上書きしてしまう恐れがあるため）
- 変更: `frame_scorer.py`
  - `import sport_profile` を追加（末端モジュールのため制約C-4に抵触しない）
  - `app_base_dir()` を複製実装（`kite_clipper.py` を import しない方針のため）
  - `resolve_sport_profile(sport_arg)` を新設し、train/predict 両サブコマンドで使用
  - train: `--dataset` / `--model-out` の `default` を `None` に変更し、無指定時はプロファイルの
    `paths.dataset` / `paths.model` を使う（CLI明示 > プロファイル > 従来のハードコード既定値の順）
  - predict: `--model` の `default` を `None` に変更し同様に解決。`--enter` / `--exit` /
    `--smooth-window` も `default=None` にし、`profile.segment` の値で解決
  - train/predict 冒頭で `sport_profile.check_profile_switch()` を呼び、
    `ProfileSwitchGuardError` は終了コード2で停止
  - predict 成功時のみ `sport_profile.write_active_profile()` を呼ぶ（train は `review/frames` 等の
    共有作業フォルダに触れないため対象外とした）
  - モデル保存先ディレクトリ（`models/windsurf/` 等）は既存の `model_out_path.parent.mkdir()` が
    そのままカバーするため追加変更不要だった
- 変更: `record_labels.py`
  - `import sport_profile` と `app_base_dir()` を追加、`--sport` を追加
  - 出力先を固定の `dataset/labels.csv` から `profile.dataset_path`（既定 kite で従来と同じパス）に変更。
    `dataset_frames_dir` も `profile.dataset_path.parent / "frames"` から導出するよう変更
  - 混在ガードは実装対象外（引き継ぎメモの指定どおり `kite_clipper.py` / `frame_scorer.py` のみに実装）

### 検証結果（隔離ワークスペース使用、ユーザーの作業フォルダ・real dataset/models は変更していない）

- **V-1（回帰）**: `scratchpad/baseline_ws` を再利用し、`review/` `logs/` `selected_*` `selected_clips` `final` `work`
  を `shutil.rmtree` で削除してから実行。
  - `--sport` 省略 → `logs/result.csv` と `multi_sport/baseline/result_before.csv` の `diff` が完全ゼロ
  - `--sport kite` 明示 → 同上、`diff` 完全ゼロ
  - いずれも `logs/active_profile.json` に `{"sport_id": "kite", ...}` が書き出されることを確認
- **V-2**: `frame_scorer.py predict --sport kite --root <隔離WS>` を実行。
  `profile.model_path` がプロジェクト本体の `models/frame_scorer.pkl`（実在・読み込み成功）に解決されることを確認。
  `joblib.load()` は例外なく完了し、その後は隔離WS内に `vendor/clip/vision_model_quantized.onnx` が
  無いために別エラーで停止した（CLIP埋め込みモデルのパス解決は本タスクの変更対象外の既存挙動であり、
  「モデル読み込みで落ちない」という検証観点は満たしている）
- **V-3**: 新規WS（`scratchpad/windsurf_ws1`）に動画をコピーし
  `kite_clipper.py --root <新規WS> --sport windsurf --only-video 20260704_161647` を実行。
  終了コード0、`logs/result.csv` の `has_kite_candidate` は全行 `False`（器材検出スキップを確認）
- **V-4（混在ガード）**:
  - windsurf で実行済みの WS に対し `kite_clipper.py --sport kite` → 終了コード2、
    `review/frames に 6 件の未処理データが残っています。` を含むメッセージを出力して停止
  - 同条件で `--allow-profile-switch` を付けて再実行 → 終了コード0で正常完了、
    `active_profile.json` が `kite` に更新される
  - 同じ mismatch 状態で `frame_scorer.py predict --sport windsurf --root <WS>` → 終了コード2で同様に停止
    （kite_clipper.py・frame_scorer.py 両方でガードが機能することを確認）
  - 新規WS（`scratchpad/fresh_ws1`）で kite 実行後に中間生成物をすべて削除してから `--sport windsurf` に
    切替 → 終了コード0（誤検知しないことを確認）

### 制約・仕様との整合性メモ

- 制約 C-1〜C-5 はすべて維持（`has_kite_candidate` 列名・`FEATURE_NAMES` の要素数と順序・
  kite のパス据え置き・`segment_simulator.py` 無変更・`frame_scorer.py`/`record_labels.py` が
  `kite_clipper.py` を import しない設計）を変更していない
- 仕様書に明記が無く自分で判断した点:
  1. 混在ガードを適用する範囲。`kite_clipper.py` の `--cleanup-review` のみ明示的に除外し、
     `--cut-from-*` 系や `--concat-selected-clips` にもガードを適用した（仕様書はサブコマンド単位の
     除外を明記していないため、安全側に倒し「起動時」全般に適用しつつ、唯一の救済コマンドだけ除外した）
  2. `active_profile.json` の書き込みタイミング。メインパイプライン（kite_clipper.py の既定フロー）と
     frame_scorer.py の predict 成功時のみ書き込み、cleanup系・cut系・train には書き込まないとした
     （これらは `--sport` を指定しなくても既定値 kite に解決されてしまうため、書き込むと未使用の競技で
     上書きしてしまうリスクがあると判断した）
  3. `active_profile.json` が存在しない・壊れている場合はガード判定をスキップする（明示エラーで
     停止する対象は sport_profile.json 自体の読み込み失敗のみとし、ガード用の状態ファイルの欠落・破損は
     「初回実行」として扱った）
- 抵触しかけた点: frame_scorer.py の CLIP モデルパス（`CLIP_MODEL_RELATIVE_PATH`）は `--root`
  （作業フォルダ）基準のままで、`paths.dataset`/`paths.model` のように app_base_dir 基準にしていない。
  これは既存の挙動（Phase 2 の変更対象外）であり、本タスクでは触れていない

## 未確定事項

MULTI_SPORT_SPEC.md 8章と同期。決まり次第、決定ログへ移す。

1. windsurf / wing の enter/exit/split_gap の適正値 → 4-4 / 4-8 で確定
2. フレーム抽出間隔（wing で1.0秒が粗すぎるか） → 4-8 で判断
3. 作業フォルダの競技別分離の要否 → Phase 3 まで運用してから再検討
4. `has_kite_candidate` 列名の汎用化 → 本タスクでは実施しない
