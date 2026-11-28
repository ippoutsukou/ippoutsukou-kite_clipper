"""「空中の印」を正解にして、CLIP の画像特徴からトリック（空中）を当てるモデルを作り、評価する（評価用の開発ツール）。

入力：
  <day>/eval/air_labels.txt      印付けページ（make_trick_label_page.py）の「印の一覧を作る」の出力をそのまま保存したもの
  <day>/eval/trick_all_cache.pkl  trick_topk.py が作った、0.25秒ごとのコマの CLIP 画像特徴

正解の作り方（0.25秒ごとのコマ）：
  空中の印（0.5秒ごと）の連なり a〜b に対し、a-0.25〜b+0.25 のコマを「空中」、
  迷うの印の前後0.25秒は学習・評価から外し、それ以外は「空中でない」とする。

評価：
  学習に使った動画で測ると甘くなるので、動画単位で分けて、学習に使わなかった動画の点数だけで測る。
    - 5分割（動画単位）
    - 日付で分割（10/07 だけで学習して 10/10 を当てる、とその逆）
  言葉で指定する方式（trick_zeroshot.py の v2）と同じ物差しで比べる：
    - コマ単位の AUC
    - 上位40場面（1.5秒以内の山をまとめる）のうち、本当に空中だった割合
    - 空中の場面（印の連なり）のうち、上位N場面に拾えた割合

出力：<day>/eval/trick_learn.txt（成績）、trick_learn_top.csv（5分割での上位場面）

使い方：
  .venv/Scripts/python.exe -I owner_eval/trick_learn.py dataset/owner/2026-10-07_10
"""
from __future__ import annotations

import argparse
import csv
import pickle
import re
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trick_zeroshot import OTHER_V2, TRICK, text_embeds  # noqa: E402

FPS = 4
PEAK_GAP_S = 1.5
C = 0.1  # frame_scorer.py の CLIP_LOGREG_C と同じ


def parse_runs(text: str) -> list[tuple[float, float]]:
    runs = []
    for part in text.split(","):
        part = part.strip()
        if not part or part == "-":
            continue
        a, _, b = part.partition("-")
        runs.append((float(a), float(b or a)))
    return runs


def load_labels(path: Path) -> dict[str, tuple[list, list]]:
    out = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^(?P<clip>\S+\.(?:mp4|mov|m4v)):\s*(?P<air>[^/]*)(?:/\s*迷う\s*(?P<unk>.*))?$", line.strip(), re.I)
        if m:
            out[m["clip"]] = (parse_runs(m["air"]), parse_runs(m["unk"] or ""))
    return out


def peaks(score: np.ndarray) -> list[int]:
    taken = np.zeros(len(score), bool)
    out = []
    for j in np.argsort(-score):
        lo, hi = max(0, j - int(PEAK_GAP_S * FPS)), j + int(PEAK_GAP_S * FPS) + 1
        if not taken[lo:hi].any():
            taken[j] = True
            out.append(int(j))
    return out


def evaluate(name: str, clips: list[dict], key: str, lines: list[str], top_n: int = 40,
             what: str = "空中") -> list[tuple]:
    y = np.concatenate([c["y"] for c in clips])
    s = np.concatenate([c[key] for c in clips])
    use = y >= 0
    auc = roc_auc_score(y[use], s[use]) if len(set(y[use])) == 2 else float("nan")
    allp = []
    for c in clips:
        for j in peaks(c[key]):
            allp.append((float(c[key][j]), c, j))
    allp.sort(key=lambda x: -x[0])
    top = allp[:top_n]
    hit = sum(c["y"][j] == 1 for _, c, j in top)
    unk = sum(c["y"][j] == -1 for _, c, j in top)
    n_events = sum(len(c["events"]) for c in clips)
    found = set()
    k_rec = max(top_n, 2 * n_events)
    for _, c, j in allp[:k_rec]:
        t = j / FPS
        for e_i, (a, b) in enumerate(c["events"]):
            if a - 0.5 <= t <= b + 0.5:
                found.add((c["name"], e_i))
    lines.append(f"{name}: AUC {auc:.3f} / 上位{top_n}場面のうち{what} {hit}（迷う {unk}）"
                 f" / {what}の場面{n_events}個のうち上位{k_rec}場面で拾えた {len(found)}")
    return top


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    ap.add_argument("--labels", type=Path, help="印の一覧のファイル（既定は <day>/eval/air_labels.txt）")
    ap.add_argument("--out-dir", type=Path, help="成績の保存先（既定は <day>/eval）")
    args = ap.parse_args()
    ev = args.day_dir / "eval"
    out = args.out_dir or ev

    labels = load_labels(args.labels or ev / "air_labels.txt")
    cache = pickle.loads((ev / "trick_all_cache.pkl").read_bytes())
    tx = text_embeds(TRICK + OTHER_V2)

    clips = []
    for entry in sorted(cache.values(), key=lambda e: e["name"]):
        emb = entry["emb"]
        t = np.arange(len(emb)) / FPS
        air, unk = labels.get(entry["name"], ([], []))
        y = np.zeros(len(emb), int)
        for a, b in unk:
            y[(t >= a - 0.25) & (t <= b + 0.25)] = -1
        for a, b in air:
            y[(t >= a - 0.25) & (t <= b + 0.25)] = 1
        logits = 100.0 * emb @ tx.T
        p = np.exp(logits - logits.max(axis=1, keepdims=True))
        p /= p.sum(axis=1, keepdims=True)
        clips.append({"name": entry["name"], "day": entry["name"][:8], "emb": emb, "y": y, "events": air,
                      "zs": p[:, :len(TRICK)].sum(axis=1), "cv": np.zeros(len(emb)), "day_cv": np.zeros(len(emb))})

    missing = sorted(set(labels) - {c["name"] for c in clips})
    n_pos = sum(int((c["y"] == 1).sum()) for c in clips)
    n_ev = sum(len(c["events"]) for c in clips)
    lines = [f"印のあった動画 {sum(bool(c['events']) for c in clips)}/{len(clips)}本・空中の場面 {n_ev}個・"
             f"空中のコマ {n_pos}（0.25秒ごと）・迷う {sum(int((c['y'] == -1).sum()) for c in clips)}"]
    if missing:
        lines.append(f"注意：特徴が見つからない動画の印は使っていません: {', '.join(missing)}")

    def fit_predict(train: list[dict], test: list[dict], key: str) -> None:
        X = np.concatenate([c["emb"][c["y"] >= 0] for c in train])
        Y = np.concatenate([c["y"][c["y"] >= 0] for c in train])
        if len(set(Y)) < 2:
            for c in test:
                c[key] = np.zeros(len(c["emb"]))
            return
        m = LogisticRegression(C=C, class_weight="balanced", max_iter=2000).fit(X, Y)
        for c in test:
            c[key] = m.predict_proba(c["emb"])[:, 1]

    # 5分割（動画単位）。空中の場面がある動画が各組に散らばるよう、場面数の多い順に配る
    order = sorted(range(len(clips)), key=lambda i: -len(clips[i]["events"]))
    folds = [[] for _ in range(5)]
    for k, i in enumerate(order):
        folds[k % 5].append(clips[i])
    for f in folds:
        fit_predict([c for c in clips if c not in f], f, "cv")
    # 日付で分割
    for day in sorted({c["day"] for c in clips}):
        fit_predict([c for c in clips if c["day"] != day], [c for c in clips if c["day"] == day], "day_cv")

    lines.append("")
    lines.append("## 全素材（学習に使わなかった動画の点数で測定）")
    evaluate("言葉で指定（v2）    ", clips, "zs", lines)
    top = evaluate("印で学習（5分割）   ", clips, "cv", lines)
    evaluate("印で学習（日付で分割）", clips, "day_cv", lines)
    for day in sorted({c["day"] for c in clips}):
        sub = [c for c in clips if c["day"] == day]
        lines.append(f"\n## {day} の動画だけ（{len(sub)}本）")
        evaluate("言葉で指定（v2）    ", sub, "zs", lines, 20)
        evaluate("印で学習（5分割）   ", sub, "cv", lines, 20)
        evaluate("印で学習（日付で分割）", sub, "day_cv", lines, 20)

    (out / "trick_learn.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (out / "trick_learn_top.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "clip", "t", "score", "label"])
        for k, (sc, c, j) in enumerate(top, 1):
            w.writerow([k, c["name"], f"{j / FPS:.2f}", f"{sc:.3f}", {1: "空中", -1: "迷う", 0: ""}[int(c["y"][j])]])
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
