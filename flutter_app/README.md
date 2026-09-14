# Kite Clipper Flutter 最小実証

現在の実装範囲は動画1本の選択・再生・範囲指定・個別書き出しです。
Flutter画面とWindowsのFFmpeg接続、AndroidのMediaExtractor/MediaMuxer接続を追加しています。
AI解析、候補の並べ替え、結合保存、タグ編集は未実装です。

## 起動準備
SDK導入済みのこの作業環境では、プロジェクトのルートから以下を実行できます。

```powershell
.\flutter_app\tool.ps1 doctor
.\flutter_app\tool.ps1 windows
.\flutter_app\tool.ps1 apk
```

スクリプトはvendor内のJDK/Android SDK/Gradleキャッシュを参照し、終了時にプロセス環境変数を元に戻します。OSの永続PATHは変更しません。

作業ディレクトリはこのフォルダーです。Flutter SDKは親フォルダーのvendor/flutterに配置します。
PowerShellで `../vendor/flutter/bin/flutter.bat pub get` を実行します。
Windowsは対応するVisual Studio C++デスクトップ開発ツールが必要です。
AndroidはAndroid SDK、JDK、接続端末またはエミュレーターが必要です。
`flutter doctor` で環境を確認してから `flutter run -d windows` またはAndroid端末を指定して起動します。

## 保存と制限
- Windows: 保存先内に一意なkite_clip_ディレクトリを作りclip.mp4を書き出します。FFmpegは実行ファイル隣、親プロジェクトのvendor、PATHの順で検出します。
- Android: Android 8以降でOS標準の新規ドキュメント保存を使用します。映像H.264/H.265と任意のAAC音声が初期検証対象です。
- 指定時間は要求範囲です。再エンコードなしのため開始位置などはキーフレーム・音声パケットの境界に依存します。出力の実効範囲の表示とプレビュー一致は今後の作業です。
- 元動画全体をプレビューします。範囲外で自動停止する機能はまだありません。
- 保存失敗時に未完成ファイルが残ることがあります。アプリから自動削除しません。
- Androidで選んだ保存先がクラウドプロバイダーならOS側で同期される可能性があります。ローカル保存先で検証してください。

## 確認方法
合成したH.264/AACの短い動画で、音声付き再生・シーク・区間書き出しを確認します。
保存先の動画を再度開き、映像・音声・長さを確認します。
WindowsとAndroidの結果を個別に記録し、ビルド成功だけで実機確認済みにしません。
