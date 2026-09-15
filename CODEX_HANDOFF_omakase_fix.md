# おまかせモードAI採点フリーズ修正 — 再開用ハンドオフ

作成日: 2026-07-27 09:04 時点でのスナップショット。
元の指示書: (scratchpad) `omakase_ai_fix_spec.md`（Codexに渡した全文はこの下の「元の指示書」参照）

## 状況

`/codex:rescue` で A案（内部サブコマンド方式）の実装を依頼し、Codexが作業中に
ユーザー判断で中断。**中断時点で codex.exe プロセスはまだ稼働していた**
（ファイル更新は 08:29 の README.md 更新を最後に停止、以後35分変化なし＝
テスト実行中か停滞かは不明）。

**中断前にコード変更自体はタスク1〜7すべて着手・実質完了していた。**
未検証なのはビルド・実機テストのみ。

## タスク進捗（レビュー結果）

- [x] **タスク1**: `frame_scorer_launcher.py` 新規作成
  - `FRAME_SCORER_ARGV_FLAG = "--__run-frame-scorer"` 定義済み
  - `build_predict_command(root, extra=None)` 実装済み（frozen/非frozen分岐、
    `--root` 明示渡し込み）
- [x] **タスク2**: `kite_clipper_gui.py` のエントリ分岐
  - `main()` 関数化、`_run_frame_scorer()` 実装済み
  - `sys.stdout`/`sys.stderr` が `None` の場合の `os.devnull` フォールバック実装済み
  - 想定外引数で終了コード2を返す分岐も実装済み
  - 未実装（任意扱いだったので問題なし）: `logs/frame_scorer_last.log` への出力保存
- [x] **タスク3**: `run_frame_scorer_predict` を `build_predict_command(ROOT)` 経由に変更済み
- [x] **タスク4**: `frame_selector.py` の `_ai_scoring_worker` も
  `build_predict_command(self.root_dir)` に統一済み（再発防止その1）
- [x] **タスク5**: `_run_cli_worker` に事前チェック＋無応答ウォッチドッグ実装済み（再発防止その2）
  - 実行前チェック: 実行ファイル実在確認、frozen時のGUI自己起動ガード
    （`FRAME_SCORER_ARGV_FLAG` なしでの自己起動を検知してエラー終了）
  - ウォッチドッグ: `CLI_WATCHDOG_TIMEOUT_SECONDS = 30 * 60`、無出力かつプロセス
    生存中がこの秒数続くと `terminate()` → 5秒待って `kill()`、`callback` に
    非0を返す実装
- [x] **タスク6**: `kite_clipper_gui.spec` 更新済み（`.gitignore` 対象なので `git status`
  には出ないが、ファイルは存在し中身も更新済み）
  - `collect_all('sklearn')` / `collect_all('onnxruntime')` を追加
  - `hiddenimports=['frame_scorer', 'clip_embedder', *sklearn_hiddenimports, *onnxruntime_hiddenimports]`
- [x] **タスク7**: `README.md` 更新済み（frame_scorer.py がexeに内蔵される旨を追記）

## ⚠️ 要確認事項（スコープ外の変更が混入）

**`WORKFLOW.md` が指示書に無い内容で変更されている。**

```diff
-4. 「切り出し」
-5. selected_clips の順番を上へ/下へで調整
-6. 「結合」
-7. final/final_video_<日時>.mp4 を確認
+4. 「切り出し」(高速コピーにチェック)　＊ここで動画編集ソフトに渡すでもOK
+5-1. selected_clips の順番を上へ/下へで調整
+5-2. 「結合」
+6. final/final_video_<日時>.mp4 を確認
```

指示書のスコープ外（「おまかせモードの機能追加・UI変更は行わない」と明記していた）。
コード変更ではなくドキュメントの手順記述変更なので実害は無いはずだが、
再開時にCodexへの指示に含めていない内容のため、**採用するか差し戻すかユーザー判断が必要**。

## 再開時にやること（優先順）

1. **codex.exe が今も動いているか確認**し、動いていなければ `/codex:rescue --resume`
   で続きから再開する（`--fresh` にすると別スレッドになるので注意）。
2. Codexに「検証手順（開発環境）を実施して結果を報告して」と明示的に依頼する
   （中断時点で自己検証が完了した確証がない）。
   - `python -c "import kite_clipper_gui"` 等の構文・import確認
   - `frame_scorer_launcher.build_predict_command` の単体的な動作確認
     （非frozen環境で正しい python.exe パスが返るか）
3. `WORKFLOW.md` の差分について、採用可否をユーザーに確認（上記「要確認事項」）。
4. 全て問題なければ、以下の配布版検証はユーザー側で実施:
   - `pyinstaller kite_clipper_gui.spec` で再ビルド
   - `kite_clipper_gui.exe --__run-frame-scorer predict --root <配布フォルダ>` を
     直接実行し、GUIが出ずに終了コード0で `logs/ai_scores.csv` /
     `logs/ai_segments.csv` が更新されることを確認
   - `kite_clipper_gui.exe --unknown-flag` が終了コード2で即終了することを確認
   - exe版のおまかせモードで抽出→採点→切り出しが完走することを確認
   - exe版の選別ビューアの「AI採点を実行」が成功することを確認
5. 問題なければ `git add` で下記ファイルをコミット（`.spec` は `.gitignore` 対象のため
   コミット対象外、現状通りでよい）:
   - `frame_scorer_launcher.py`（新規）
   - `kite_clipper_gui.py`
   - `frame_selector.py`
   - `README.md`
   - `WORKFLOW.md`（要確認事項の結論次第で差し戻す可能性あり）

## 変更差分の要約（`git diff --stat`、中断時点）

```
README.md           |   5 ++-
WORKFLOW.md         |   8 ++--
frame_selector.py   |   6 +--
kite_clipper_gui.py | 127 ++++++++++++++++++++++++++++++++++++++++++++++++----
4 files changed, 130 insertions(+), 16 deletions(-)
```
（`frame_scorer_launcher.py` は新規ファイルのため diff --stat には出ない）
