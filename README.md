# Media Clip Organizer

## 新UIの標準フロー（動画）

GUIでは、動画を選択するとサムネイルを生成し、利用可能な学習済みモデルで実際にAI採点して区間候補を表示します。候補を確認・除外・範囲調整した後、個別または結合して保存します。音声付きプレビュー、任意タグ、区間ごとの開始・代表・終了サムネイルに対応し、作業状態は `out/sessions/` に自動保存されます。

学習済みモデルが無い場合はAI候補を作らず、手動で区間を追加できます。元動画は参照のみで変更しません。再生は同梱FFmpegとwaveOutを使用します。

以下のCLI手順は旧来の補助ツール向けです。新UIの標準フローと混同しないでください。


Windows向けの動画専用クリップ作成ツールです。標準GUIは動画選択から自動解析、候補確認、範囲調整、プレビュー、個別・結合保存までを扱います。写真整理と旧フレーム選別は旧CLI・内部互換のために残していますが、標準GUIの導線では使用しません。

## フォルダ構成

```text
input/
  photos/        # 旧CLI互換（標準GUIでは使用しない）
  videos/        # 元動画を入れる
review/
  photos/        # 旧CLI互換（標準GUIでは使用しない）
  frames/        # 内部解析・旧CLI用フレーム
logs/
  result.csv     # 判定結果
  error.log      # エラー記録
```

元ファイルは削除・移動せず、コピーと新規出力だけを行います。

## セットアップ

ffmpeg はインストール済みで、`ffmpeg` コマンドが PowerShell から実行できる前提です。

```powershell
pip install -r requirements.txt
```

## 実行

```powershell
python .\kite_clipper.py
```

しきい値を変えたい場合:

```powershell
python .\kite_clipper.py --blur-threshold 120 --dark-threshold 70 --min-width 1280 --min-height 720 --similarity-threshold 10
```

## 判定内容

- ブレ判定: グレースケール画像に Laplacian をかけ、分散が低いものをブレ候補にします。
- 暗さ判定: 平均輝度がしきい値未満のものを暗い候補にします。
- 解像度判定: 幅・高さが指定値未満のものを低解像度候補にします。
- 類似グループ化: 平均ハッシュのハミング距離で近い写真を同じグループにします。
- 採用候補: シャープさ、明るさ、解像度をスコア化し、各グループで最高スコアの写真を `review/photos` にコピーします。

## 出力 CSV

`logs/result.csv` には、写真ごとの判定結果、選択有無、コピー先、動画フレームの抽出結果、エラー行が保存されます。

## 競技（スポーツプロファイル）の切り替え

このアプリはカイトボード専用ではなく、`profiles/*.json` で定義した競技プロファイルを
切り替えて使えます（現時点で対応: カイトボード / ウィンドサーフィン / ウィングフォイル）。

競技によって変わるのは器材検出の有無・区間提案パラメータ・教師データとモデルの保存先だけで、
パイプライン本体（フレーム抽出・CLIP採点・切り出し・結合・GUI）はすべて共通です。
教師データとモデルは競技ごとに `dataset/<sport_id>/labels.csv` / `models/<sport_id>/frame_scorer.pkl`
に分かれます（カイトボードのみ後方互換のため従来どおり `dataset/labels.csv` / `models/frame_scorer.pkl`）。

### CLI での切り替え

各スクリプトに `--sport <id>` を指定します（未指定時は既定の `kite`）。

```powershell
python .\kite_clipper.py --sport windsurf
python .\record_labels.py --sport windsurf
python .\frame_scorer.py train --sport windsurf
python .\frame_scorer.py predict --sport windsurf
```

前回と異なる競技の中間生成物（`review/frames` / `selected_frames` / `logs/ai_scores.csv` /
`logs/ai_segments.csv`）が残ったまま別競技を実行しようとすると、誤判定を防ぐため
処理前に停止します（終了コード2）。表示される案内に従い、
`python kite_clipper.py --cleanup-review --confirm-delete` で整理するか、
`--allow-profile-switch` を付けて混在を承知のうえで続行してください。

### GUI での切り替え

GUI 上部（ステッパーの上）の「競技」ドロップダウンから選べます。選択状態は
`logs/gui_settings.json` に保存され、次回起動時に復元されます。処理実行中は切り替えできません。
中間生成物が残った状態で切り替えようとすると確認ダイアログが出て、
「整理してから切り替える」「そのまま続行する」「キャンセル」のいずれかを選べます。
## FFmpeg の同梱配布

このアプリは `vendor/ffmpeg/windows-x64/ffmpeg.exe` と
`vendor/ffmpeg/windows-x64/ffprobe.exe` が存在する場合、それらを優先して使います。
存在しない場合は、従来どおり PATH 上の `ffmpeg` / `ffprobe` を使います。

配布時は LGPL 版 FFmpeg のみを同梱してください。GPL 版または nonfree 版は同梱しないでください。

配布前に次を確認します。

```powershell
.\vendor\ffmpeg\windows-x64\ffmpeg.exe -version
.\vendor\ffmpeg\windows-x64\ffprobe.exe -version
```

表示される configure 行に、次の文字列が含まれていないことを確認してください。

```text
--enable-gpl
--enable-nonfree
```

同梱する場合は、FFmpeg のライセンス文、ソース入手先、ビルド情報を
`vendor/ffmpeg/windows-x64/` に残してください。

## 配布版（インストール不要の実行ファイル）

Python をインストールしていない Windows PC でも使えるように、GUI・CLI を
実行ファイル化した配布パッケージを GitHub Releases で公開しています。

- ダウンロード: [Releases ページ](https://github.com/ippoutsukou/ippoutsukou-kite_clipper/releases) から
  `kite_clipper_release_YYYYMMDD.zip` を取得してください。
- 展開後、`kite_clipper_gui.exe` をダブルクリックすると GUI が起動します。
- コード署名を行っていないため、初回起動時に Windows Defender
  SmartScreen が「発行元不明」の警告を表示することがあります。
  内容を確認のうえ「詳細情報」→「実行」を選んでください。

### 同梱パッケージの構成

```text
kite_clipper_gui.exe   # GUI 本体（AI 採点処理の frame_scorer.py を内蔵）
kite_clipper.exe       # CLI 本体（GUI から内部的に呼び出されます）
vendor/ffmpeg/         # 同梱 FFmpeg（LGPL 版）
vendor/clip/           # CLIP 画像特徴量モデル（ONNX）
models/                # AI スコアリング用学習済みモデル（競技ごとのサブフォルダを含む）
input/ logs/ work/ ...  # 実行時に使う作業用フォルダ（初期状態は空）
```

スポーツプロファイル（`profiles/*.json`）は両方の exe に内蔵されるため、
配布フォルダに個別に置く必要はありません（GUI は `_internal/profiles/`、
CLI は exe 内部に展開されます）。

### 配布パッケージを作り直す手順

`kite_clipper.py` や `profiles/*.json` を変更した場合は、**GUI と CLI の両方**を
ビルドし直してください。片方だけ更新すると、GUI と CLI で競技の扱いが食い違います。

```powershell
.\.venv\Scripts\pyinstaller.exe kite_clipper_gui.spec --noconfirm
.\.venv\Scripts\pyinstaller.exe kite_clipper.spec --noconfirm
```

`--noconfirm` は出力フォルダを作り直すため、`dist/kite_clipper_gui/` に手で
配置した `vendor/` や `models/` は消えます。配布物の組み立ては
`dist/kite_clipper_distribution/` など別フォルダで行ってください。

`frame_scorer.py` は `kite_clipper_gui.exe` に内蔵されるため、配布フォルダへ
別途 `.py` ファイルを同梱する必要はありません。

### 同梱ソフトウェアのライセンス

- **FFmpeg**: LGPL 版を同梱しています（`--enable-gpl` / `--enable-nonfree`
  を含まないビルド）。ライセンス文・ソース入手先・ビルド情報は
  `vendor/ffmpeg/windows-x64/` 内に同梱しています。
- **CLIP ViT-B/32 (ONNX 量子化版)**: OpenAI CLIP（MIT License）を
  [Xenova/clip-vit-base-patch32](https://huggingface.co/Xenova/clip-vit-base-patch32)
  で ONNX 変換したものを使用しています。詳細は `vendor/clip/SOURCE.txt`
  を参照してください。


## 動画専用UI（標準導線）

`kite_clipper_gui.py` は動画専用UIを標準起動します。動画選択後、解析担当が生成したAI区間候補を仮採用状態で表示し、不要候補を外して「切り出し実行」へ進みます。写真の入力・整理UIは通常導線から除外しました。内部フレーム画像はAI採点の中間データとして利用します。作業状態とプレビュー用クリップは `out/sessions/<session_id>/` に保存し、元動画と既存の保存先は上書きしません。

MCIが素材を開けない環境では、再生担当の動画バックエンドがエラー理由を画面へ表示します。静止画サムネイルは候補確認用で、プレビュー完了の代用ではありません。保存は映像・音声とも `-c copy` を使い、結合前にストリーム互換性を検査します。