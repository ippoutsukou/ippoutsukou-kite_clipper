"""Flutter版おまかせの提案区間を、オーナーの採用区間（正解）と比べて集計する（評価用の開発ツール）。

入力：
  <day>/match/segments.csv      match_final.py が作った採用区間
  <day>/eval/flutter_omakase.json  tool/eval_omakase.dart が作った解析結果（4モデル分）

正解として使うのは、完成動画の [--gt-from, --gt-to] 秒に収まる区間だけ（タイトル背景・写真・終了画面を除く）。
範囲外の区間に使われた元素材の時間は「どちらとも言えない」として、適合率の計算から外す。

使い方：
  python -I owner_eval/score_eval.py dataset/owner/2026-10-07_10 --gt-from 19.5 --gt-to 108.5
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

STEP = 0.1  # 時間を0.1秒刻みの印で数える


def mask(intervals, duration):
    m = np.zeros(int(np.ceil(duration / STEP)) + 1, bool)
    for s, e in intervals:
        m[int(s / STEP): int(np.ceil(e / STEP))] = True
    return m


def smooth(scores, window):
    if window <= 1:
        return np.asarray(scores, float)
    half = window // 2
    s = np.asarray(scores, float)
    return np.array([s[max(0, i - half): i + half + 1].mean() for i in range(len(s))])


def propose(times_ms, scores, enter, exit_, window, pre_ms, post_ms, duration_ms, min_ms=100):
    """ai_segments.dart の buildProposedSegments と同じ手順（しきい値を変えて試す用）。"""
    sm = smooth(scores, window)
    segs, inside, start, last = [], False, 0, 0

    def close():
        s = min(max(start - pre_ms, 0), duration_ms)
        e = min(max(last + post_ms, 0), duration_ms)
        if e - s < min_ms:
            e = min(duration_ms, s + min_ms)
        if e - s >= min_ms:
            segs.append((s / 1000, e / 1000))

    for t, v in zip(times_ms, sm):
        if not inside:
            if v >= enter:
                inside, start, last = True, t, t
        elif v >= exit_:
            last = t
        else:
            close()
            inside = False
    if inside:
        close()
    return segs


def evaluate(items, preds, gt, ignore):
    """preds: {name: [(s,e)]}。時間ベースと素材単位の指標を返す。"""
    tp = fp = fn = 0.0
    used_hit = used_total = unused_zero = unused_total = 0
    total = 0.0
    for it in items:
        name, dur = it["name"], it["durationMs"] / 1000
        p = mask(preds.get(name, []), dur)
        g = mask(gt.get(name, []), dur)
        ig = mask(ignore.get(name, []), dur) & ~g
        tp += (p & g).sum() * STEP
        fp += (p & ~g & ~ig).sum() * STEP
        fn += (~p & g).sum() * STEP
        total += p.sum() * STEP
        if gt.get(name):
            used_total += 1
            used_hit += bool((p & g).any())
        elif not ignore.get(name):
            unused_total += 1
            unused_zero += not p.any()
    return {
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "total_s": total,
        "used_hit": f"{used_hit}/{used_total}",
        "unused_zero": f"{unused_zero}/{unused_total}",
    }


def topk_preds(items, model_id, target_s, pre_ms, post_ms, window):
    """全素材の平滑化後の点数を高い順に採り、合計が target_s に達するまで1秒ずつ足す方式。"""
    cand = []
    for it in items:
        sm = smooth(it["models"][model_id]["scores"], window)
        for t, v in zip(it["timesMs"], sm):
            cand.append((v, it["name"], t, it["durationMs"]))
    cand.sort(key=lambda c: -c[0])
    preds, acc = {}, 0.0
    for v, name, t, dur in cand:
        if acc >= target_s:
            break
        s, e = max(0, t - pre_ms) / 1000, min(dur, t + 1000 + post_ms) / 1000
        preds.setdefault(name, []).append((s, e))
        acc += e - s  # 重なりは数えすぎになるが、目安として十分
    return preds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    ap.add_argument("--gt-from", type=float, required=True)
    ap.add_argument("--gt-to", type=float, required=True)
    args = ap.parse_args()

    data = json.loads((args.day_dir / "eval" / "flutter_omakase.json").read_text(encoding="utf-8"))
    items = [it for it in data["items"] if "error" not in it]
    errors = [it["name"] for it in data["items"] if "error" in it]

    # 目視チェック済み（apply_review.py の出力）があればそちらを使う
    seg_path = args.day_dir / "match" / "segments_reviewed.csv"
    if not seg_path.exists():
        seg_path = args.day_dir / "match" / "segments.csv"
    gt, ignore, by_scene = {}, {}, {}
    with seg_path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("review") == "違う":  # 照合の誤り：正解にも「どちらとも言えない」にも入れない
                continue
            seg = (float(r["raw_start"]), float(r["raw_end"]))
            inside = float(r["final_start"]) >= args.gt_from and float(r["final_end"]) <= args.gt_to
            (gt if inside else ignore).setdefault(r["clip"], []).append(seg)
            if inside and r.get("scene"):
                by_scene.setdefault(r["scene"], {}).setdefault(r["clip"], []).append(seg)
    gt_total = sum(mask(v, 600).sum() * STEP for v in gt.values())
    raw_total = sum(it["durationMs"] for it in items) / 1000

    lines = [f"正解: {seg_path.name}",
             f"素材 {len(items)}本 / {raw_total:.1f}秒、正解区間 {sum(len(v) for v in gt.values())}個 / {gt_total:.1f}秒"
             f"（{len(gt)}本）、正解に使わない素材時間あり {len(ignore)}本",
             f"解析に失敗: {errors or 'なし'}", ""]

    header = f"{'方式':<34}{'再現率':>7}{'適合率':>7}{'合計秒':>8}  {'使用素材に提案':>10}  {'未使用素材が0個':>10}"
    lines += ["## アプリの今の設定（おまかせのまま）", header]
    for mid, cfg in data["models"].items():
        preds = {it["name"]: [(s["startMs"] / 1000, s["endMs"] / 1000) for s in it["models"][mid]["segments"]]
                 for it in items}
        m = evaluate(items, preds, gt, ignore)
        label = f"{mid} ({cfg['enter']}/{cfg['exit']})"
        lines.append(f"{label:<34}"
                     f"{m['recall']:>7.0%}{m['precision']:>7.0%}{m['total_s']:>8.1f}  {m['used_hit']:>10}  {m['unused_zero']:>10}")

    if by_scene:
        names = {"1": "①トリック", "2": "②走行", "3": "③人物・笑顔", "4": "④その他"}
        scene_ids = sorted(by_scene)
        lines += ["", "## 場面ごとの再現率（アプリの今の設定・目視チェックの場面ラベル）",
                  f"{'方式':<34}" + "".join(
                      f"{names.get(s, s) + '(' + str(sum(len(v) for v in by_scene[s].values())) + ')':>14}"
                      for s in scene_ids)]
        for mid in data["models"]:
            preds = {it["name"]: [(s["startMs"] / 1000, s["endMs"] / 1000) for s in it["models"][mid]["segments"]]
                     for it in items}
            cells = "".join(f"{evaluate(items, preds, by_scene[s], {})['recall']:>14.0%}" for s in scene_ids)
            lines.append(f"{mid:<34}{cells}")

    lines += ["", "## しきい値を変えた場合（enter を動かし、exit との差はモデルの設定のまま）", header]
    for mid, cfg in data["models"].items():
        gap = cfg["enter"] - cfg["exit"]
        for enter in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
            preds = {it["name"]: propose(it["timesMs"], it["models"][mid]["scores"], enter, enter - gap,
                                         cfg["smoothWindow"], cfg["preRollMs"], cfg["postRollMs"], it["durationMs"])
                     for it in items}
            m = evaluate(items, preds, gt, ignore)
            lines.append(f"{f'{mid} enter={enter:.1f}':<34}{m['recall']:>7.0%}{m['precision']:>7.0%}"
                         f"{m['total_s']:>8.1f}  {m['used_hit']:>10}  {m['unused_zero']:>10}")

    lines += ["", f"## 目標の長さで上位から選ぶ場合（目標＝正解の合計 {gt_total:.0f}秒）", header]
    for mid, cfg in data["models"].items():
        preds = topk_preds(items, mid, gt_total, cfg["preRollMs"], cfg["postRollMs"], cfg["smoothWindow"])
        m = evaluate(items, preds, gt, ignore)
        lines.append(f"{mid + ' 上位から':<34}{m['recall']:>7.0%}{m['precision']:>7.0%}"
                     f"{m['total_s']:>8.1f}  {m['used_hit']:>10}  {m['unused_zero']:>10}")
    # 偶然の基準：素材全体からランダムに同じ長さを選んだときの再現率は、おおよそ「目標秒数／素材全体」。
    lines.append(f"{'（参考）でたらめに選んだ場合の目安':<30}{gt_total / raw_total:>9.0%}{gt_total / raw_total:>7.0%}")

    report = "\n".join(lines)
    print(report)
    (args.day_dir / "eval" / "report.txt").write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
