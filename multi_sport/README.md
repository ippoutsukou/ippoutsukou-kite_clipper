# multi_sport/ — マルチスポーツ対応の作業用フォルダ

kite_clipper を複数競技（カイトボード／ウィンドサーフィン／ウィングフォイル）に対応させる作業の、
**ドキュメントと作業記録**を置く場所です。

## ファイル

| ファイル | 内容 |
|---|---|
| [MULTI_SPORT_SPEC.md](MULTI_SPORT_SPEC.md) | 仕様書（要件・仕様・設計・確認方法） |
| [TASKS.md](TASKS.md) | タスクリストと進捗、決定ログ、引き継ぎメモ |

今後追加する想定のもの:

- `baseline/result_before.csv` — Phase 1 の等価性確認用ベースライン
- `tuning_windsurf.md` / `tuning_wing.md` — しきい値の実測結果
- 調査メモ・比較検討の記録

## ⚠️ このフォルダに置いてはいけないもの

### 1. 製品コード

以下は**リポジトリルート**に置きます。ここに入れると import パスと PyInstaller の配布ビルドが壊れます。

| 対象 | 正しい配置 |
|---|---|
| `sport_profile.py` | リポジトリルート |
| `profiles/*.json` | リポジトリルート直下の `profiles/` |

`profiles/` をルートに置く理由は、`kite_clipper_gui.spec` の `datas` で同梱する対象であり、
将来のネイティブアプリ（Expo）からも参照する共有データだからです。

### 2. 教師データ・学習済みモデル

`.gitignore` は `/dataset/` と `/models/` を**ルート直下のパスとして**除外しています。
`multi_sport/dataset/labels.csv` のような場所に置くと**除外が効かず、誤ってコミットされます**。

labels.csv やモデルは必ず、ルートの `dataset/<sport>/` と `models/<sport>/` に置いてください。

### 3. 動画・フレーム画像などの実データ

`input/` `review/` `work/` を使ってください。このフォルダは軽量なドキュメント専用です。

## Git の扱い

このフォルダは `.gitignore` の対象外なので、**コミット対象になります**（意図どおり）。
ただし上記の `baseline/result_before.csv` はサイズ次第でコミット要否を判断してください。
