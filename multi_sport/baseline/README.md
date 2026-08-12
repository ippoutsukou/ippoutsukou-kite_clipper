# Phase 1 等価性確認用ベースライン

`result_before.csv` は、**マルチスポーツ対応の実装に着手する前**の `kite_clipper.py` が出力した
`logs/result.csv` です。Phase 1-5 でこのファイルとの差分ゼロを確認します。

## 取得条件

| 項目 | 値 |
|---|---|
| 取得日 | 2026-08-12 |
| 起点コミット | 8c9fe6b（作業ツリーは未変更の状態） |
| 入力 | `input/videos/` の3本（20260704_161647 / _161657 / _161713.mp4） |
| オプション | なし（すべて既定値） |
| 行数 | 20行（+ ヘッダ1行） |
| SHA256 | `278b985ae7547fcf22d6ee89ca2b83ac91d8c18af086a4a45bd0c048eea0d254` |

**ユーザーの作業フォルダは一切変更していません。** スクラッチパッド上に隔離ワークスペースを作り、
入力動画だけをコピーして `--root` で実行しています。

## ⚠️ 再現に必要な入力動画が失われている（2026-08-12 時点）

ベースライン取得に使った3本（`20260704_161647` / `_161657` / `_161713.mp4`）は、
**すでに `input/videos/` から無くなっています**（wing の教師データ収集のため入れ替えられた）。

現時点でこの3本が残っているのは、スクラッチパッド上の隔離ワークスペースだけです。
スクラッチパッドはセッション用の一時領域なので、**消えるとベースラインの再現ができなくなります**。

今後も回帰確認を続けたい場合は、この3本をどこか安全な場所に退避してください（合計約96MB）。
退避しない場合、`result_before.csv` は「過去にこの結果だった」という記録としては残りますが、
再実行して比較することはできなくなります。

## 再現手順

```bash
WS=/path/to/isolated_workspace
mkdir -p "$WS/input/videos"
cp /退避先/20260704_1616*.mp4 /退避先/20260704_161713.mp4 "$WS/input/videos/"
./.venv/Scripts/python.exe kite_clipper.py --root "$WS"
diff "$WS/logs/result.csv" multi_sport/baseline/result_before.csv
```

`result.csv` には `source_path` / `output_path` として**絶対パスが埋め込まれる**ため、
比較するワークスペースのパスはベースライン取得時と同一でなければならない点にも注意。

実装後に `--sport kite` を付けた場合も、**同じ結果になること**が Phase 1 の完了条件です。

```bash
./.venv/Scripts/python.exe kite_clipper.py --root "$WS" --sport kite
diff "$WS/logs/result.csv" multi_sport/baseline/result_before.csv
```

> 隔離ワークスペースは毎回作り直すこと。`review/frames` に前回分が残っていると
> 抽出済みフレームの扱いが変わり、比較が成立しなくなる可能性がある。

## このベースラインがカバーする範囲

20フレーム中のフラグ分布:

| フラグ | True | False | 判定 |
|---|---|---|---|
| `has_kite_candidate` | 17 | 3 | ✅ **両分岐を通る**。器材検出のプロファイル化を検証できる |
| `has_person_candidate` | 2 | 18 | ✅ 両分岐を通る |
| `is_empty_scene` | 1 | 19 | ✅ 両分岐を通る |
| `is_blurry` | 20 | 0 | ⚠️ 片側のみ |
| `is_dark` | 0 | 20 | ⚠️ 片側のみ |
| `is_low_resolution` | 0 | 20 | ⚠️ 片側のみ |

**Phase 1 の主眼である器材検出の分岐は両方カバーできています。**

一方、ブレ・暗さ・低解像度の3判定は片側の値しか出ていません。これらは Phase 1 では
「プロファイルから既定値を読むようにするだけでロジックは不変」なので実害は小さいものの、
しきい値の受け渡しにミスがあっても20行の比較では検出できない可能性があります。

補強したい場合は、次のようにしきい値を振った実行でも一致を確認してください。

```bash
./.venv/Scripts/python.exe kite_clipper.py --root "$WS" --blur-threshold 5 --dark-threshold 200 --min-width 99999
```

（実装後に同じ引数＋`--sport kite` で同結果になれば、明示指定がプロファイル値を上書きする
優先順位も同時に検証できます = 仕様書 2.3 の確認）
