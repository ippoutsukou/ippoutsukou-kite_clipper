# マルチスポーツ対応 仕様書

kite_clipper を「カイトボード専用アプリ」から「スポーツプロファイル切り替え型アプリ」へ拡張するための仕様です。
対象スポーツは カイトボード（既存）／ウィンドサーフィン／ウィングフォイル。

- 作成日: 2026-08-12
- 対象コミット: 8c9fe6b 時点のコードベース
- 方式: **同一コードベース・同一アプリ内でのプロファイル切り替え**（アプリの fork はしない）

---

## 1. 要件定義

### 1.1 目的

1本のアプリで、複数のウォータースポーツ映像から「良いシーン」を自動抽出できるようにする。
競技ごとに異なるのは「教師データと少数のパラメータ」だけであり、パイプライン本体は共通化する。

### 1.2 対象（スコープ内）

| # | 項目 |
|---|---|
| R-1 | スポーツプロファイルの定義形式（JSON）と読み込み機構 |
| R-2 | 競技ごとの教師データ・学習済みモデルの分離 |
| R-3 | 競技固有の器材検出（カイト検出）のプロファイル化・無効化可能化 |
| R-4 | 競技ごとの区間提案パラメータ（enter/exit/split_gap 等）の分離 |
| R-5 | GUI からのプロファイル選択と、選択状態の永続化 |
| R-6 | プロファイル取り違えによる誤判定の防止（中間生成物の混在ガード） |
| R-7 | 既存カイト資産（labels.csv / frame_scorer.pkl / result.csv）の後方互換維持 |

### 1.3 対象外（スコープ外）

- 新しい競技固有CVアルゴリズムの開発（セイル検出・フォイル検出など）
  → 本仕様では windsurf / wing は器材検出を使わず CLIP + 教師データに委ねる
- モデルアーキテクチャの変更（RandomForest / LogisticRegression / CLIP ViT-B/32 は現状維持）
- 動画の結合・エンコード仕様の変更
- ネイティブアプリ（Expo）側の実装（ただしプロファイル定義はネイティブ側でも読める JSON にする）
- `segment_simulator.py` の変更（既存コメントで変更禁止と明記されているため触らない）

### 1.4 制約

| ID | 制約 | 理由 |
|---|---|---|
| C-1 | **`has_kite_candidate` というCSV列名は変更しない** | この文字列が `logs/result.csv` → `dataset/labels.csv` → pickle化モデルの特徴量順序 まで貫通している。改名すると既存のカイト用 labels.csv と学習済みモデルが無言で壊れる |
| C-2 | `FEATURE_NAMES` の要素数・順序を変えない | 既存 pkl との互換性 |
| C-3 | kite プロファイル選択時の出力は現行と完全一致させる | 回帰の有無を判定する唯一の基準 |
| C-4 | `frame_scorer.py` は `kite_clipper.py` / `record_labels.py` を import しない | 既存コメントで明示された設計方針。プロファイル読み込みも同方針に従い、独立した薄いモジュールを共有する |
| C-5 | 学習には動画2本以上・ラベル2種類以上が必須 | `cross_validate_variant()` が ValueError を送出する |

### 1.5 成功条件

1. `--sport kite` で実行した `logs/result.csv` が、変更前の出力とバイト一致する
2. `--sport windsurf` で、器材検出をスキップして最後まで通る（フレーム抽出→採点→区間提案→切り出し）
3. 既存の `models/frame_scorer.pkl` が移行後も kite プロファイルで読み込める
4. kite の中間生成物が残った状態で windsurf を実行しようとすると、処理前に停止して警告が出る
5. GUI でプロファイルを切り替えると、モデル未学習チェックが選択中の競技のモデルに対して行われる

---

## 2. 仕様

### 2.1 プロファイル定義

**配置**: `profiles/<sport_id>.json`（リポジトリにコミットする。教師データとモデルは従来どおり除外）

**探索基準**: `--root`（作業フォルダ）ではなく **`app_base_dir()`（アプリ本体の設置場所。PyInstaller配布時は exe と同じ場所）** を基準に探す。
`paths.dataset` / `paths.model` の解決基準も同じ。
作業フォルダは競技をまたいで移動しうるが、プロファイルはアプリに同梱される静的データであるため。
（Phase 1 実装時に判明した、当初の仕様に記載漏れがあった論点。2026-08-12 に上記で確定）

```json
{
  "schema_version": 1,
  "id": "windsurf",
  "display_name": "ウィンドサーフィン",
  "equipment_detector": "none",
  "equipment_detector_params": {},
  "cv": {
    "blur_threshold": 120.0,
    "dark_threshold": 70.0,
    "min_width": 1280,
    "min_height": 720,
    "similarity_threshold": 10,
    "frame_interval": 1.0
  },
  "segment": {
    "enter": 0.5,
    "exit": 0.4,
    "smooth_window": 3,
    "split_gap": 2.0,
    "pre_roll": 1.0,
    "post_roll": 1.0
  },
  "paths": {
    "dataset": "dataset/windsurf/labels.csv",
    "model": "models/windsurf/frame_scorer.pkl"
  }
}
```

**kite プロファイル（`profiles/kite.json`）は現行のハードコード値をそのまま書き写す**。

| キー | kite | windsurf | wing |
|---|---|---|---|
| `equipment_detector` | `"kite_hsv_upper"` | `"none"` | `"none"` |
| `segment.enter` / `exit` | 0.5 / 0.4 | 0.5 / 0.4（暫定） | 0.5 / 0.4（暫定） |
| `segment.split_gap` | 2.0 | 2.0（暫定） | 2.0（暫定） |
| `paths.dataset` | `dataset/labels.csv` | `dataset/windsurf/labels.csv` | `dataset/wing/labels.csv` |
| `paths.model` | `models/frame_scorer.pkl` | `models/windsurf/frame_scorer.pkl` | `models/wing/frame_scorer.pkl` |

> kite のパス2つは**現行のまま据え置く**（移行不要にするため）。windsurf/wing のしきい値はラベリング後に
> `segment_simulator.py` で実測して確定する。現時点では推測値を入れず、kite と同値を暫定値として明示する。

### 2.2 器材検出器の仕様

`equipment_detector` の取りうる値:

| 値 | 挙動 |
|---|---|
| `"kite_hsv_upper"` | 現行 `detect_kite_candidate()` と完全に同一。画像上部65%のHSV彩度70以上・明度80以上のブロブを探し、面積が画像の0.025%〜8%、幅12px以上・高さ8px以上、アスペクト比0.5〜8.0のものがあれば True |
| `"none"` | 常に `False` を返す。CV検出は行わない |

**windsurf / wing で `"none"` とする根拠**（現行アルゴリズムがそのままでは機能しないため）:

1. 探索範囲が画像上部65%に限定されている。ウィンドのセイル／ウィングはライダーと同じ高さにあり、範囲外に出る
2. ブロブ面積の上限が画像の8%。セイルは画面の数十%を占めるため確実に弾かれる

`"none"` の場合、`has_kite_candidate` 列は常に `False` になる。
`detect_empty_scene()` と `has_person_candidate`（HOG人物検出）は全競技共通で有効のまま。

> 注意: `PhotoAnalysis.score` は `has_person_candidate or has_kite_candidate` で subject_score 20点を与えている。
> windsurf/wing では人物検出のみが加点源になる。これはヒューリスティックスコアの話であり、
> AI採点（CLIP + 教師データ）には `heuristic_score` の1特徴量として入るだけなので、実用上の影響は小さい。

### 2.3 CLI インターフェース

全スクリプトに共通オプションを追加する。

```
--sport <id>    使用するスポーツプロファイルID（既定: kite）
```

| スクリプト | 追加箇所 | プロファイルから読む値 |
|---|---|---|
| `kite_clipper.py` | `parse_args()` | cv.* の既定値、equipment_detector、segment.split_gap / pre_roll / post_roll |
| `record_labels.py` | `parse_args()` | paths.dataset（出力先） |
| `frame_scorer.py train` | `train_parser` | paths.dataset（既定入力）、paths.model（既定出力） |
| `frame_scorer.py predict` | `predict_parser` | paths.model、segment.enter / exit / smooth_window |

**明示指定の優先順位**: コマンドラインで明示された値 > プロファイル値 > 従来のハードコード既定値。
つまり `--sport windsurf --enter 0.6` は 0.6 を使う。既存の `--blur-threshold` 等の使い方は壊れない。

### 2.4 プロファイル混在ガード（R-6）

作業フォルダ（`input/` `review/` `selected_frames/` `logs/` `work/`）は**全競技で共有**する。
そのため、前回の競技の中間生成物が残ったまま別競技を実行すると、
「カイトのフレームをウィンドサーフィンのモデルで採点する」事故が起きる。

**仕様**:

1. 実行成功時に `logs/active_profile.json` を書き出す
   ```json
   { "sport_id": "kite", "updated_at": "2026-08-12T10:00:00" }
   ```
2. 起動時、`logs/active_profile.json` の `sport_id` が今回の `--sport` と異なり、かつ
   `review/frames` / `selected_frames` / `logs/ai_scores.csv` / `logs/ai_segments.csv` の
   いずれかに中身がある場合、**処理を実行せず終了コード2で停止**し、次を表示する:
   ```
   前回は kite で実行しましたが、今回は windsurf が指定されています。
   review/frames に 412 件の未処理データが残っています。
   競技を切り替える前に、以下のいずれかを行ってください。
     - python kite_clipper.py --cleanup-review --confirm-delete で整理する
     - --allow-profile-switch を付けて、混在を承知のうえで続行する
   ```
3. `--allow-profile-switch` で明示的にバイパスできる
4. `input/videos` と `input/photos` は元データなのでガード対象外（削除提案もしない）

**CLIP埋め込みキャッシュ `work/embeddings` は競技間で共有してよい**。
キャッシュキーが「親フォルダ名 + ファイル名 + mtime」で、モデル（ViT-B/32）も競技非依存のため、
混在しても誤りは起きず、再計算の節約になる。

### 2.5 GUI 仕様

| 項目 | 内容 |
|---|---|
| 配置 | ウィンドウ上部（ステッパーの上）に「競技」ドロップダウンを追加 |
| 選択肢 | `profiles/*.json` を走査し `display_name` を表示 |
| 永続化 | `logs/gui_settings.json` に `sport_id` を保存し、次回起動時に復元 |
| CLI連携 | `run_cli()` / `build_predict_command()` の引数に `--sport <id>` を自動付与 |
| モデル存在チェック | 現行のハードコード `ROOT/"models"/"frame_scorer.pkl"` を、選択中プロファイルの `paths.model` に置き換える |
| 切り替え時の挙動 | 処理実行中（`self.busy`）は切り替え不可。切り替え時に中間生成物が残っていれば確認ダイアログを出す |
| おまかせモード | 未学習判定メッセージに競技名を含める（例:「ウィンドサーフィンのAI採点モデルが未学習です」） |

---

## 3. 技術スタック

現行から**追加する依存はゼロ**。標準ライブラリの `json` のみで実装できる。

- Python 3.x / OpenCV / NumPy / scikit-learn / onnxruntime / Tkinter（すべて現行のまま）
- プロファイル定義: JSON（ネイティブアプリ側からも読めるようコード埋め込みにしない）
- 配布: PyInstaller（`kite_clipper_gui.spec` の `datas` に `profiles/` を追加）

---

## 4. 設計

### 4.1 新規モジュール `sport_profile.py`

制約 C-4（`frame_scorer.py` は他スクリプトを import しない）に配慮し、
**依存を持たない末端モジュール**として新設する。`clip_embedder.py` と同じ立ち位置。

```python
DEFAULT_SPORT_ID = "kite"

@dataclass(frozen=True)
class SportProfile:
    id: str
    display_name: str
    equipment_detector: str
    equipment_detector_params: dict
    cv: dict
    segment: dict
    dataset_path: Path      # root からの絶対パスに解決済み
    model_path: Path

def load_profile(root: Path, sport_id: str) -> SportProfile: ...
def list_profiles(root: Path) -> list[SportProfile]: ...
def detect_equipment_candidate(image, profile) -> bool: ...   # ディスパッチのみ
```

`detect_equipment_candidate()` は検出ロジックを**持たない**。
`"kite_hsv_upper"` のときは呼び出し元が持つ既存関数を呼ぶ形にし、アルゴリズム本体は
`kite_clipper.py` の `detect_kite_candidate()` に置いたまま動かさない（差分を最小化するため）。

未知の `sport_id`、`profiles/` 不在、JSON パースエラーは、
推測でフォールバックせず**明示的なエラーで停止**する（rules/30 の「推測で埋めない」に従う）。

### 4.2 データフローの変化

```
                        ┌── profiles/<sport>.json ──┐
                        │                            │
input/videos ──► kite_clipper.py ──► review/frames + logs/result.csv
                  (equipment_detector で分岐)          │
                                                       ▼
                                        record_labels.py ──► dataset/<sport>/labels.csv
                                                       │
                                                       ▼
                                        frame_scorer.py train ──► models/<sport>/frame_scorer.pkl
                                                       │
                                                       ▼
                                        frame_scorer.py predict ──► logs/ai_scores.csv
                                                       │            logs/ai_segments.csv
                                                       ▼
                          kite_clipper.py --cut-from-ai-segments ──► selected_clips/
```

競技によって変わるのは **点線で囲われた3箇所（器材検出の有無・データセットのパス・モデルのパス）と数値パラメータだけ**。
CLIP埋め込み、フレーム抽出、区間化アルゴリズム、切り出し、GUI、ラベリングUIはすべて共通。

### 4.3 フォルダ構成（変更後）

```
profiles/
  kite.json            # 現行のハードコード値をそのまま記述
  windsurf.json
  wing.json
sport_profile.py       # 新規
dataset/
  labels.csv           # 既存（kite。移動しない）
  windsurf/labels.csv
  wing/labels.csv
models/
  frame_scorer.pkl     # 既存（kite。移動しない）
  windsurf/frame_scorer.pkl
  wing/frame_scorer.pkl
logs/
  active_profile.json  # 新規（混在ガード用）
  gui_settings.json    # 新規（GUIの選択状態）
```

`.gitignore` は `/dataset/` `/models/` を既に除外済みのため、サブフォルダも自動的に対象。
`profiles/` は除外対象に含まれないのでそのままコミットされる（追記不要）。

### 4.4 命名についての判断

内部の概念名は「器材候補（equipment candidate）」に汎用化するが、
**シリアライズされる文字列 `has_kite_candidate` は据え置く**（制約 C-1）。

| レイヤ | 名前 |
|---|---|
| CSV列名（result.csv / labels.csv） | `has_kite_candidate` ← **変更しない** |
| `FEATURE_NAMES` の要素 | `has_kite_candidate` ← **変更しない** |
| `PhotoAnalysis` のフィールド | `has_kite_candidate` ← 変更しない（列名生成と直結しているため） |
| プロファイルのキー | `equipment_detector` |
| 新規関数名 | `detect_equipment_candidate()` |

列名の汎用化（例 `has_equipment_candidate` への改名）は、
labels.csv のスキーマバージョニングと移行スクリプトを伴う**別タスク**とする。
本タスクでは行わない。各定義箇所に「競技固有の器材候補フラグ。互換性のため列名は kite のまま」とコメントを残す。

---

## 5. タスクリスト

### Phase 1: MVP（プロファイル機構と kite の等価性確保）

| # | タスク | 変更ファイル |
|---|---|---|
| 1-1 | `sport_profile.py` を新規作成（dataclass + ローダ + 一覧） | 新規 |
| 1-2 | `profiles/kite.json` を作成。現行ハードコード値を転記 | 新規 |
| 1-3 | `kite_clipper.py` に `--sport` を追加し、cv/segment の既定値をプロファイル由来にする | kite_clipper.py |
| 1-4 | `detect_kite_candidate()` の呼び出しを `equipment_detector` による分岐に変更 | kite_clipper.py:342 |
| 1-5 | **等価性確認**: 変更前後で `logs/result.csv` がバイト一致することを確認 | — |

### Phase 2: 競技追加

| # | タスク | 変更ファイル |
|---|---|---|
| 2-1 | `profiles/windsurf.json` / `profiles/wing.json` を作成（detector は `"none"`） | 新規 |
| 2-2 | `record_labels.py` に `--sport` を追加し、出力先を `paths.dataset` に | record_labels.py |
| 2-3 | `frame_scorer.py train/predict` に `--sport` を追加 | frame_scorer.py |
| 2-4 | 混在ガード（`logs/active_profile.json` + `--allow-profile-switch`）を実装 | kite_clipper.py, frame_scorer.py |

### Phase 3: GUI・配布

| # | タスク | 変更ファイル |
|---|---|---|
| 3-1 | GUI に競技ドロップダウンを追加、`logs/gui_settings.json` で永続化 | kite_clipper_gui.py |
| 3-2 | `FRAME_SCORER_MODEL_PATH` のハードコードを廃し、選択中プロファイル参照に | kite_clipper_gui.py:46, :1369 |
| 3-3 | `build_predict_command()` に `--sport` を渡す | frame_scorer_launcher.py |
| 3-4 | `kite_clipper_gui.spec` の `datas` に `profiles/` を追加 | kite_clipper_gui.spec |
| 3-5 | README / WORKFLOW.md に競技切り替え手順を追記 | README.md, WORKFLOW.md |

### Phase 4: 実データ運用（コード変更なし）

| # | タスク |
|---|---|
| 4-1 | windsurf の動画を撮影・収集し、フレーム抽出とラベリング（→ 6章の目安を参照） |
| 4-2 | `frame_scorer.py train --sport windsurf` を実行し、PR-AUC を確認 |
| 4-3 | `segment_simulator.py` で enter/exit/split_gap を実測し、`profiles/windsurf.json` の暫定値を確定値に更新 |
| 4-4 | wing について 4-1〜4-3 を繰り返す |

---

## 6. 必要データ量の目安

### 6.1 前提となる仕組み

- フレーム抽出は既定 **1.0秒間隔** → 10分の動画から約600フレーム
- 評価は **GroupKFold（動画単位）**。同じ動画のフレームが学習と評価に分かれないよう分割される
- fold数は `min(5, 動画本数)`。**動画2本未満、またはラベルが1種類だけだと ValueError で学習不能**
- 学習時、採用フレーム（label=1）の前後 **±1.5秒以内の非採用フレームは除外**される（`--ambiguous-sec`）
- 分類器は `class_weight="balanced"` かつ `C=0.1` の強めの正則化 → **少ない正例でも動く設計**

### 6.2 効くのは総枚数ではなく「正例数」と「動画本数」

CLIP埋め込み512次元に対して強い正則化をかけているため、
総ラベル数よりも **正例（良いシーン）の数** と **撮影条件のバリエーション** が精度を決める。

| 段階 | 動画本数 | 総ラベル | うち正例 | 期待できること |
|---|---|---|---|---|
| **最小（動作確認）** | 5〜6本 | 600〜1,000枚 | 80〜150枚 | 学習は通り fold=5 も成立する。ただし PR-AUC のブレが大きく、実運用の判断材料にはならない |
| **実用ライン** | 15〜25本 | 2,000〜4,000枚 | 300〜600枚 | おまかせモードが「そこそこ使える」水準。取りこぼしはあるが手動レビューの下地になる |
| **安定** | 30本以上 | 5,000枚以上 | 1,000枚以上 | しきい値調整が効くようになり、`segment_simulator.py` での最適化が意味を持つ |

### 6.3 実際の作業量イメージ（実用ラインの場合）

```
10分の動画 20本 = 約12,000フレーム抽出
  ↓ ヒューリスティックのNG判定（ブレ・暗さ・低解像度・空シーン）で足切り
  ↓ 実際にラベリング画面に出るのは 2,000〜4,000枚程度
  ↓ 1枚あたり1〜2秒で判断
ラベリング実作業: 1.5〜3時間程度
```

正例率は競技によるが、走っている時間のうち「技が決まっている瞬間」は限られるため、
**全フレームの5〜15%程度が正例**になるのが典型。
上表の正例数は、この比率から逆算している。

### 6.4 動画の集め方（枚数より重要）

同一セッションから20本集めるより、**5セッションから4本ずつ**のほうが効く。
分散させたい軸は次のとおり:

- 天候・時間帯（曇天／順光／逆光／夕方）
- 水面状態（フラット／チョッピー／波あり）
- カメラ位置と距離（岸から望遠／並走／ヘルメットマウント）
- ライダーの服装・ウェットスーツの色（CLIPが色に引っ張られるのを防ぐ）

GroupKFold は動画単位で分割するため、**同一セッションばかりだと交差検証のスコアが楽観的に出る**。
PR-AUC が高いのに実運用で外す場合、まずここを疑う。

### 6.5 wing / windsurf 固有の注意

- **器材検出を使わない**ぶん、カイトより教師データへの依存度が上がる。6.2 の下限は守ったほうがよい
- ウィンドサーフィンのジャイブは3〜8秒と長く、カイトのジャンプ（2〜4秒）より区間が伸びる
  → `split_gap` / `pre_roll` / `post_roll` は 4-3 で必ず実測して調整する
- 1.0秒間隔のフレーム抽出だと、ウィングの短いジャンプは1〜2フレームしか捉えられない可能性がある
  → 正例が集まりにくいと感じたら `--frame-interval 0.5` を試し、プロファイルに反映する

---

## 7. 確認方法

| # | 確認内容 | 手順 |
|---|---|---|
| V-1 | kite の完全等価性 | Phase 1 の前後で同一 input を処理し、`logs/result.csv` を比較して差分ゼロを確認 |
| V-2 | 既存モデルの読み込み | 移行後に `frame_scorer.py predict --sport kite` が既存 pkl で成功することを確認 |
| V-3 | windsurf の通し実行 | 未学習状態で `--sport windsurf` を実行し、フレーム抽出まで正常終了することを確認 |
| V-4 | 混在ガード | kite 実行後に review/frames を残したまま `--sport windsurf` を実行し、終了コード2で停止することを確認 |
| V-5 | GUI 切り替え | 競技を切り替えてアプリを再起動し、選択が復元されることを確認 |
| V-6 | 配布ビルド | PyInstaller ビルド後の exe で `profiles/` が同梱され、競技一覧が表示されることを確認 |

---

## 8. 未確定事項

推測で埋めず、決まっていないものとして明記する。

1. **windsurf / wing の enter/exit/split_gap の適正値** — 教師データ収集後に `segment_simulator.py` で実測して決める。それまでは kite と同値を暫定値として置く
2. **フレーム抽出間隔** — wing で1.0秒が粗すぎるかは実データで検証する（6.5参照）
3. **作業フォルダの競技別分離の要否** — Phase 1〜3 では共有＋ガード方式とする。運用してみて競技を頻繁に行き来するようなら、`workspaces/<sport>/` への分離を別タスクで検討する（全パス定数に手が入るため、regression リスクとのトレードオフ）
4. **`has_kite_candidate` 列名の汎用化** — 本タスクでは行わない。スキーマバージョニングと移行スクリプトを伴う別タスク
