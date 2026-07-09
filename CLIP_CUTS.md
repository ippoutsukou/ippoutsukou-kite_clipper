# selected_frames から動画を切り出す

## 流れ

1. `input/videos` に元動画を入れる
2. 指定秒ごとに `review/frames` へフレーム画像を抽出する
3. 残したい区間のフレーム画像を `selected_frames` にコピーする
4. `selected_frames` の連続した時刻を1つの区間として、元動画から切り出す
5. 結果を `selected_clips` に保存する

## フレーム抽出

1秒ごと:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --frame-interval 1
```

0.5秒ごと:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --frame-interval 0.5
```

0.25秒ごと:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --frame-interval 0.25
```

抽出画像は次のような名前になります。

```text
session1__t000012.50.jpg
```

## 切り出し

`selected_frames` に、残したい区間の画像をコピーします。

```text
selected_frames/
  session1__t000012.00.jpg
  session1__t000013.00.jpg
  session1__t000014.00.jpg
  session1__t000045.00.jpg
  session1__t000046.00.jpg
  session1__t000047.00.jpg
```

切り出し:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cut-from-selected-frames
```

この例では、`12秒〜14秒` と `45秒〜47秒` が別クリップとして切り出されます。

## 調整

前後に余白を付ける:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cut-from-selected-frames --pre-roll 1.5 --post-roll 2
```

別クリップに分ける間隔を変える:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cut-from-selected-frames --split-gap 2
```

`--split-gap 2` は、選択フレーム同士が2秒より離れていたら別クリップにする、という意味です。

高速に切る:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cut-from-selected-frames --fast-copy
```

`--fast-copy` は速いですが、カット位置が少しずれる場合があります。指定しない場合は再エンコードして、より正確に切り出します。

## 出力

```text
selected_clips/
logs/clip_cuts.csv
```

元動画は変更しません。

## selected_clips の結合

ファイル名順に結合する場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --concat-selected-clips
```

出力:

```text
final/final_video_<日時>.mp4
logs/concat_clips.csv
```

結合するたびに実行日時付きの名前で保存されるため、過去の結合結果は上書きされません。

順番を変えたい場合は、GUIを使って `selected_clips` の一覧を上へ/下へで並べ替えてから結合します。

縦長・横長が混在する場合は、正規化してから結合します。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --concat-selected-clips --normalize vertical --fit contain
```

`vertical` はショート向けの `1080x1920`、`contain` は映像全体を収める設定です。

## 切り出し後の整理

全体の作業手順は `WORKFLOW.md` を参照してください。

切り出し後に `selected_frames` を整理する場合は、まず削除予定を確認します。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-after-cut --dry-run
```

問題なければ削除します。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-after-cut --confirm-delete
```
