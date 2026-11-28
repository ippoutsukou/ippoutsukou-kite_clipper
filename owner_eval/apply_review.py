"""目視チェックのメモ（review_memo.txt）を採用区間に反映する（評価用の開発ツール）。

入力：
  <day>/review_memo.txt     各行「<動画名> 区間N（開始〜終了秒）: <判定> <場面>」
                            判定：OK / ずれ（内容） / 違う / 上の区間に含まれる
                            場面：① トリック ② 走行 ③ 人物・笑顔 ④ その他
  <day>/match/segments.csv  match_final.py の出力（このスクリプトは書き換えない）

出力：
  <day>/match/segments_reviewed.csv
    segments.csv と同じ列に review / scene / review_note を足したもの。
    - 「上の区間に含まれる」の行は直前の区間に統合する（照合が途切れて分かれていた区間をつなぐ）。
    - メモに無い正解範囲内の行は、同じ動画の統合後の区間に収まっていれば統合済みとして除く。
    - 正解範囲外の行（タイトル背景など）はそのまま残し、review は空にする。
  score_eval.py は segments_reviewed.csv があればそちらを使う。

使い方：
  python -I owner_eval/apply_review.py dataset/owner/2026-10-07_10 --gt-from 19.5 --gt-to 108.5
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

LINE_RE = re.compile(r"^(?P<clip>\S+) 区間(?P<n>\d+)（(?P<s>[\d.]+)〜(?P<e>[\d.]+)秒）:\s*(?P<body>.*)$")
SCENES = {"①": "1", "②": "2", "③": "3", "④": "4"}
TOL = 0.06  # メモの秒数は0.1秒単位に丸めてある


def parse_memo(path: Path) -> list[dict]:
    rows = []
    for no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            raise ValueError(f"{path.name}:{no} 形式が読めません: {line}")
        body = m["body"].strip()
        if body.startswith("上の区間に含まれる"):
            review = "含まれる"
        elif body.startswith("OK"):
            review = "OK"
        elif body.startswith("ずれ"):
            review = "ずれ"
        elif body.startswith("違う"):
            review = "違う"
        else:
            raise ValueError(f"{path.name}:{no} 判定が読めません（OK／ずれ／違う／上の区間に含まれる）: {line}")
        scenes = [v for k, v in SCENES.items() if k in body]
        if review != "含まれる" and len(scenes) != 1:
            raise ValueError(f"{path.name}:{no} 場面（①〜④）が1つに決まりません: {line}")
        rows.append({"clip": m["clip"] + ".mp4", "s": float(m["s"]), "e": float(m["e"]), "review": review,
                     "scene": scenes[0] if scenes else "", "note": body, "line": no})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    ap.add_argument("--gt-from", type=float, required=True)
    ap.add_argument("--gt-to", type=float, required=True)
    args = ap.parse_args()

    memo = parse_memo(args.day_dir / "review_memo.txt")
    src = args.day_dir / "match" / "segments.csv"
    with src.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        seg = list(reader)

    def in_gt(r):
        return float(r["final_start"]) >= args.gt_from and float(r["final_end"]) <= args.gt_to

    # メモの各行に segments.csv の行を対応させる
    used: set[int] = set()
    for m in memo:
        hits = [i for i, r in enumerate(seg) if i not in used and in_gt(r) and r["clip"] == m["clip"]
                and abs(float(r["raw_start"]) - m["s"]) <= TOL and abs(float(r["raw_end"]) - m["e"]) <= TOL]
        if len(hits) != 1:
            raise ValueError(f"メモ{m['line']}行目に対応する区間が {len(hits)} 個あります: {m['clip']} {m['s']}〜{m['e']}")
        m["row"] = hits[0]
        used.add(hits[0])

    # 「含まれる」を直前の区間に統合する
    out: dict[int, dict] = {}
    parent = None
    for m in memo:
        r = seg[m["row"]]
        if m["review"] == "含まれる":
            if parent is None or seg[parent["row"]]["clip"] != m["clip"]:
                raise ValueError(f"メモ{m['line']}行目：同じ動画の直前の区間がありません")
            p = out[parent["row"]]
            p["raw_end"] = max(float(p["raw_end"]), float(r["raw_end"]))
            p["final_end"] = max(float(p["final_end"]), float(r["final_end"]))
            p["_merged"] += 1
            continue
        out[m["row"]] = dict(r, review=m["review"], scene=m["scene"], review_note=m["note"], _merged=1)
        parent = m

    # メモに無い正解範囲内の行：統合後の区間に収まるなら除く
    for i, r in enumerate(seg):
        if i in used or not in_gt(r):
            continue
        holder = [o for o in out.values() if o["clip"] == r["clip"]
                  and float(o["raw_start"]) - TOL <= float(r["raw_start"]) and float(r["raw_end"]) <= float(o["raw_end"]) + TOL]
        if not holder:
            raise ValueError(f"メモに無い区間があります: {r['clip']} {r['raw_start']}〜{r['raw_end']}")
        holder[0]["_merged"] += 1

    # 「ずれ（N秒くらいまで…）」の秒数と、統合後の終了が合っているか確かめる
    warnings = []
    for o in out.values():
        m = re.search(r"([\d.]+)秒くらいまで", o["review_note"])
        if m and abs(float(m[1]) - float(o["raw_end"])) > 0.5:
            warnings.append(f"注意: {o['clip']} メモは{m[1]}秒まで、統合後の終了は{float(o['raw_end']):.2f}秒")

    rows = []
    for i, r in enumerate(seg):
        if i in out:
            o = out[i]
            if o["_merged"] > 1:
                o["note"] = (o["note"] + " " if o["note"] else "") + f"目視で{o['_merged']}行を統合"
            o["raw_end"] = f"{float(o['raw_end']):.2f}".rstrip("0").rstrip(".")
            o["final_end"] = f"{float(o['final_end']):.2f}".rstrip("0").rstrip(".")
            rows.append({k: v for k, v in o.items() if k != "_merged"})
        elif not in_gt(r):
            rows.append(dict(r, review="", scene="", review_note=""))

    dst = args.day_dir / "match" / "segments_reviewed.csv"
    with dst.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["review", "scene", "review_note"])
        w.writeheader()
        w.writerows(rows)

    gt_rows = [r for r in rows if r["review"]]
    count = {k: sum(r["review"] == k for r in gt_rows) for k in ("OK", "ずれ", "違う")}
    scenes = {s: sum(r["scene"] == s for r in gt_rows if r["review"] != "違う") for s in "1234"}
    print(f"メモ {len(memo)}行 → 正解区間 {len(gt_rows)}個（OK {count['OK']} / ずれ {count['ずれ']} / 違う {count['違う']}）")
    print("場面: " + " / ".join(f"{k}{scenes[v]}" for k, v in SCENES.items()))
    for w_ in warnings:
        print(w_)
    print(f"保存: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
