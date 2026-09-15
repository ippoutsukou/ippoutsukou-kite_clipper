# kite_clipper ネイティブアプリ版(iOS) 要件定義・仕様書

作成日: 2026-07-18
ステータス: ドラフト(ユーザー承認待ち)
補足: WEBAPP_SPEC.md(自宅PC+LANのWeb版)は本方針の採用により保留とする。

## 1. 要件定義

### 目的

PCを持たないカイトボーダーでも、iPad / iPhone 単体で
「撮影素材から良い瞬間だけを抜き出す下ごしらえ」ができるアプリを作る。

結合・色調整・BGM・テロップ等の仕上げは対象外とし、
各自が使い慣れた編集アプリ(LumaFusion / CapCut / iMovie 等)に引き継ぐ。

### 対象スコープ

```text
動画読み込み → フレーム抽出(サムネイル) → AI採点(任意) → 選別(タップ採用/解除)
→ 切り出し(再エンコードなし) → 並べ替え → 連番付きで書き出し
```

| 機能 | 内容 |
|------|------|
| 動画読み込み | 写真ライブラリ / ファイルアプリから選択(microSD→ファイルアプリ経由もこれで対応) |
| フレーム抽出 | 指定間隔(1 / 0.5 / 0.25秒)のサムネイルをグリッド表示。表示範囲から順次生成 |
| 選別 | タップで採用/解除、範囲適用、採用数・推定クリップ本数・推定合計時間の常時表示 |
| AI採点(Phase 2) | CLIP埋め込み+学習済みモデルでスコアバッジ、ヒステリシス区間提案(入り0.5/抜け0.4)、一括採用 |
| 切り出し | pre-roll / post-roll / split-gap 指定。パススルー(再エンコードなし)で画質劣化ゼロ |
| 並べ替え・書き出し | クリップ一覧をドラッグで並べ替え → `01_xxx.mp4` 連番でファイルアプリ(または写真)へ書き出し |

### 非対象

- 結合・解像度正規化・再エンコード(編集アプリに任せる)
- 色調整・BGM・テロップ等の編集機能
- Android対応(将来検討)
- 学習(train)のオンデバイス実行 — 学習は当面PCで行い、変換済みモデルをアプリに同梱

### 制約

- 開発環境は Windows のみ(Macなし)。ビルドは EAS Build(クラウド)を使用
- Apple Developer Program(年間$99)は Phase 1 の実機配布時点で契約
- パススルー切り出しはキーフレーム境界に丸められる(最大1〜2秒のずれ)。
  pre/post-roll の余白で吸収する方針とし、フレーム精度の切り出しは要件にしない

### 成功条件

1. iPad/iPhone 単体で「素材選択→選別→切り出し→書き出し」が完結する
2. 10分の4K素材(1秒間隔=約600フレーム)で選別操作が実用速度で行える
3. 書き出したクリップが LumaFusion / CapCut にそのまま取り込め、画質劣化がない

## 2. 技術スタック

| 項目 | 選定 | 備考 |
|------|------|------|
| フレームワーク | Expo (React Native) + TypeScript | Windows開発+EASクラウドビルドでMacレス |
| サムネイル | expo-video-thumbnails | Expo Go 内で動作(Phase 0 で性能検証) |
| 動画選択 | expo-image-picker / expo-document-picker | 写真ライブラリ / ファイルアプリ |
| 切り出し | Expo Modules API + AVAssetExportSession(passthrough) | 小規模なSwiftモジュール。ビルドはEASが実施 |
| 書き出し | expo-file-system / expo-media-library | 連番ファイル名でフォルダ書き出し |
| AI推論 | onnxruntime-react-native + CLIP ONNX(vendor/clip 流用) | scorer は skl2onnx で変換して同梱 |
| 配布 | EAS Build → TestFlight | 仲間への配布もTestFlightで可 |

## 3. 段階計画

プロジェクト場所: `C:\Users\ippou\Documents\portfolio\porject\kite_clipper_mobile`

注: AI採点(onnxruntime-react-native)はネイティブモジュールのため Expo Go では動かない。
実機でのAI検証には development build が必要で、Macレス環境ではその時点で
Apple Developer Program($99)契約が必要になる。そのため「無料のままAI実機検証」は不可。
$99契約の判断材料を Phase 0a で作り、契約後は AI+切り出しを同一ビルドで検証する。

### Phase 0a: 選別UI検証(費用ゼロ・Expo Goのみ)

目的: 「iPadでの選別体験」と「サムネイル生成速度」という2大不確定要素を最小コストで検証する。

- 動画を写真ライブラリから選択
- 間隔選択(1/0.5/0.25秒)→ サムネイルグリッド表示(遅延生成)
- タップ採用/解除、採用リスト、推定クリップ本数表示(split-gapグルーピングのTS移植)
- 検証項目: 600枚規模での生成時間・スクロール性能・メモリ、タッチ選別の操作感

判断基準: 10分素材で選別開始までの待ちが許容範囲(目安: 初期表示数秒、全体でも数十秒)で、
選別操作が快適なら $99 を契約して Phase 0b へ。

### Phase 0b: AI採点+切り出しの実機検証(ここで $99 契約・EAS development build)

- AI採点: CLIP ONNX(vendor/clip/vision_model_quantized.onnx 89MB を同梱)+ onnxruntime-react-native
  - frame_scorer.pkl を skl2onnx でONNX化して同梱
  - スコアバッジ・ヒステリシス区間提案(入り0.5/抜け0.4)・AI提案一括採用のTS移植
  - 検証項目: 600フレームの推論時間(バックグラウンド実行+進捗表示)
- 切り出し: Swiftネイティブモジュール(AVAssetExportSession パススルートリム)
  - pre-roll / post-roll / split-gap 指定 → 採用フレームからクリップ生成
  - 並べ替え画面 → 連番付き書き出し(ファイルアプリ / 写真)
- AI・切り出しは同一の development build に同居させ、ビルド回数を節約する

### Phase 1: TestFlight 配布

- Phase 0b の検証で採点・切り出しが実用レベルなら、同じEAS資格情報のまま
  TestFlight へ提出し、自分の iPad/iPhone で実運用開始

### Phase 2: 仲間への展開(任意)

- TestFlightの外部テスター招待で配布
- フィードバックを踏まえ App Store 公開を検討

## 4. 既存Python資産との関係

- PC版(CLI / Tkinter GUI)は現状のまま維持。自分のワークフローでは併用可
- 学習ループ(record_labels → frame_scorer train)はPC側に残し、
  精度が上がったモデルをONNX変換してアプリ更新時に同梱する運用
- 移植対象ロジック: split-gapグルーピング、pre/post-roll計算、ヒステリシス区間提案、
  スコアバッジしきい値(70/40)などの仕様値
