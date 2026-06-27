# kite_clipper

Windows 上で、カイトボードの写真と動画を整理するための OpenCV ベースのツールです。

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
