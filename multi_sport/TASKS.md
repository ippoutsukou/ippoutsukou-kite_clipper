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
| 3 | GUI・配布 | ✅ 完了（V-5/V-6のみユーザー確認待ち） | 7/8 |
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

- [x] 3-0 `sport_profile.list_profiles()` の堅牢化を検討 — 2026-08-12 完了
      → `list_profiles_lenient(root) -> (list[SportProfile], list[tuple[str, str]])` を新設。
      読めたプロファイルだけを返し、読めなかったものは (ファイル名, エラー理由) として分けて返す
      （例外を送出しない）。既存の `list_profiles()` は変更せず、CLIの厳格な挙動を維持。
- [x] 3-1 GUI に競技ドロップダウンを追加し、`logs/gui_settings.json` で永続化 — 2026-08-12 完了
      → ステッパーの上に配置。`self.busy` 中は disabled。切り替え時に中間生成物が残っていれば
      3択（整理してから切り替える／そのまま続行する／キャンセル）の確認ダイアログを表示。
      「整理してから切り替える」は `--cleanup-review --confirm-delete` →
      `--allow-profile-switch --cleanup-after-cut --confirm-delete --include-review-frames
      --include-review-photos` → `logs/ai_scores.csv` / `logs/ai_segments.csv` をGUI側で直接削除、
      の3段階で混在ガード対象を完全にクリアしてから切り替える。
- [x] 3-2 `kite_clipper_gui.py:46` の `FRAME_SCORER_MODEL_PATH` ハードコードを廃し、選択中プロファイル参照に（`:1369` のチェックも） — 2026-08-12 完了
      → `current_model_path()` を新設（プロファイル未読み込み時のみ旧パスにフォールバック）。
      おまかせモードの未学習警告に競技名を含めるよう変更。
- [x] 3-3 `frame_scorer_launcher.py` の `build_predict_command()` に `--sport` を渡す — 2026-08-12 完了
      → `build_predict_command(root, sport=None, extra=None)` に変更。凍結時・非凍結時どちらの
      分岐でも同じ `extra_args`（`--sport` を含む）を使うため書き漏れなし。
      `kite_clipper_gui.py` の `run_cli` / `run_frame_scorer_predict` に加え、
      `frame_selector.py`（ビューア内「AI採点を実行」）にも `get_predict_extra_args` コールバック
      経由で `--sport` を伝搬（仕様書に明記は無いが、ここも kite 固定で採点していた同種のバグの
      ため合わせて修正。詳細は本ファイル末尾の実装ログを参照）。
- [x] 3-4 `kite_clipper_gui.spec` の `datas` に `profiles/` を追加 — 2026-08-12 完了
      → `hiddenimports` にも `sport_profile` を追加。PyInstaller未導入環境のため実ビルドでの
      動作確認はできていない（ユーザー確認待ち。3-6参照）。
- [x] 3-5 README.md / WORKFLOW.md に競技切り替え手順を追記 — 2026-08-12 完了
- [ ] 3-6 検証: GUI 切り替えの永続化（V-5）／ PyInstaller ビルド後の exe で競技一覧が出る（V-6）
      → **ユーザー確認待ち。** 目視でのGUI操作確認とexeビルドは本環境では実施できなかった。
      ガード判定・コマンド組み立て・永続化ロジックは関数単体でのin-process検証で代替した
      （詳細は実装ログを参照）。
- [x] 3-7 CLIPモデルパスの解決基準の不整合を検討（**既存の挙動。Phase 2 で作り込んだものではない**） — 2026-08-12 判断
      → **Phase 3 では見送り。** `frame_scorer.py:720` の `clip_model_path = root / CLIP_MODEL_RELATIVE_PATH`
      は `--root`（作業フォルダ）基準のまま。通常運用では `--root` と `app_base_dir()` が同じフォルダ
      になるため顕在化しない。挙動変更を伴い単独の回帰確認が必要なため、本Phaseのスコープ外とする。

---

## Phase 4: 実データ運用（コード変更なし）

**目標データ量（実用ライン）**: 動画15〜25本 / 総ラベル2,000〜4,000枚 / うち正例300〜600枚
根拠と最小ラインは MULTI_SPORT_SPEC.md 6章を参照。

**着手順**: 2026-08-12 にユーザーの判断で **wing を先行**することにした（当初は windsurf 先行の想定だった）。

### wing（先行）

- [ ] 4-1 動画の収集（**5セッション以上に分散**。同一セッション偏重は交差検証を楽観的にする）🟡 収集中
- [ ] 4-2 フレーム抽出とラベリング（`record_labels.py --sport wing`）🟡 進行中
- [ ] 4-3 `frame_scorer.py train --sport wing` を実行し PR-AUC を確認
- [ ] 4-4 しきい値の実測と確定（`segment_simulator.py` → `profiles/wing.json` の暫定値を更新）
      → 測定結果は `multi_sport/tuning_wing.md` に残す
      → ジャンプが短く1.0秒間隔では取りこぼす可能性あり。`--frame-interval 0.5` の要否もここで判断

### windsurf（後続）

- [ ] 4-5 動画の収集
- [ ] 4-6 フレーム抽出とラベリング（`record_labels.py --sport windsurf`）
- [ ] 4-7 学習と PR-AUC 確認
- [ ] 4-8 しきい値の実測と確定（`multi_sport/tuning_windsurf.md`）
      → ウィンドのジャイブは3〜8秒と長い。`split_gap` / `pre_roll` / `post_roll` を特に見直すこと

> **Phase 3 完了により解消済み（2026-08-12）**
> 上記の注意はPhase 3（3-1〜3-3）の実装により解消した。GUIは選択中の競技を
> `logs/gui_settings.json` に保存し、`--sport` を自動付与するようになった。CLIで別競技を
> 実行した後にGUIへ戻っても、GUI側の事前ガードチェックが確認ダイアログを出すため、
> 謎の終了コード2で止まることはない（詳細はPhase 3実装ログを参照）。

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
| 2026-08-12 | GUIの混在ガード事前チェックは `sport_profile.check_profile_switch()` をそのまま呼ぶ | GUI独自の判定ロジックを別途書くと、CLIとGUIで「いつ止まるか」の基準がずれる恐れがある。同一関数を in-process で呼ぶことで判定基準を一本化した |
| 2026-08-12 | `frame_scorer.py:720` のCLIPモデルパス基準の不整合は Phase 3 でも見送り | `--root` 基準のままで、`app_base_dir()` 基準の `paths.model` と不整合。通常運用では両者が同じフォルダのため顕在化しない。挙動変更を伴い単独の回帰確認が必要なため対象外とした |
| 2026-08-12 | プロファイルJSONの探索先と `paths.dataset`/`paths.model` の解決基準を分離した（`sport_profile._resolve_profiles_dir()` を新設） | PyInstaller 6.x onedir 配布では `datas` が `_internal/`（`_MEIPASS`）に入るが、exe は配布ルート直下にある。両者を同じ `root`（`app_base_dir()`）で扱っていたため凍結ビルドが `profiles/` を見つけられず起動不能になっていた。dataset/model は書き込み領域のため `_MEIPASS` 基準にはできず、探索ロジックのみ分離した |

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

## Phase 3 実装ログ（2026-08-12）

- 変更: `sport_profile.py`
  - `list_profiles_lenient(root)` を新設（3-0）。既存の `list_profiles()` は無変更。
    読めたプロファイルは `list[SportProfile]`、読めなかったものは `[(ファイル名, エラー理由), ...]`
    として分けて返す。例外は送出しない。
  - `find_residual_artifacts(root)` を新設。混在ガード対象（review/frames・selected_frames・
    logs/ai_scores.csv・logs/ai_segments.csv）に中身が残っているかを、`active_profile.json` の
    内容とは無関係に判定する（GUIの切り替え確認ダイアログ用）。
    `check_profile_switch()` 内の重複ロジックもこの関数を呼ぶよう整理した（判定基準の一本化）。
- 変更: `frame_scorer_launcher.py`
  - `build_predict_command(root, sport=None, extra=None)` に変更。`sport` を渡すと
    `extra_args` の先頭に `--sport <sport>` を追加してから凍結時／非凍結時の分岐に入るため、
    両分岐で漏れなく反映される（3-3）。
- 変更: `frame_selector.py`
  - `FrameSelectorWindow.__init__` に `get_predict_extra_args: Callable[[], list[str]] | None` を追加。
    「AI採点を実行」ボタン（`_ai_scoring_worker`）が `build_predict_command()` を呼ぶ際、
    このコールバックの戻り値を `extra` として渡すようにした。
    **仕様書に明記は無いが自分の判断で追加した変更**: このビューアは `kite_clipper_gui.py` とは
    独立した `subprocess.run(build_predict_command(self.root_dir))` 呼び出しを持っており、
    そのままでは常に `--sport` なし（＝kite扱い）でAI採点していた。つまり windsurf/wing で
    フレーム選別ビューアからAI採点すると、常にkiteモデルで誤って採点する
    （バグが表面化しない静かな誤判定）事故が起きる状態だった。3-3の趣旨
    「GUIがkite_clipper.pyを呼ぶ経路すべてに--sportを付与する」に当てはまると判断し、
    このファイルも合わせて修正した。混在ガードに引っかかった場合はCLI側（frame_scorer.py）の
    `ProfileSwitchGuardError` メッセージがそのまま `messagebox.showerror` に表示される
    （3-1のような3択ダイアログはこのビューアには実装していない。理由は後述）。
- 変更: `kite_clipper_gui.py`
  - `import sport_profile` を追加。`FRAME_SCORER_MODEL_PATH` 定数を削除し、
    `GUI_SETTINGS_FILE = ROOT / "logs" / "gui_settings.json"` を追加。
  - `load_gui_settings()` / `save_gui_settings()` を新設（壊れたJSON・書き込み失敗時も例外を
    投げず、GUIの動作を止めない）。
  - `__init__` で `sport_profile.list_profiles_lenient(ROOT)` を呼び、`self.sport_profiles`
    （id→SportProfile辞書）・`self.sport_ids_ordered` を構築。`logs/gui_settings.json` の
    `sport_id` を読み、存在すれば復元、存在しない/プロファイル自体が0件なら
    既定(kite、無ければ先頭、それも無ければNone)にフォールバックしてログに警告する。
    ログ欄がまだ無い起動シーケンスの都合上、警告は `create_widgets()` の後にまとめて出力する。
  - `_build_stepper` の手前（ヘッダーとステッパーの間）に競技ドロップダウン（`ttk.Combobox`,
    `state="readonly"`）を追加。表示名⇔sport_idの対応は `_refresh_sport_combo_values()` で管理。
  - 混在ガード事前チェック `_guarded(sport_id, run_after)` を新設。
    `sport_profile.check_profile_switch()` を直接呼び出し、`ProfileSwitchGuardError` を
    捕捉したら `run_cli` を呼ばずに `_show_guard_dialog()`（整理してから切り替える／
    そのまま続行する／キャンセルの3択）を表示する。CLI側のガードロジックをそのまま
    in-processで再利用しているため、GUIとCLIの判定基準が乖離しない。
  - `run_cli()` / `run_frame_scorer_predict()` は、まず `_guarded()` で事前チェックしてから
    実行するように変更。実際のコマンド組み立て（`--sport` / `--allow-profile-switch` の自動付与）は
    `_run_cli_with_sport()` / `_run_frame_scorer_predict_now()` に分離。
    「整理してから切り替える」を選んだ際の内部コマンド（後述）は、二重にガードチェックしない
    よう `_run_cli_raw()`（ガード無し・sport手組み立て済み）経由で実行する。
  - `_show_guard_dialog()` の「整理してから切り替える」は `_cleanup_then()` から3段階で実行:
    1) `--sport <id> --cleanup-review --confirm-delete`（review/frames・review/photosを削除）
    2) `--sport <id> --allow-profile-switch --cleanup-after-cut --confirm-delete
       --include-review-frames --include-review-photos`（selected_framesを削除。
       cleanup-after-cut自体もkite_clipper.py側の混在ガード対象コマンドのため、
       整理目的であることを明示して `--allow-profile-switch` を付けている）
    3) `logs/ai_scores.csv` / `logs/ai_segments.csv` をGUI側で直接削除
       （**仕様書に明記は無く自分で判断した点**: 既存のCLIコマンド
       `--cleanup-review` / `--cleanup-after-cut` はどちらもこの2つのCSVを削除対象にしていない
       ため、CLIコマンドの組み合わせだけでは混在ガードの対象を完全にクリアできない。
       kite_clipper.py 自体には手を入れず、単純なログCSVの削除としてGUI側で直接処理した）
    3段階すべて成功した後に `run_after()`（切り替え確定 or 元々実行しようとしていたコマンド）を呼ぶ。
  - 「そのまま続行する」を選ぶと `self.allow_profile_switch = True` を設定し、以後の
    `run_cli` / `run_frame_scorer_predict` すべてに `--allow-profile-switch` が自動付与される
    （切り替え時だけでなく、セッション中ずっと有効。次に「整理してから切り替える」を選ぶか
    アプリを再起動するまで維持する設計とした。仕様書に明記は無い判断）。
  - `set_busy()` で `self.sport_combo` の state を busy に応じて disabled/readonly に切り替え。
  - `current_model_path()` を新設し、`FRAME_SCORER_MODEL_PATH` の参照箇所（おまかせモードの
    未学習チェック、旧 `:1369`）を置き換え。プロファイルが1件も読めなかった極端なケースのみ
    旧来の `ROOT/"models"/"frame_scorer.pkl"` にフォールバックする。
    警告メッセージに `current_sport_display_name()` で競技名を含めるようにした。
  - `open_frame_selector()` で `FrameSelectorWindow` に `get_predict_extra_args=
    self.current_predict_extra_args` を渡すよう変更。
- 変更: `kite_clipper_gui.spec`
  - `datas` に `('profiles', 'profiles')` を追加（凍結時、`app_base_dir()` が指す exe と同じ場所に
    `profiles/*.json` が展開されるようにするため）。`hiddenimports` に `sport_profile` を追加。
- 変更: `README.md` / `WORKFLOW.md`
  - 「競技（スポーツプロファイル）の切り替え」節を追加し、CLIの `--sport` とGUIのドロップダウン
    両方の使い方、教師データ・モデルが競技ごとに分かれることを明記。

### 検証結果

いずれもユーザーの実プロジェクトの `input/` `review/` `selected_*` `logs/` `dataset/` `models/`
は変更していない。スクラッチパッド上の隔離ワークスペース（`baseline_ws` はPhase 1のものを再利用、
`gui_conflict_ws` はPhase 2セッションが用意していた「wingで実行済み・review/framesに13件残存・
active_profile.json が wing」の状態のものをそのまま利用）で検証した。

- **V-1（回帰、最重要）**: `baseline_ws` の中間生成物を `shutil.rmtree` で削除後、
  `kite_clipper.py --root <baseline_ws>`（`--sport` なし）と `--sport kite` 明示の両方を実行。
  いずれも `logs/result.csv` の SHA256 が `278b985ae7547fcf22d6ee89ca2b83ac91d8c18af086a4a45bd0c048eea0d254`
  と一致し、`multi_sport/baseline/result_before.csv` との `diff` はゼロだった。
  Phase 3 では `kite_clipper.py` 自体に変更を加えていないため、この結果は妥当。
- **中心課題の解消（GUIウィンドウは開けなかったため、ロジックを直接呼んで検証）**:
  1. 症状の再現: `gui_conflict_ws`（active_profile.json=wing、review/framesに13件）に対し、
     旧GUI相当のコマンド `kite_clipper.py --root <ws> --frame-interval 1`（`--sport` なし）を
     実行 → 終了コード **2** で停止することを確認（バグの再現）。
  2. 新GUIの事前ガード（`_guarded()` が内部で呼ぶ関数）を直接実行:
     `sport_profile.check_profile_switch(root, "kite", False)` が `ProfileSwitchGuardError` を
     送出し、`find_residual_artifacts(root)` が `['review/frames に 13 件の未処理データが...']`
     を返すことを確認。GUIはこの時点で `run_cli` を呼ばずに確認ダイアログを出すため、
     謎の終了コード2には到達しない。
  3. 「そのまま続行する」相当: `kite_clipper.py --root <ws> --sport kite --allow-profile-switch
     --frame-interval 1` → 終了コード0で成功、`active_profile.json` が `kite` に更新されることを確認。
  4. 「整理してから切り替える」相当: `gui_conflict_ws` を wing 状態に戻した上で、
     `_cleanup_then()` が発行する2つのCLIコマンド
     （`--cleanup-review --confirm-delete` → `--allow-profile-switch --cleanup-after-cut
     --confirm-delete --include-review-frames --include-review-photos`）をどちらも実行し、
     両方とも終了コード0で成功。review/frames・selected_frames が空になることを確認。
     その後 `check_profile_switch(root, "kite", False)` を再度呼び、例外を送出しない
     （ガードが解消している）ことを確認した。
  5. 「キャンセル」相当は分岐が単純（`dialog.destroy()` と表示を戻すだけ）なため、
     コードレビューでの確認に留めた。
- **`list_profiles_lenient()` の堅牢化**: スクラッチパッド上に `profiles/` を複製し、
  壊れたJSON（`broken.json`）と必須キー欠落のJSON（`ghost.json`）を追加して実行。
  例外を送出せず、`kite`/`windsurf`/`wing` の3件は正常に読み込み、2件はエラーとして
  分けて返すことを確認した（検証用の壊れたJSONは実プロジェクトの `profiles/` には置いていない）。
- **`gui_settings.json` の保存・復元とフォールバック**: `load_gui_settings()` /
  `save_gui_settings()` を `GUI_SETTINGS_FILE` をスクラッチパッドへ差し替えて実行し、
  初回（未作成）→空dict、保存→復元、壊れたJSON→例外を投げず空dict、をそれぞれ確認。
  また `__init__` 内の復元ロジック（保存sport_idがプロファイル一覧に無い場合、
  既定kiteへフォールバックして警告を1件生成する分岐）を同等のロジックとして単体実行し、
  期待どおりの挙動を確認した。
- **`build_predict_command()` の `--sport` 付与**: 非凍結時（`sport="windsurf"`,
  `extra=["--allow-profile-switch"]`）と、`sys.frozen=True` をモンキーパッチした凍結時
  （`sport="wing"`）の両方でコマンド列に `--sport <id>` が含まれることを確認した。
- **できなかった検証（正直に報告）**:
  - **V-5（GUI操作での目視確認）**: 実施できなかった。本環境で `tkinter.Tk()` の生成自体は
    成功する（ディスプレイは利用可能）ことを確認したが、実際に `KiteClipperGui()` を
    インスタンス化すると `ROOT`（実プロジェクトのフォルダ）に対して `ensure_dirs()` や
    `gui_settings.json` の書き込みが走ってしまい、「ユーザーの作業フォルダを変更しない」
    制約に抵触するため、意図的に実行しなかった。上記の通り、内部ロジックは関数単位で
    個別に検証した。
  - **V-6（PyInstallerビルド後のexeでの確認）**: 実施できなかった。本環境に PyInstaller が
    インストールされていない（`pip install` の事前確認が必要なため、無許可でのインストールは
    行わなかった）。`kite_clipper_gui.spec` の `datas` 追加はPyInstallerの一般的な
    `(ソースフォルダ, 配置先フォルダ)` タプル記法に沿っているが、実ビルドでの動作は未確認。

### 仕様書に記載が無く自分で判断した点（まとめ）

1. `frame_selector.py`（フレーム選別ビューアの「AI採点を実行」）にも `--sport` を伝搬した。
   3-3の対象として明記されていたのは `frame_scorer_launcher.py` と GUI 本体の `run_cli` 系
   だったが、このビューアも独立に `build_predict_command()` を呼んでおり、そのままでは
   静かな誤判定（kite以外の競技をkiteモデルで採点）が残ってしまうため、範囲に含めた。
2. GUIの3択ダイアログの「整理してから切り替える」で、`logs/ai_scores.csv` /
   `logs/ai_segments.csv` をGUI側で直接削除する処理を追加した。既存のCLIコマンドの
   組み合わせだけでは混在ガードの対象を完全にクリアできないと判断したため
   （`kite_clipper.py` 自体には手を入れていない）。
3. 「そのまま続行する」で設定する `self.allow_profile_switch` は、次に競技を切り替えて
   「整理してから切り替える」を選ぶかアプリを再起動するまで、セッション内で維持する設計にした。
4. `frame_selector.py` 内でガードに引っかかった場合は、`kite_clipper_gui.py` と同じ3択
   ダイアログは実装せず、CLI側のエラーメッセージをそのまま `messagebox.showerror` に表示する
   挙動に留めた（フレーム選別ビューアは `kite_clipper_gui.py` に依存しない独立ウィンドウとして
   設計されており、3択ダイアログのロジックを丸ごと複製するのは差分過大と判断した）。
   実用上、通常のワークフローでは工程2（フレーム生成）の時点で `run_cli` 側のガードが
   既に働くため、ビューアを開いた時点で競技の食い違いが残っているケースは稀である。

## Phase 3 追補: PyInstaller onedir 配布での `profiles/` 不在バグ修正（2026-08-12）

### バグの内容

- `app_base_dir()`（`kite_clipper.py` / `kite_clipper_gui.py` / `frame_scorer.py` /
  `record_labels.py` に同一実装が複製されている）は凍結時に
  `Path(sys.executable).resolve().parent` を返す。onedir 配布ではこれは
  `dist/kite_clipper_gui/`（exe が置かれる場所）になる。
- 一方 PyInstaller 6.x の onedir ビルドは `datas` を **`_internal/` サブフォルダ**に配置する。
  `kite_clipper_gui.spec` の `profile_datas = [('profiles', 'profiles')]` は
  `dist/kite_clipper_gui/_internal/profiles/` に展開されるが、`sport_profile.load_profile()` は
  （`root` = `app_base_dir()` を渡していたため）`dist/kite_clipper_gui/profiles/` を探しに行き、
  `ProfileError` で起動不能になっていた。
- Phase 3 実装ログの V-6（PyInstallerビルド後のexeでの確認、未実施）で見落とされていた不具合。

### 修正内容

`sport_profile.py` に「プロファイルJSONの探索先」を、「`paths.dataset` / `paths.model` の解決基準
（`root` = `app_base_dir()`、従来どおり）」から分離した。

- `_profiles_dir_candidates(root)`: 凍結時は `sys._MEIPASS`（onedir では `_internal/` を指す）配下の
  `profiles/` を最優先候補にし、次点で `sys.executable` と同じ場所の `profiles/`
  （手動で exe 横に配置し直した場合の救済）を候補にする。非凍結時は従来どおり `root/profiles/` のみ。
- `_resolve_profiles_dir(root)`: 候補を順に確認し、最初に存在するディレクトリを返す。
  すべて存在しない場合は、探索した候補パスを**すべて列挙**した `ProfileError` を送出する
  （原因調査しやすくするため）。
- `load_profile()` / `list_profiles()` / `list_profiles_lenient()` はいずれもこの解決ロジックを通す。
  `load_profile(root, sport_id)` の**シグネチャは変更していない**。`paths.dataset` /
  `paths.model` の解決（`Path(root) / paths["dataset"]` 等）は一切変更していない
  （`_MEIPASS` は読み取り専用の展開先であり、モデル保存先として不正なため）。
- `kite_clipper_gui.spec` のコメントを「exe と同じ場所に同梱される」という誤った説明から、
  実際の展開先（`_internal/profiles/`、すなわち `_MEIPASS` 基準）に合わせて修正した。
  `datas` の指定自体（`profile_datas = [('profiles', 'profiles')]`）は変更していない。

### 検証結果

- **V-1（baseline回帰）**: `scratchpad/baseline_ws` の中間生成物（`final/` `logs/` `review/`
  `selected_clips/` `selected_frames/` `work/`、`input/` は保持）を `shutil.rmtree` で削除後、
  `.venv/Scripts/python.exe kite_clipper.py --root <baseline_ws>` を実行。生成された
  `logs/result.csv` の SHA256 は `278b985ae7547fcf22d6ee89ca2b83ac91d8c18af086a4a45bd0c048eea0d254` で、
  期待値と完全一致（差分ゼロ）。
- **V-2（凍結時探索ロジックの単体検証）**: `sys.frozen` / `sys._MEIPASS` / `sys.executable` を
  モンキーパッチし、スクラッチパッドに擬似配布レイアウトを作成して3ケースを確認した。
  1. `_MEIPASS/profiles/kite.json` がある場合、そちらを最優先で読み込み、かつ
     `paths.model` / `paths.dataset` は `root`（exe の場所）基準で解決され、`_MEIPASS` 配下には
     ならないこと（dataset/model の解決基準を変えていないことの直接確認）。
  2. `_MEIPASS/profiles/` が無く、exe と同じ場所の `profiles/` にはある場合、フォールバックで
     読み込めること。
  3. どちらにも無い場合、`ProfileError` のメッセージに両方の候補パスが列挙されること。
  3ケースとも期待どおりの結果。
- **V-3（非凍結時の回帰）**: 非凍結の通常実行で `list_profiles_lenient()` が
  `kite` / `windsurf` / `wing` の3件をエラーなしで返し、`load_profile(root, "kite")` の
  `dataset_path` / `model_path` が従来どおりリポジトリ直下の `dataset/labels.csv` /
  `models/frame_scorer.pkl` に解決されることを確認。

### 実際の PyInstaller ビルドによる確認（未実施・要ユーザー確認）

- 指示によりビルド自体は実施していない。ユーザーが手元で `pyinstaller kite_clipper_gui.spec` を
  実行し、`dist/kite_clipper_gui/kite_clipper_gui.exe` の起動でプロファイル一覧
  （kite / windsurf / wing）が表示されることを確認する必要がある。
- あわせて、`dist/kite_clipper_gui/_internal/profiles/` に3つの JSON が実際に展開されているか
  （PyInstaller のバージョンによって `_internal` の有無や名称が変わる可能性があるため）も
  合わせて目視確認することを推奨する。

## 未確定事項

MULTI_SPORT_SPEC.md 8章と同期。決まり次第、決定ログへ移す。

1. windsurf / wing の enter/exit/split_gap の適正値 → 4-4 / 4-8 で確定
2. フレーム抽出間隔（wing で1.0秒が粗すぎるか） → 4-8 で判断
3. 作業フォルダの競技別分離の要否 → Phase 3 まで運用してから再検討
4. `has_kite_candidate` 列名の汎用化 → 本タスクでは実施しない
5. GUIの `frame_selector.py`（フレーム選別ビューア）に、メインウィンドウと同じ3択の
   混在ガード確認ダイアログを実装するかどうか → 現状はCLIエラーメッセージの表示のみ。
   実運用で不便が出るようなら別タスクで検討する（Phase 3 実装ログ参照）
