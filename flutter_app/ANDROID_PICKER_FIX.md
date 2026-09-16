# Androidで大きい動画を追加すると落ちる不具合

## 症状
- Android実機（motorola edge 50s pro / Android 16）で、4K動画（3840x2160・HEVC・18秒・約97MB）を「動画を追加…」で選ぶと、その時点でアプリが落ちる。
- AI解析とは無関係。小さい書き出し済みクリップでは起きないため、これまで表面化していなかった。

## 原因（実機ログで確定）
- `java.lang.OutOfMemoryError: Failed to allocate a 194571512 byte allocation ... growth limit 268435456`
- スタックトレースは `file_selector_android` の `FileSelectorApiImpl.toFileResponse` →
  `GeneratedFileSelectorApiPigeonCodec.writeValue` → `ByteArrayOutputStream.grow`。
- プラグイン（file_selector_android 0.5.2+11）は、選ばれたファイルを
  `new byte[size]` に全部読み込み（readFully）、さらに端末内にコピーしたうえで、
  パスと中身の両方を Dart 側へ返す。中身をメッセージに詰め直すときに約2倍の
  メモリ（97MB → 約194MB）が必要になり、アプリのヒープ上限256MBを超える。
- つまりAndroidでは、おおむね80〜100MBを超える動画を選ぶと必ず落ちる。

## 対処（ビルド17）
- Androidの動画選択を自前の処理に置き換える（新しい依存は追加しない）。
  - Kotlin：`kite_clipper/picker` チャンネルの `pickVideos`。`ACTION_OPEN_DOCUMENT`（複数選択可）で
    場所（URI）だけを受け取り、64KBずつ読み書きしてアプリ専用領域 `files/videos/` にコピーし、
    コピー先のパスの一覧を返す。ファイルの中身はDart側へ渡さない。
  - Dart：`VideoPicker` がAndroidではこのチャンネル、Windowsでは従来の file_selector を使う。
- Windowsの動作は変更なし。再生・サムネイル・保存・AI解析は、これまでどおりパスで動く。

## 制限・注意
- Androidでは選んだ動画がアプリ専用領域にコピーされる（元の動画は変更しない）。
  1本あたり元動画と同じ容量を使う。一覧から外してもコピーは自動削除しない（削除は別途相談）。
- 従来の file_selector も端末内にコピーを作っていたため、コピーが増える点は以前と同じ。
  置き場所がアプリ専用領域になるため、端末のキャッシュ削除で消えにくくなる（復元には有利）。
- 空き容量が足りない場合はコピーに失敗する。エラーとして表示する。

## 利用者確認（ビルド17）
- 4K動画（3840x2160・HEVC・18秒・約97MB）を落ちずに追加でき、AI解析も実行できた（2026-09-16）。
- 実測：18フレーム・合計6.2秒（取り出し4.6秒／推論0.9秒／モデル読み込み0.7秒）＝動画1分あたり約20.8秒。
  1枚あたり取り出し0.26秒・推論0.05秒。小さいクリップ（0.13秒）より取り出しが約2倍重い。
- 換算：10分の動画で約3分30秒、20分で約7分。目安（1分あたり60秒以内）を下回るため、この方式で作り込む。
