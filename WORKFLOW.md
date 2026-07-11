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

フレーム選択は「フレーム選別」ボタンで開く専用ビューアで行います。
縦動画のフレームもアスペクト比を保ったまま並ぶため、エクスプローラーの
特大アイコン表示より確認しやすくなっています。

基本の流れ:

```text
1. input/videos を開いて元動画を入れる
2. フレーム間隔を選んで「生成」
3. 「フレーム選別」でビューアを開き、残したい画像をクリックで採用
4. 「切り出し」
5. selected_clips の順番を上へ/下へで調整
6. 「結合」
7. final/final_video_<日時>.mp4 を確認
```

AIモデルを学習済みの場合は、手順2の後に `frame_scorer.py predict` を実行すると、手順3のビューアにスコアバッジが表示されます。

フレーム選別ビューアの操作:

```text
クリック          採用/解除（採用 = selected_frames へコピー）
Shift+クリック    アンカー（最後に操作したフレーム）から、その現在の状態をそこまで範囲適用
矢印キー          フォーカス移動
Space             フォーカス中のフレームを採用/解除（ここがアンカーになる）
Shift+Space       アンカーからフォーカス位置まで範囲適用
右クリック        元画像を既定のビューアで開く
```

範囲適用は「アンカーの今の状態に揃える」動きです。
Spaceで採用してから範囲適用すればまとめて採用、
Spaceで解除してから範囲適用すればまとめて解除になります。
キー操作はサムネイル一覧をクリックしてから使ってください（一覧にフォーカスがある間だけ効きます）。

- 解除すると `selected_frames` 内のコピーだけが削除されます。`review/frames` の元画像は残ります。
- 上部で動画ごとの絞り込みとサムネイルサイズ（小/中/大/特大）を切り替えられます。
- `logs/result.csv` にNG判定があるフレームには「NG」バッジが付き、「NG候補を隠す」で非表示にできます（ファイルは削除されません）。
- 元動画の音声を解析し、音量が動画全体の中央値より明らかに大きい秒（歓声・声など）の前後のフレームにオレンジの「♪」バッジが付きます。良いシーン探しの手がかりに使えます。音声なし動画ではバッジは付きません。
- 上部に選択枚数・推定クリップ本数・推定合計時間（前後余白込み）が常時表示されるため、結合前に尺を確認できます。
- サムネイルは `work/frame_thumbs` に、音量解析結果は `work/audio_peaks` にキャッシュされ、2回目以降は速く開きます。
- `logs/ai_scores.csv` があるフレームには、AIによる評価スコアのバッジ（緑=高評価/オレンジ=中間/グレー=低評価）が付きます。手順2.5でAI採点を実行した場合に表示されます。

従来どおりエクスプローラーで `selected_frames` にコピーする方法も併用できます。

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

## 2.5 AIにフレームを採点させる（任意）

`review/frames` の各フレームをAIが採点し、次のフレーム選別（手順3）でどのフレームを
残すか判断する参考にできます。手順2（フレーム抽出）の後、手順3（選別）の前に実行します。

初回で `models/frame_scorer.pkl` が無い場合は、先に一度 `train` を実行してモデルを作る
必要があります。ただし学習には `dataset/labels.csv` がある程度貯まっている必要があるため、
手順4.5を何度か実行済みであることが前提です（詳しくは手順4.6を参照してください）。

採点を実行します。

```powershell
.\.venv\Scripts\python.exe .\frame_scorer.py predict
```

出力:

```text
logs/ai_scores.csv   # フレームごとのAIスコア(0〜1)
```

GUIの「フレーム選別」ビューアを開くと、サムネイル左下に「AI 87」のような0〜100の
バッジが表示されます（緑=70以上/オレンジ=40〜69/グレー=40未満）。バッジはあくまで参考であり、
最終判断は手動選別のままです。

この手順は任意です。実行しなくてもスコアが無いだけで、選別自体は今までどおり行えます。

## 3. 切り出したい動画範囲を選ぶ

GUIの「フレーム選別」ビューアで残したいフレームをクリックして採用するか、
`review/frames` を見て残したい区間の画像を `selected_frames` にコピーします。

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

## 4.5 選別結果を学習データとして記録する

選別結果（採用=OK / 非採用=NG）を、将来のAI半自動トリミング機能の学習データとして
`dataset/labels.csv` に蓄積します。手順5のクリーンアップで `review/frames` を削除すると
元画像を参照できなくなるため、削除する前に実行してください。

まず件数だけ確認します（dry-run）。

```powershell
.\.venv\Scripts\python.exe .\record_labels.py --dry-run
```

問題なければ記録を実行します。

```powershell
.\.venv\Scripts\python.exe .\record_labels.py
```

出力:

```text
dataset/labels.csv        # ラベル+特徴量(追記式)
dataset/frames/<動画名>/  # 学習用の縮小画像(長辺640px)
```

補足オプション:

```text
--no-images              CSVのみ記録し、画像コピーをしません
--max-side N             コピーする画像の長辺ピクセルを変更します（既定640）
--allow-empty-selection  採用フレームが0件でも、全フレームをNGとして記録します
```

この手順は任意です。実行しなくても手順5以降に影響はありません。

## 4.6 AIモデルを再学習する（任意）

手順4.5で蓄積した `dataset/labels.csv` を使って、AI採点モデルを最新のデータで学習し直します。
データが増えるほど精度向上が期待できます。

```powershell
.\.venv\Scripts\python.exe .\frame_scorer.py train
```

出力:

```text
models/frame_scorer.pkl     # 学習済みモデル
models/train_meta.json      # 学習時のメタ情報
models/train_report.txt     # 精度レポート
```

実行するとコンソールに精度レポート（classic/clip/both特徴量セットの比較、しきい値ごとの
precision/recallなど）が表示されます。

データが少ないうちは精度が不安定です。手順4.5を何度か繰り返してデータを増やしてから
実行するのがおすすめです。

この手順は任意です。実行しなくても手順4.5・手順2.5の動作に影響はありません（train を
一度も実行していない場合は手順2.5のpredictは使えません）。

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
final/final_video_<日時>.mp4   例: final_video_20260704_204438.mp4
logs/concat_clips.csv
```

結合するたびに実行日時付きの新しいファイル名で保存されるため、過去の結合結果は上書きされずに残ります。
出力名を自分で指定したい場合は `--concat-output 出力先.mp4` を付けます（この場合は同名なら上書きされます）。

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
dataset/labels.csv
logs/ai_scores.csv
```
