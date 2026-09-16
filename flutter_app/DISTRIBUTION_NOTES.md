# 配布に向けた整理（調査メモ）

利用者の指示（2026-09-16）で、未確認項目の確認（項目1）と並行して配布向けの整理（項目2）に着手。
現時点は調査のみで、配布物の作成は行っていない。

## Windows（Debugビルド・build/windows/x64/runner/Debug）
| 内訳 | サイズ |
|---|---|
| 合計 | 270MB |
| data/（同梱ファイル） | 155MB（うちCLIPモデル 85MB） |
| flutter_windows.dll | 46MB（Debug版。Releaseでは小さくなる見込み・未検証） |
| libmpv-2.dll（再生エンジン） | 29MB |
| onnxruntime.dll（AI解析） | 15MB |
| その他DLL（d3dcompiler、libGLESv2、vk_swiftshader等） | 約16MB |

### 課題
- **ffmpeg / ffprobe が未同梱**。現在は PATH 上の FFmpeg（利用者PCにインストール済み）か
  vendor/ffmpeg を使っている。配布先のPCにはどちらも無いため、そのままでは保存もAI解析も動かない。
  → 配布物に ffmpeg.exe / ffprobe.exe を同梱し、実行ファイルの隣を最優先で探すようにする必要がある
    （探索処理は既にその順序に対応済み）。FFmpegのライセンス表記も同梱する。
- CLIPモデル85MBは Windows でも AI解析に使うため必要（当初「Windowsでは不要」と書いたが誤り）。
- Release ビルドでの実サイズは未計測。署名設定も未対応（現在は開発用）。

## Android
- 現在のAPK（ビルド18）は321MB。CPU種別3種のネイティブライブラリを含む
  （arm64-v8a 17.6MB / armeabi-v7a 12.3MB / x86_64 20.3MB ＝ 約50MB）。
- 実機向けに arm64-v8a のみへ絞ったビルド19を作成中。約30MB以上の削減を見込む。
- 残りの大部分は CLIPモデル 89MB と Flutter のデバッグ用ファイル。Releaseビルドで更に減る見込み。

## 今後の選択肢（未着手）
1. ffmpeg / ffprobe の同梱と、配布物の組み立て手順の整備。
2. Release ビルドでのサイズ実測と、起動・保存・AI解析の動作確認。
3. Android の署名設定（現在は開発用の署名）。
4. モデルの軽量化（量子化済みだが、より小さいCLIP派生モデルへの差し替えは精度検証が必要）。

## Android：CPU種別の絞り込み（2026-09-16）
- ビルド19で `--target-platform android-arm64` を指定。321MB → 247MB（74MB減）。
- ただし出力を確認すると x86_64 と armeabi-v7a のライブラリが残っていた（ネイティブ合計145MB）。
  `--target-platform` はFlutter本体（libflutter.so / libapp.so）しか絞らず、
  プラグイン由来（media_kitのlibmpv、ONNX Runtimeのネイティブ）はGradle側の指定が必要。
- 対処：android/app/build.gradle.kts の defaultConfig に `ndk { abiFilters += listOf("arm64-v8a") }` を追加。
  次のビルドから適用。x86_64（エミュレータ）では動かなくなる点に注意。実機はarm64のため影響なし。
- 見込み：x86_64のlibmpv 15MB・onnxruntime 20MB、armeabi-v7aの各種を除き、さらに60MB前後の削減。

## ビルド20の結果（abiFiltersは効かなかった）
- `ndk { abiFilters += listOf("arm64-v8a") }` を追加してビルドしたが、APKは321MBに戻り、
  CPU種別3種（arm64-v8a / armeabi-v7a / x86_64）が残った。ネイティブ合計218MB。
- 理由：`--target-platform` を外したためFlutter本体が全種類入り、かつ abiFilters は
  Flutterのdebugビルドで後から上書きされて効いていない。
- 対処：`packaging { jniLibs { excludes += listOf("lib/x86_64/**", "lib/x86/**", "lib/armeabi-v7a/**") } }`
  を追加し、パッケージ段階で除外する（上書きされない）。次のビルドで効果を確認する。
- これで駄目な場合は深追いせず、配布用（Release）ビルドを作る段階で `--split-per-abi` 等でまとめて対処する。
  確認作業はビルド19（247MB・arm64本体入り）で支障なく行える。

## ビルド21：CPU種別の絞り込みに成功（2026-09-16）
- `packaging { jniLibs { excludes += listOf("lib/x86_64/**", "lib/x86/**", "lib/armeabi-v7a/**") } }`
  と `--target-platform android-arm64` の併用で、APKは186MB。含まれるCPU種別は arm64-v8a のみ。
- サイズ推移：321MB（ビルド18）→ 247MB（ビルド19・Flutter本体のみ絞る）→ 321MB（ビルド20・abiFilters無効）
  → **186MB（ビルド21）**。ビルド18比で135MB減（約42%減）。
- ネイティブライブラリ合計は218MB → 84MB。
- 残る大きな要素：CLIPモデル89MB（AI解析に必須）、Flutterのデバッグ用ファイル。
  Releaseビルドでさらに小さくなる見込み（未計測）。
- 注意：x86_64のエミュレータでは動かなくなった。実機（arm64）のみ対象。

## 利用者確認（2026-09-16）
- Windows・Androidの両方で確認：
  1. 中止（処理中の1枚の完了後に停止し、途中までの結果で提案）→ OK
  2. 保存済み結果の再利用（待たずに結果が出る／解析し直しも可能）→ OK
  3. 5分以上の長い動画 → 未確認
- 別途、特定の動画（Downloads/1788573856347.mp4）でAI解析ができない事象の報告あり。調査中。
