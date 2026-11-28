"""言葉で指定する方式（ゼロショット）で、トリックとほかの場面を見分けられるかを確かめる（評価用の開発ツール）。

目視チェック済みの区間（match/segments_reviewed.csv）のうち、①トリックと、それ以外（②走行・③人物・④風景）の
コマを0.25秒ごとに取り出し、CLIP で「トリックらしさ」を点数にする。区間ごとの最大値で①とそれ以外を比べる。

点数：トリックを表す文と、トリックでない場面を表す文を並べ、画像との近さを softmax（CLIP 標準の倍率100）にかけ、
      トリック側の確率の合計を取る（0〜1）。
前処理：アプリと同じ「短辺224に縮めて中央を切り抜く」と、「画面全体を正方形に収める（上下を黒で埋める）」の2通り。

出力（<day>/eval/）：
  trick_zeroshot_<v1|v2>.txt  区間ごとの点数と、①とそれ以外の分かれ具合（AUC）
  trick_zeroshot_frames.csv   コマごとの点数
  trick_zeroshot_top.jpg      区間ごとに点数が最も高いコマの一覧（目視確認用）

使い方：
  .venv/Scripts/python.exe -I owner_eval/trick_zeroshot.py dataset/owner/2026-10-07_10
"""
from __future__ import annotations

import argparse
import csv
import pickle
import sys
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parent.parent
VISION = ROOT / "vendor" / "clip" / "vision_model_quantized.onnx"
TEXT = ROOT / "vendor" / "clip" / "text_model_quantized.onnx"
TOKENIZER = ROOT / "vendor" / "clip" / "tokenizer.json"

MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
STEP_S = 0.25

TRICK = [
    "a photo of a kitesurfer jumping high in the air",
    "a photo of a kiteboarder doing a trick in the air above the sea",
    "a photo of a kitesurfer flying above the water with the board in the air",
]
OTHER = [
    "a photo of a kitesurfer riding on the water",
    "a photo of a person standing on the beach",
    "a photo of the sea and the sky",
    "a photo of people smiling at the camera",
]
# 1回目（2026-11-28）で、空に浮かぶカイトだけの場面がトリック扱いになったため足した文。
# 同じ26区間を見て足したので、この文での成績は実際より良く出やすい。
OTHER_V2 = OTHER + [
    "a photo of kites flying in the sky",
    "a photo of kites lying on the beach",
    "a photo of a kitesurfer standing in shallow water",
]
SCENE_NAMES = {"1": "①トリック", "2": "②走行", "3": "③人物", "4": "④風景"}


def norm(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def text_embeds(texts: list[str]) -> np.ndarray:
    tok = Tokenizer.from_file(str(TOKENIZER))
    sess = ort.InferenceSession(str(TEXT), providers=["CPUExecutionProvider"])
    out = []
    for t in texts:  # 長さがそろわないので1文ずつ
        ids = np.array([tok.encode(t).ids], np.int64)
        out.append(sess.run(None, {"input_ids": ids})[0][0])
    return norm(np.array(out, np.float32))


def prep_center(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    s = 224 / min(h, w)
    r = cv2.resize(rgb, (max(224, round(w * s)), max(224, round(h * s))), interpolation=cv2.INTER_AREA)
    t, l = (r.shape[0] - 224) // 2, (r.shape[1] - 224) // 2
    return r[t:t + 224, l:l + 224]


def prep_full(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    s = 224 / max(h, w)
    r = cv2.resize(rgb, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((224, 224, 3), np.uint8)
    t, l = (224 - r.shape[0]) // 2, (224 - r.shape[1]) // 2
    canvas[t:t + r.shape[0], l:l + r.shape[1]] = r
    return canvas


def to_tensor(img224: np.ndarray) -> np.ndarray:
    x = (img224.astype(np.float32) / 255.0 - MEAN) / STD
    return x.transpose(2, 0, 1)


def grab_frames(path: Path, start: float, end: float) -> list[tuple[float, np.ndarray]]:
    """位置合わせは最初の1回だけにして、あとは順に読み進める（コマごとに探し直すと非常に遅い）。"""
    cap = cv2.VideoCapture(str(path))
    if start > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start - 0.5) * 1000)
    frames = []
    want = start
    while want <= end + 1e-6:
        if not cap.grab():
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        if t + 1e-3 < want:
            continue
        ok, bgr = cap.retrieve()
        if ok:
            frames.append((round(want, 2), cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
        want += STEP_S
    cap.release()
    return frames


def auc(pos: list[float], neg: list[float]) -> float:
    """①の区間の点数が、それ以外の区間の点数より高い組の割合（同点は半分）。"""
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    ap.add_argument("--other", choices=["v1", "v2"], default="v2",
                    help="トリックでない側の文の組（v1＝最初の4文、v2＝カイトの文を足した7文）")
    args = ap.parse_args()

    with (args.day_dir / "match" / "segments_reviewed.csv").open(encoding="utf-8-sig") as f:
        segs = [r for r in csv.DictReader(f) if r.get("scene") and r.get("review") != "違う"]

    other = OTHER if args.other == "v1" else OTHER_V2
    tx = text_embeds(TRICK + other)
    vis = None

    # 4K動画の読み出しが遅いので、画像側の結果（埋め込みと縮小画像）を保存して使い回す
    cache_path = args.day_dir / "eval" / "trick_zeroshot_cache.pkl"
    cache = pickle.loads(cache_path.read_bytes()) if cache_path.exists() else {}

    rows, results, tops = [], [], []
    for r in segs:
        key = f"{r['clip']}|{r['raw_start']}|{r['raw_end']}|{STEP_S}"
        if key not in cache:
            frames = grab_frames(args.day_dir / "raw" / r["clip"], float(r["raw_start"]), float(r["raw_end"]))
            if not frames:
                print(f"コマを取り出せません: {r['clip']}", file=sys.stderr)
                continue
            if vis is None:
                vis = ort.InferenceSession(str(VISION), providers=["CPUExecutionProvider"])
            entry = {"t": [t for t, _ in frames],
                     "thumb": [cv2.resize(fr, (320, max(1, round(fr.shape[0] * 320 / fr.shape[1]))),
                                          interpolation=cv2.INTER_AREA) for _, fr in frames]}
            for mode, prep in (("center", prep_center), ("full", prep_full)):
                x = np.stack([to_tensor(prep(fr)) for _, fr in frames])
                entry[mode] = norm(vis.run(None, {"pixel_values": x})[0])
            cache[key] = entry
            cache_path.write_bytes(pickle.dumps(cache))
        entry = cache[key]
        frames = list(zip(entry["t"], entry["thumb"]))
        res = {}
        for mode in ("center", "full"):
            emb = entry[mode]
            logits = 100.0 * emb @ tx.T
            p = np.exp(logits - logits.max(axis=1, keepdims=True))
            p /= p.sum(axis=1, keepdims=True)
            res[mode] = p[:, :len(TRICK)].sum(axis=1)
        for i, (t, _) in enumerate(frames):
            rows.append({"clip": r["clip"], "scene": r["scene"], "t": t,
                         "center": f"{res['center'][i]:.3f}", "full": f"{res['full'][i]:.3f}"})
        best = int(np.argmax(res["full"]))
        results.append({"clip": r["clip"], "scene": r["scene"], "start": r["raw_start"], "end": r["raw_end"],
                        "n": len(frames),
                        "center_max": float(res["center"].max()), "center_mean": float(res["center"].mean()),
                        "full_max": float(res["full"].max()), "full_mean": float(res["full"].mean()),
                        "best_t": frames[best][0]})
        tops.append((r, frames[best][0], frames[best][1], float(res["full"][best])))

    out_dir = args.day_dir / "eval"
    with (out_dir / f"trick_zeroshot_frames_{args.other}.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["clip", "scene", "t", "center", "full"])
        w.writeheader()
        w.writerows(rows)

    lines = ["トリックを表す文: " + " / ".join(TRICK), "それ以外の文: " + " / ".join(other), ""]
    lines.append(f"{'動画':<24}{'場面':<8}{'範囲(秒)':>13}{'コマ':>5}"
                 f"{'中央 最大':>9}{'中央 平均':>9}{'全体 最大':>9}{'全体 平均':>9}{'最大の時刻':>9}")
    for x in sorted(results, key=lambda x: (x["scene"], -x["full_max"])):
        lines.append(f"{x['clip'][:-4]:<24}{SCENE_NAMES[x['scene']]:<8}{x['start']:>6}〜{x['end']:<6}{x['n']:>5}"
                     f"{x['center_max']:>9.2f}{x['center_mean']:>9.2f}{x['full_max']:>9.2f}{x['full_mean']:>9.2f}"
                     f"{x['best_t']:>9.2f}")
    lines.append("")
    lines.append("## ①トリックとほかの場面の分かれ具合（AUC：1.0＝完全に分かれる、0.5＝でたらめと同じ）")
    for key in ("center_max", "center_mean", "full_max", "full_mean"):
        pos = [x[key] for x in results if x["scene"] == "1"]
        for label, negs in (("②走行と", {"2"}), ("②③④すべてと", {"2", "3", "4"})):
            neg = [x[key] for x in results if x["scene"] in negs]
            lines.append(f"{key:<12} {label:<12} AUC {auc(pos, neg):.2f}（①{len(pos)}区間 / 相手{len(neg)}区間）")
    report = "\n".join(lines)
    (out_dir / f"trick_zeroshot_{args.other}.txt").write_text(report + "\n", encoding="utf-8")
    print(report)

    # 区間ごとの最高点のコマを並べた一覧（全体を収めた画像。左上に場面と点数）
    tiles = []
    for r, t, rgb, score in sorted(tops, key=lambda z: (z[0]["scene"], -z[3])):
        h, w = rgb.shape[:2]
        tile = cv2.resize(rgb, (320, max(1, round(h * 320 / w))))
        tile = cv2.copyMakeBorder(tile, 0, max(0, 240 - tile.shape[0]), 0, 0, cv2.BORDER_CONSTANT)[:240]
        bgr = cv2.cvtColor(tile, cv2.COLOR_RGB2BGR)
        color = (38, 38, 220) if r["scene"] == "1" else (255, 255, 255)
        cv2.putText(bgr, f"S{r['scene']} {score:.2f} {r['clip'][9:15]} t{t:.1f}", (6, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
        cv2.putText(bgr, f"S{r['scene']} {score:.2f} {r['clip'][9:15]} t{t:.1f}", (6, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1)
        tiles.append(bgr)
    cols = 5
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    grid = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    ok, buf = cv2.imencode(".jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ok:
        buf.tofile(str(out_dir / f"trick_zeroshot_top_{args.other}.jpg"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
