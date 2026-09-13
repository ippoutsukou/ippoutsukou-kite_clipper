# Media Clip Organizer

## 新UIの標準フロー（動画）

動画選択 → サムネイル生成とAI区間候補表示 → 候補の範囲調整・除外 → 音声付きプレビュー → 個別保存または結合保存、の順で進めます。任意タグを付けられ、状態は `out/sessions/` に自動保存されます。モデル未準備時はAI候補を作らず、手動区間追加を利用します。再生はFFmpegとwaveOutです。

この後に記載するCLI・旧補助ツールの手順は、上記GUI標準フローとは別の運用です。
 作業手順書

## 標準GUIの動画6工程

動画ファイルを選択 → 自動解析 → AI候補を確認 → 切り出し実行 → 動画プレビューと範囲調整 → 個別保存または結合保存、の順に進みます。候補の除外・復帰・並べ替えと任意タグは同じ作業状態に保存され、再開時に復元されます。写真機能と旧フレーム選別は旧CLI・内部互換として残していますが、標準GUIでは使用しません。内部フレーム画像は動画解析のために生成されます。

## 0. 競技（スポーツプロファイル）を選ぶ

このアプリはカイトボード専用ではなく、`profiles/*.json` で定義した競技プロファイルを
切り替えて使えます（対応: カイトボード / ウィンドサーフィン / ウィングフォイル）。
教師データ・学習済みモデルは競技ごとに `dataset/<sport_id>/` `models/<sport_id>/` に
分かれます（カイトボードのみ後方互換のため `dataset/labels.csv` `models/frame_scorer.pkl` のまま）。

- GUI: 画面上部（工程ステッパーの上）の「競技」ドロップダウンから選びます。選択は
  `logs/gui_settings.json` に保存され、次回起動時も復元されます。処理実行中は切り替えできません。
- CLI: 各コマンドに `--sport <id>` を付けます（未指定時は既定の `kite`）。例: `--sport windsurf`。

前回と異なる競技の中間生成物（`review/frames` 等）が残ったまま切り替えようとすると、
誤判定を防ぐため確認が入ります（GUIは確認ダイアログ、CLIは終了コード2での停止）。
詳しくは README.md の「競技（スポーツプロファイル）の切り替え」を参照してください。

## 1. 元データを入れる

標準GUIではOSのファイル選択から動画を指定します。

```text
input/photos/
```

動画:

```text
input/videos/
```

元ファイルは削除・移動しません。内部フレーム画像は動画解析用に生成されます。

## GUIで作業する場合

PowerShellで次を実行します。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper_gui.py
```

旧CLI・内部互換のフレーム選別は「フレーム選別」ボタンから開けます。標準GUIの動画6工程では使用しません。
縦動画のフレームもアスペクト比を保ったまま並ぶため、エクスプローラーの
特大アイコン表示より確認しやすくなっています。

基本の流れ:

```text
1. input/videos を開いて元動画を入れる
2. フレーム間隔を選んで「生成」
3. 「フレーム選別」でビューアを開き、残したい画像をクリックで採用
4. 「切り出し」(高速コピーにチェック)　＊ここで動画編集ソフトに渡すでもOK
5-1. selected_clips の順番を上へ/下へで調整
5-2. 「結合」
6. final/final_video_<日時>.mp4 を確認
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

- 手順2.5でAI採点（区間提案つき）を実行済みの場合、サムネイル上部に色帯（区間バンド）が付きます。同じ動画内で隣り合う区間は色が交互（青系/緑系）に変わるため、区間の切れ目が分かります。
- ツールバーの「AI区間:」ドロップダウンで「すべて」「提案区間のみ」「提案区間以外」を切り替えられます（提案区間が無い場合はドロップダウン自体が無効表示になり「すべて」に固定されます）。「提案区間以外」はAIが拾い損ねたフレームが無いか取りこぼし確認する際に便利です。
- 「AI提案を採用」ボタンを押すと、提案区間に属する未採用フレームをまとめて採用できます（確認ダイアログあり）。採用済みのフレームはそのまま残るため、押し直しても二重コピーにはなりません。
- 「AI採点を実行」ボタンを押すと、確認ダイアログの後にバックグラウンドで `frame_scorer.py predict` が実行され、完了するとスコア・区間提案が自動で反映されます（CLIを使わずビューアだけで採点・区間提案まで完結できます）。実行中はボタンが「AI採点 実行中...」になりUIはブロックされません。
- ビューアを開いた直後、提案区間があり、かつ採用が1件も無い場合だけ、同じ内容の確認ダイアログが自動で表示されます。すでに手動で採用している場合は表示されません（選択を壊さないため）。

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

カイトボード以外の競技を使う場合は `--sport` を付けます（例: `--sport windsurf`）。
モデルパス・しきい値は指定した競技のプロファイル（`profiles/<sport>.json`）から解決されます。

GUIのビューアの「AI採点を実行」ボタンからも同じ処理を実行できます（GUI上部で選択中の競技が自動的に使われます）。CLIを開かずに、フレーム選別ビューアだけで採点から区間提案までを完結させたい場合はこちらが便利です。

出力:

```text
logs/ai_scores.csv     # フレームごとのAIスコア(0〜1)
logs/ai_segments.csv   # 動画ごとの区間提案(採用候補区間)
```

GUIの「フレーム選別」ビューアを開くと、サムネイル左下に「AI 87」のような0〜100の
バッジが表示されます（緑=70以上/オレンジ=40〜69/グレー=40未満）。バッジはあくまで参考であり、
最終判断は手動選別のままです。

`predict` は各動画についてフレームスコアを移動平均で平滑化し、ヒステリシス方式
（入り0.5以上で区間開始、抜け0.4未満で区間終了、既定はどちらも `segment_simulator.py`
のシミュレーションで実証済みの値）で「採用候補の区間」を自動提案し、
`logs/ai_segments.csv` に出力します。次のオプションで調整できます。

```text
--enter N          区間開始のスコアしきい値（既定 0.5）
--exit N           区間終了のスコアしきい値（既定 0.4、--enterより小さい必要があります）
--smooth-window N  スコア平滑化の移動平均窓（フレーム数、中央寄せ、既定 3。1で平滑化なし）
--no-segments      logs/ai_segments.csv を出力しません
```

GUIの「フレーム選別」ビューアでは、提案区間に属するフレームのサムネイル上部に
色帯（区間バンド）が表示されるほか、「AI区間:」ドロップダウン（すべて/提案区間のみ/
提案区間以外）での絞り込みや、「AI提案を採用」ボタンによる一括採用（プリセレクト）
ができます。詳しくは「フレーム選別ビューアの操作」を参照してください。

この手順は任意です。実行しなくてもスコア・区間提案が無いだけで、選別自体は今までどおり行えます。

## 2.6 おまかせモード（自動切り出し）

GUIの「① 素材」パネルにある「おまかせモード（自動切り出し）」をONにすると、
`input/videos` を自動監視し、新しい動画が置かれるたびに

```text
フレーム抽出 → AI採点(predict) → AI提案区間からの切り出し
```

を自動実行し、`selected_clips` にクリップを出力します。手順3（選別）・手順4
（切り出し）を手動で行わずに済む、AI提案区間をそのまま採用するモードです。
**結合（手順6）は行われません。**

前提:

```text
models/frame_scorer.pkl が学習済みであること（手順4.6を参照）
```

未学習の場合はONにした際に警告が出て、トグルは自動でOFFに戻ります。

使い方:

```text
1. GUIを起動し、「① 素材」パネルで「おまかせモード（自動切り出し）」をON
2. input/videos に動画ファイルを置く（コピー中でも構いません。書き込み完了を
   自動検知してから処理します）
3. ログ欄に「おまかせ: フレーム抽出中...」→「おまかせ: AI採点中...」→
   「おまかせ: 切り出し中...」の順で進捗が表示されます
4. selected_clips に自動でクリップが出力されます
5. 続けて別の動画を input/videos に置けば、同様に自動処理されます
```

補足:

```text
- フレーム間隔・前後余白は、②③④パネルで設定している値がそのまま使われます。
- 対象動画のAI提案区間が0件だった場合は、切り出しをスキップし
  「区間提案なし（切り出しスキップ）」とログに出して処理済みとして記録します。
- 他のコマンド実行中は自動処理を開始せず、空くのを待って再試行します。
- 処理中にエラーが起きた場合は処理を中断し、ログに理由を出します。この場合は
  「処理済み」として記録しないため、原因を直してもう一度同じ動画を検知させれば
  再試行できます（ファイルを一度どかしてから入れ直すなどしてください）。
- OFFにしても実行中の自動処理はそのまま完走します。
```

処理済みの動画は `logs/omakase_processed.json` に記録され、GUIを再起動しても
再処理されません。同じ動画をもう一度おまかせモードで処理したい場合は、この
ファイルから該当ファイル名のエントリを削除するか、ファイル自体を削除してください
（削除は手動で行ってください。GUI・CLIからは削除しません）。

出力・ログ:

```text
selected_clips/               # 自動切り出し結果
logs/ai_segments.csv          # AI提案区間（frame_scorer.py predictが出力）
logs/auto_clip_cuts.csv       # おまかせモードでの切り出し結果ログ（手動切り出しのlogs/clip_cuts.csvとは別）
logs/omakase_processed.json   # おまかせモードの処理済み記録
```

CLIから直接使う場合は次のオプションが使えます（GUIのおまかせモードが内部で
使っているものと同じです）。

```powershell
# 特定の動画だけフレーム抽出（他の動画のresult.csvの内容は保持されます）
.\.venv\Scripts\python.exe .\kite_clipper.py --frame-interval 1 --only-video 動画ファイル名

# AI提案区間から直接切り出し（selected_frames を経由しません）
.\.venv\Scripts\python.exe .\kite_clipper.py --cut-from-ai-segments --only-video 動画ファイル名 --pre-roll 1 --post-roll 1
```

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
--sport <id>             出力先を profiles/<id>.json の paths.dataset にします（既定 kite）
```

この手順は任意です。実行しなくても手順5以降に影響はありません。

## 4.6 AIモデルを再学習する（任意）

手順4.5で蓄積した `dataset/labels.csv` を使って、AI採点モデルを最新のデータで学習し直します。
データが増えるほど精度向上が期待できます。

```powershell
.\.venv\Scripts\python.exe .\frame_scorer.py train
```

カイトボード以外の競技を学習する場合は `--sport` を付けます（例: `--sport windsurf`）。
入力（`dataset/<sport>/labels.csv`）・出力先（`models/<sport>/frame_scorer.pkl`）は
指定した競技のプロファイルから解決されます。

出力（`--sport` 未指定＝kiteの場合）:

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
logs/ai_segments.csv
logs/auto_clip_cuts.csv
logs/omakase_processed.json
```


## 動画専用UIの確認

動画の選択から候補確認、明示的な切り出し、音声付きプレビュー、範囲調整、任意タグ、個別保存／互換性確認付き結合を同じ作業セッションで行います。作業JSONは `out/sessions/` に一意IDで保存され、途中状態から復元できます。

AIモデルが未準備または候補ゼロの場合は候補を水増しせず、画面の手動追加から対象動画と区間を指定します。元動画の参照切れ・変更は自動差替えせず、場所の再指定を案内します。