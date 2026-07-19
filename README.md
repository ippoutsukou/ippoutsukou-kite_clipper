# Media Clip Organizer

Windows 上で、写真と動画を整理するための OpenCV ベースのツールです。

## フォルダ構成

```text
input/
  photos/        # 元写真を入れる
  videos/        # 元動画を入れる
review/
  photos/        # 類似グループごとの採用候補写真コピー
  frames/        # 動画から1秒ごとに抽出したフレーム
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
kite_clipper_gui.exe   # GUI 本体
kite_clipper.exe       # CLI 本体（GUI から内部的に呼び出されます）
vendor/ffmpeg/         # 同梱 FFmpeg（LGPL 版）
vendor/clip/           # CLIP 画像特徴量モデル（ONNX）
models/                # AI スコアリング用学習済みモデル
input/ logs/ work/ ...  # 実行時に使う作業用フォルダ（初期状態は空）
```

### 同梱ソフトウェアのライセンス

- **FFmpeg**: LGPL 版を同梱しています（`--enable-gpl` / `--enable-nonfree`
  を含まないビルド）。ライセンス文・ソース入手先・ビルド情報は
  `vendor/ffmpeg/windows-x64/` 内に同梱しています。
- **CLIP ViT-B/32 (ONNX 量子化版)**: OpenAI CLIP（MIT License）を
  [Xenova/clip-vit-base-patch32](https://huggingface.co/Xenova/clip-vit-base-patch32)
  で ONNX 変換したものを使用しています。詳細は `vendor/clip/SOURCE.txt`
  を参照してください。
