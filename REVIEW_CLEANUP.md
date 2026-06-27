# reviewフォルダの整理

`review/photos` と `review/frames` は確認用の一時置き場です。
残したい写真やフレームは、削除前に次のフォルダへコピーしてください。

```text
selected_photos/
selected_frames/
```

削除予定だけを確認する場合:

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-review --dry-run
```

問題なければ、確認済み候補を削除します。
削除対象は `review/photos` と `review/frames` のファイルだけです。
`input/photos` と `input/videos` は触りません。

```powershell
.\.venv\Scripts\python.exe .\kite_clipper.py --cleanup-review --confirm-delete
```

整理結果は `logs/cleanup.csv` に保存されます。
エラーが出た場合は `logs/error.log` に記録され、処理は継続します。

## フレームのNG候補判定

動画から抽出した `review/frames` の画像は、`logs/result.csv` の `frame` 行に次の判定列が出ます。

```text
has_person_candidate
has_kite_candidate
is_empty_scene
is_ng
ng_reason
```

`is_ng=True` は、人候補もカイト候補も見つからない、または空・海だけに近いフレーム候補です。
OpenCVだけの簡易判定なので、自動削除ではなく確認優先度を下げるための目安として使ってください。
