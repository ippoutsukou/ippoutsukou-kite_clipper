# 検証結果

## 2026-09-15 更新（以下の旧記録より優先）
- ビルド4でAndroid実機のMOV保存と音声について利用者から「保存できた、音もOK」の確認を得た。
- Androidの動画選択・再生・範囲バー連動・音声付き保存の最小実証を完了。
- Windowsの画面見切れと範囲バー連動は利用者が修正を確認。
- 自動テストは10件成功。Android PCM変換の自動実機テストではない。
- AndroidはH.264/H.265映像とAAC音声をコピー。PCM16・1〜2チャンネルの音声をAACへ変換する。
- ビルド4 APK生成・署名検証成功。配布物は `../out/flutter-proof/kite-clipper-0.1.0-build4-pcm-aac.apk`。
- 全形式・全端末、厳密な音声同期、保存境界のフレーム精度は未検証。高速コピーの境界差は残る。
- 参照画像の完全再現、連続サムネイル、複数候補、結合、AI解析は未完了。

## 2026-09-14 初回ビルド時点の記録

## 現在の結果
| 項目 | 結果 |
| --- | --- |
| Windowsビルド | 成功。Debug実行ファイルと動画再生用DLLを生成 |
| Windows起動 | Kite Clipperウィンドウの生成・応答を確認 |
| 自動テスト | 5件すべて合格 |
| Windows再生エンジン | 同梱libmpvで合成動画の時刻進行・一時停止・シークを確認（無音テスト） |
| Windows区間保存 | 4秒の合成動画から1〜3秒を保存。映像・音声・長さ2秒±0.15秒・全デコード・原本不変を確認 |
| Androidビルド | Debug APK生成成功。アプリのKotlinコードもコンパイル成功 |
| APK検証 | 署名検証成功。最小API 26、対象API 36、arm64-v8a / armeabi-v7a / x86_64 |
| Android実機 | 接続端末なし。選択・再生・保存の実機確認は未実施 |
| 見た目の比較 | 画面操作ランタイムのACLヘルパー起動エラーにより未実施 |

## 成果物
- Windows: `build/windows/x64/runner/Debug/kite_clipper_flutter.exe`
  DLLとdataフォルダーを含む同じディレクトリ全体が必要。
- Android: `../out/flutter-proof/kite-clipper-0.1.0-debug.apk`
  開発確認用APK。公開ストア向けリリースではない。
- 合成動画・保存結果: `../out/flutter-proof/`

## 未完了
- Windowsの実画面でのファイル選択、動画描画、音の聴取、保存操作の一連の確認。
- Android実機での選択・再生・保存、回転情報、音声同期、形式差の確認。
- 参照生成画像と起動スクリーンショットの比較・調整。
- タイムラインの連続サムネイル、実効範囲へのスナップ、保存範囲と一致するプレビュー。
- AI解析、複数候補管理、結合、タグは初期実証の対象外。

## 開発環境
Flutter 3.47.4 / Dart 3.13.3、JDK 17.0.20.1、Android API 36、Build Tools 36.0.0、NDK 28.2.13676358をvendor配下に導入。
JDKとAndroidコマンドラインツールはSHA-256を確認。SDK規約は利用者の同意後に受諾。
tool.ps1はプロセス内だけにSDK/JDK/Gradleキャッシュを設定し、終了時に戻す。
OSの永続PATH、ACL、セキュリティ除外は変更していない。開発者モードは利用者が有効化した。

## 今回のビルド復旧
通常のコマンド・画像・編集ツールでACLヘルパーエラーがあり、承認付きコマンドとapply_patchエンジンの直接起動を使用。
Windowsの動画ライブラリをCMakeで直接展開し、mpvヘッダーを期待する階層にもコピー。
MSBuild/Gradle経由の初回コンパイルはapp.dillへのアクセス拒否で失敗。
小さい書き込み検証は成功。公式Flutter assembleをDartから直接実行するとコンパイル成功し、Windowsビルドも完了。
Androidは依存取得後の再ビルドで成功。アクセス拒否の根本原因は未確定。
ログは `../out/flutter-proof/windows-build.log`、`windows-assemble-direct.log`、`android-build.log`。
