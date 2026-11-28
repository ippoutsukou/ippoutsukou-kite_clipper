"""③人物・笑顔を「言葉で指定」と「印で学習」で見分けられるかを確かめる（評価用の開発ツール）。

トリックで行った確認（trick_zeroshot.py → trick_topk.py → trick_learn.py）を、③人物・笑顔について1本で行う。
CLIP の画像特徴は trick_topk.py が作った <day>/eval/trick_all_cache.pkl（0.25秒ごと・画面全体）を使い回す。

印がまだないとき（<day>/eval/person_labels.txt がない）：
  言葉で指定した点数だけを出す。オーナーが③として使った区間（segments_reviewed.csv の scene=3）と比べた目安と、
  点数の高い上位40場面の一覧（person_top.csv）を作る。印付けで探す手がかりに使える。
印があるとき（make_trick_label_page.py --kind person の「印の一覧を作る」の出力を保存したもの）：
  「人物」（人物＋笑顔）と「笑顔」のそれぞれを正解にして、言葉で指定・印で学習（動画単位の5分割／日付で分割）を
  同じ物差しで比べる（AUC、上位40場面のうち正解の数、正解の場面のうち拾えた数）。

出力：<day>/eval/person_eval.txt、person_top.csv

使い方：
  .venv/Scripts/python.exe -I owner_eval/person_eval.py dataset/owner/2026-10-07_10
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
from trick_learn import FPS, evaluate, parse_runs, peaks  # noqa: E402
from trick_zeroshot import text_embeds  # noqa: E402

PERSON = [
    "a close-up photo of a person",
    "a photo of a smiling person looking at the camera",
    "a kitesurfer standing close to the camera holding the control bar",
    "people standing together on the beach",
]
SMILE = [PERSON[1]]  # 笑顔の点数に使う文（PERSON の一部）
# 人物の場面（浅瀬や浜でバーを持つ人を近くから撮ったもの）を吸い取らないよう、人が近くに写る文は入れない
OTHER = [
    "a kitesurfer riding on the water far away",
    "a kitesurfer jumping high in the air",
    "kites flying in the sky",
    "a photo of the sea and the sky",
    "kites lying on the beach",
    "a blurry photo of water",
]
C = 0.1


def load_labels(path: Path) -> dict[str, dict[str, list]]:
    """1行＝ `動画: 人物の連なり / 笑顔 連なり / 迷う 連なり`（後ろ2つは省略あり）。"""
    out = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^(\S+\.(?:mp4|mov|m4v)):\s*(.*)$", line.strip(), re.I)
        if not m:
            continue
        parts = [p.strip() for p in m[2].split("/")]
        d = {"人物": parse_runs(parts[0]), "笑顔": [], "迷う": []}
        for p in parts[1:]:
            name, _, rest = p.partition(" ")
            if name in d:
                d[name] = parse_runs(rest)
        out[m[1]] = d
    return out


def softmax_score(emb: np.ndarray, tx: np.ndarray, pos: list[int]) -> np.ndarray:
    logits = 100.0 * emb @ tx.T
    p = np.exp(logits - logits.max(axis=1, keepdims=True))
    p /= p.sum(axis=1, keepdims=True)
    return p[:, pos].sum(axis=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    ap.add_argument("--labels", type=Path, help="印の一覧（既定は <day>/eval/person_labels.txt）")
    args = ap.parse_args()
    ev = args.day_dir / "eval"
    lab_path = args.labels or ev / "person_labels.txt"
    labels = load_labels(lab_path) if lab_path.exists() else None

    cache = pickle.loads((ev / "trick_all_cache.pkl").read_bytes())
    prompts = PERSON + OTHER
    tx = text_embeds(prompts)
    owner: dict[str, list[tuple[float, float]]] = {}
    with (args.day_dir / "match" / "segments_reviewed.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("scene") == "3" and r.get("review") != "違う":
                owner.setdefault(r["clip"], []).append((float(r["raw_start"]), float(r["raw_end"])))

    clips = []
    for e in sorted(cache.values(), key=lambda e: e["name"]):
        emb = e["emb"]
        clips.append({"name": e["name"], "day": e["name"][:8], "emb": emb, "t": np.arange(len(emb)) / FPS,
                      "zs_person": softmax_score(emb, tx, list(range(len(PERSON)))),
                      "zs_smile": softmax_score(emb, tx, [prompts.index(s) for s in SMILE])})

    lines = []
    # 目安：オーナーが③に使った区間の中の最高点と、それ以外の動画のコマの点数を比べる
    in_max = [float(c["zs_person"][(c["t"] >= s) & (c["t"] <= e2)].max())
              for c in clips for s, e2 in owner.get(c["name"], []) if ((c["t"] >= s) & (c["t"] <= e2)).any()]
    lines.append(f"## 言葉で指定（印なしの目安）")
    lines.append(f"オーナーが③に使った区間 {len(in_max)}個の最高点：" + ", ".join(f"{v:.2f}" for v in sorted(in_max)))
    allp = sorted(((float(c["zs_person"][j]), c, j) for c in clips for j in peaks(c["zs_person"])), key=lambda x: -x[0])
    for th in (0.9, 0.7, 0.5):
        lines.append(f"点数 {th} 以上の山：{sum(s >= th for s, _, _ in allp)}個（全 {len(allp)}個）")

    def owner_mark(c: dict, t: float) -> str:
        return "S3" if any(s - 0.13 <= t <= e2 + 0.13 for s, e2 in owner.get(c["name"], [])) else ""

    top = allp[:40]
    lines.append(f"上位40場面のうちオーナーが③に使った区間内：{sum(bool(owner_mark(c, j / FPS)) for _, c, j in top)}")

    if labels is not None:
        n_unk_total = 0
        for c in clips:
            d = labels.get(c["name"], {"人物": [], "笑顔": [], "迷う": []})
            t = c["t"]
            unk = np.zeros(len(t), bool)
            for a, b in d["迷う"]:
                unk |= (t >= a - 0.25) & (t <= b + 0.25)
            person = np.zeros(len(t), bool)
            smile = np.zeros(len(t), bool)
            for a, b in d["人物"]:
                person |= (t >= a - 0.25) & (t <= b + 0.25)
            for a, b in d["笑顔"]:
                smile |= (t >= a - 0.25) & (t <= b + 0.25)
            person |= smile
            c["y_person"] = np.where(unk & ~person, -1, person.astype(int))
            c["y_smile"] = np.where(unk & ~smile, -1, smile.astype(int))
            # 場面（印の連なり）：人物は人物と笑顔の連なりをつなげたもの
            runs_p = sorted(d["人物"] + d["笑顔"])
            merged: list[list[float]] = []
            for a, b in runs_p:
                if merged and a <= merged[-1][1] + 0.5 + 1e-6:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            c["ev_person"] = [tuple(m) for m in merged]
            c["ev_smile"] = d["笑顔"]
            n_unk_total += int(unk.sum())
        missing = sorted(set(labels) - {c["name"] for c in clips})
        lines.append("")
        lines.append(f"印：人物の場面 {sum(len(c['ev_person']) for c in clips)}個（コマ {sum(int((c['y_person'] == 1).sum()) for c in clips)}）・"
                     f"笑顔の場面 {sum(len(c['ev_smile']) for c in clips)}個（コマ {sum(int((c['y_smile'] == 1).sum()) for c in clips)}）・迷う {n_unk_total}コマ")
        if missing:
            lines.append(f"注意：特徴が見つからない動画の印は使っていません: {', '.join(missing)}")

        for target in ("person", "smile"):
            for c in clips:
                c["y"], c["events"] = c[f"y_{target}"], c[f"ev_{target}"]
                c["zs"] = c[f"zs_{target}"]

            def fit_predict(train: list[dict], test: list[dict], key: str) -> None:
                X = np.concatenate([c["emb"][c["y"] >= 0] for c in train])
                Y = np.concatenate([c["y"][c["y"] >= 0] for c in train])
                for c in test:
                    c[key] = (LogisticRegression(C=C, class_weight="balanced", max_iter=2000).fit(X, Y)
                              .predict_proba(c["emb"])[:, 1] if len(set(Y)) == 2 else np.zeros(len(c["emb"])))

            order = sorted(range(len(clips)), key=lambda i: -len(clips[i]["events"]))
            folds = [[clips[i] for k, i in enumerate(order) if k % 5 == f] for f in range(5)]
            for fo in folds:
                fit_predict([c for c in clips if c not in fo], fo, "cv")
            for day in sorted({c["day"] for c in clips}):
                fit_predict([c for c in clips if c["day"] != day], [c for c in clips if c["day"] == day], "day_cv")
            if sum(len(c["events"]) for c in clips) == 0:
                continue
            what = "人物" if target == "person" else "笑顔"
            lines.append("")
            lines.append(f"## {'人物（人物＋笑顔）' if target == 'person' else '笑顔'}（学習に使わなかった動画の点数で測定）")
            evaluate("言葉で指定          ", clips, "zs", lines, what=what)
            evaluate("印で学習（5分割）   ", clips, "cv", lines, what=what)
            evaluate("印で学習（日付で分割）", clips, "day_cv", lines, what=what)

    (ev / "person_eval.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (ev / "person_top.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "clip", "t", "score", "owner"])
        for k, (sc, c, j) in enumerate(top, 1):
            w.writerow([k, c["name"], f"{j / FPS:.2f}", f"{sc:.3f}", owner_mark(c, j / FPS)])
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
