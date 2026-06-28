# Media Clip Organizer 作業手順書

## 1. 元データを入れる

写真:

```text
input/photos/
```

動画:

```text
input/videos/
```

元ファイルは削除・移動しません。

## GUIで作業する場合

PowerShellで次を実行します。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper_gui.py
```

GUIでは、`review/frames` と `selected_frames` をエクスプローラーで開けます。
フレーム選択は、Windows エクスプローラーの特大アイコン表示を使って行います。

基本の流れ:

```text
1. input/videos を開いて元動画を入れる
2. フレーム間隔を選んで「生成」
3. review/frames を開き、残したい画像を selected_frames にコピー
4. 「切り出し」
5. selected_clips の順番を上へ/下へで調整
6. 「結合」
7. final/final_video.mp4 を確認
```

## 2. 写真と動画フレームを抽出する

デフォルトは1秒ごとです。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py
```

0.5秒ごと:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --frame-interval 0.5
```

0.25秒ごと:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --frame-interval 0.25
```

出力:

```text
review/photos/   # 写真の候補
review/frames/   # 動画から抽出したフレーム
logs/result.csv  # 判定結果
```

## 3. 切り出したい動画範囲を選ぶ

`review/frames` を見て、残したい区間の画像を `selected_frames` にコピーします。

例:

```text
selected_frames/
  session1__t000012.00.jpg
  session1__t000013.00.jpg
  session1__t000014.00.jpg
  session1__t000045.00.jpg
  session1__t000046.00.jpg
  session1__t000047.00.jpg
```

この場合、`12秒〜14秒付近` と `45秒〜47秒付近` を別クリップとして扱います。

## 4. selected_frames から動画を切り出す

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cut-from-selected-frames
```

出力:

```text
selected_clips/
logs/clip_cuts.csv
```

前後に余白を付ける場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cut-from-selected-frames --pre-roll 1.5 --post-roll 2
```

別クリップに分ける間隔を変える場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cut-from-selected-frames --split-gap 3
```

## 5. 切り出し後に不要ファイルを整理する

GUIでは「削除予定を確認」で対象を確認し、「selected_frames を削除」で削除します。
`review/frames` も削除したい場合だけ、`review/framesも削除` にチェックを入れてください。
採用候補写真も削除したい場合だけ、`review/photosも削除` にチェックを入れてください。
正規化済みの一時動画も削除したい場合だけ、`workも削除` にチェックを入れてください。
切り出し済みクリップも削除したい場合だけ、`selected_clipsも削除` にチェックを入れてください。
削除予定確認後は、対象件数と `logs/cleanup_after_cut.csv` の場所が表示されます。
削除ボタンでは確認ダイアログが出ます。

まず削除予定だけ確認します。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-after-cut --dry-run
```

問題なければ、`selected_frames` の中身を削除します。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-after-cut --confirm-delete
```

`review/frames` もまとめて削除したい場合だけ、次を使います。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-after-cut --include-review-frames --confirm-delete
```

`review/photos` の採用候補写真もまとめて削除したい場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-after-cut --include-review-photos --confirm-delete
```

`work` の一時ファイルもまとめて削除したい場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-after-cut --include-work --confirm-delete
```

`selected_clips` の切り出し済み動画もまとめて削除したい場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-after-cut --include-selected-clips --confirm-delete
```

整理結果:

```text
logs/cleanup_after_cut.csv
```

注意: この削除はWindowsのゴミ箱ではなく完全削除です。必ず dry-run で確認してから実行してください。

## 6. 切り出した動画を結合する

`selected_clips` の動画をファイル名順に結合します。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --concat-selected-clips
```

出力:

```text
final/final_video.mp4
logs/concat_clips.csv
```

GUIでは `selected_clips` の一覧にクリップ時間が表示されます。
上へ/下へで順番を調整してから「結合」を押してください。
一覧の下に選択中クリップの合計時間も表示されるので、ショート動画向けの尺を結合前に確認できます。
縦長・横長が混在する場合は、最終サイズと表示方法を選んでから結合します。

おすすめ:

```text
最終サイズ: vertical
表示方法: contain
```

これは、全クリップをショート向けの `1080x1920` に揃え、元映像全体が見えるように黒余白付きで配置する設定です。

CLIで指定する場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --concat-selected-clips --normalize vertical --fit contain
```

指定できる最終サイズ:

```text
vertical   1080x1920
horizontal 1920x1080
square     1080x1080
original   正規化しない
```

指定できる表示方法:

```text
contain  全体を収める。余白が出るが見切れにくい
cover    画面いっぱい。端が切れる場合がある
```

## 7. review フォルダ全体を整理する

`review/photos` と `review/frames` を整理する場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-review --dry-run
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-review --confirm-delete
```

## よく見るログ

```text
logs/result.csv
logs/clip_cuts.csv
logs/cleanup_after_cut.csv
logs/error.log
```
