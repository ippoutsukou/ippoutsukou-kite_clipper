"""元素材すべてのコマを「トリックらしさ」で採点し、点数の高い場面を上から並べる（評価用の開発ツール）。

trick_zeroshot.py の確認で「オーナーが使った区間の中」では一部見分けられたので、
使われなかった素材も含めたとき、上位にトリックでない場面がどれだけ混ざるかを目視で確かめるための一覧を作る。

手順：
  1. FFmpeg で各動画から 0.25秒ごとのコマを取り出す（幅640に縮小、GPUデコードを試す）。
  2. 画面全体を正方形に収める前処理で CLIP に通し、trick_zeroshot.py の v2 の文で点数を付ける。
  3. 同じ動画で1.5秒以内の高得点は同じ場面とみなし、山の頂上だけを残す。
  4. 点数の高い順に --top 個を一覧画像にする。オーナーが使った区間に入っていれば、その印（S1〜S4）を添える。

出力（<day>/eval/）：
  trick_all_frames.csv  全コマの点数
  trick_all_top.csv     上位の場面（順位・動画・時刻・点数・オーナーの印）
  trick_all_top.jpg     上位の場面の一覧画像（目視用）
  trick_all_cache.pkl   画像側の結果（再実行で使い回す）

使い方：
  .venv/Scripts/python.exe -I owner_eval/trick_topk.py dataset/owner/2026-10-07_10 --top 40
"""
from __future__ import annotations

import argparse
import csv
import pickle
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trick_zeroshot import OTHER_V2, TRICK, VISION, norm, prep_full, text_embeds, to_tensor  # noqa: E402

FPS = 4  # 0.25秒ごと
WIDTH = 640
PEAK_GAP_S = 1.5


def probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:stream_side_data=rotation", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True).stdout.split()
    w, h = (int(v) for v in out[0].split(",")[:2])
    return w, h


def decode(path: Path) -> list[np.ndarray]:
    """FFmpeg で 0.25秒ごとのコマ（RGB、幅640）を取り出す。回転情報は FFmpeg が自動で反映する。"""
    base = ["ffmpeg", "-v", "error"]
    tail = ["-i", str(path), "-vf", f"fps={FPS},scale={WIDTH}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    for extra in (["-hwaccel", "auto"], []):
        proc = subprocess.run(base + extra + tail, capture_output=True)
        if proc.returncode == 0 and proc.stdout:
            break
    else:
        raise RuntimeError(f"FFmpeg で読めません: {path.name}: {proc.stderr[-300:]!r}")
    n_px = len(proc.stdout) // 3
    # 出力の高さは元の縦横比から決まる（縦撮りで回転している場合も考え、両方の向きを試す）
    w, h = probe_size(path)
    for hh in sorted({round(WIDTH * h / w / 2) * 2, round(WIDTH * w / h / 2) * 2}):
        if (n_px % (WIDTH * hh)) == 0:
            arr = np.frombuffer(proc.stdout, np.uint8).reshape(-1, hh, WIDTH, 3)
            return list(arr)
    raise RuntimeError(f"コマの大きさが決まりません: {path.name} ({w}x{h}, {n_px}画素)")


def load_owner_labels(day_dir: Path) -> dict[str, list[tuple[float, float, str]]]:
    labels: dict[str, list[tuple[float, float, str]]] = {}
    with (day_dir / "match" / "segments_reviewed.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("scene") and r.get("review") != "違う":
                labels.setdefault(r["clip"], []).append((float(r["raw_start"]), float(r["raw_end"]), r["scene"]))
    return labels


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    raw_dir = args.day_dir / "raw"
    out_dir = args.day_dir / "eval"
    videos = sorted(p for p in raw_dir.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".m4v"})
    cache_path = out_dir / "trick_all_cache.pkl"
    cache = pickle.loads(cache_path.read_bytes()) if cache_path.exists() else {}

    vis = None
    for i, v in enumerate(videos, 1):
        key = f"{v.name}|{v.stat().st_size}|{FPS}|{WIDTH}"
        if key in cache:
            continue
        frames = decode(v)
        if vis is None:
            vis = ort.InferenceSession(str(VISION), providers=["CPUExecutionProvider"])
        embs = []
        for s in range(0, len(frames), 32):
            x = np.stack([to_tensor(prep_full(fr)) for fr in frames[s:s + 32]])
            embs.append(norm(vis.run(None, {"pixel_values": x})[0]))
        cache[key] = {"name": v.name, "emb": np.concatenate(embs)}
        cache_path.write_bytes(pickle.dumps(cache))
        print(f"[{i}/{len(videos)}] {v.name} {len(frames)}コマ", flush=True)

    tx = text_embeds(TRICK + OTHER_V2)
    labels = load_owner_labels(args.day_dir)

    def owner_label(name: str, t: float) -> str:
        for s, e, sc in labels.get(name, []):
            if s - 0.13 <= t <= e + 0.13:
                return f"S{sc}"
        return ""

    all_rows, peaks = [], []
    for v in videos:
        entry = next(c for k, c in cache.items() if k.startswith(v.name + "|"))
        logits = 100.0 * entry["emb"] @ tx.T
        p = np.exp(logits - logits.max(axis=1, keepdims=True))
        p /= p.sum(axis=1, keepdims=True)
        score = p[:, :len(TRICK)].sum(axis=1)
        for j, sc in enumerate(score):
            all_rows.append({"clip": v.name, "t": f"{j / FPS:.2f}", "score": f"{sc:.3f}",
                             "owner": owner_label(v.name, j / FPS)})
        # 山の頂上だけを残す（高い順に取り、1.5秒以内の近いコマを消す）
        taken = np.zeros(len(score), bool)
        for j in np.argsort(-score):
            lo, hi = max(0, j - int(PEAK_GAP_S * FPS)), j + int(PEAK_GAP_S * FPS) + 1
            if taken[lo:hi].any():
                continue
            taken[j] = True
            peaks.append((float(score[j]), v, j / FPS))

    with (out_dir / "trick_all_frames.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["clip", "t", "score", "owner"])
        w.writeheader()
        w.writerows(all_rows)

    peaks.sort(key=lambda x: -x[0])
    top = peaks[:args.top]
    with (out_dir / "trick_all_top.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "clip", "t", "score", "owner", "judge"])
        for k, (sc, v, t) in enumerate(top, 1):
            w.writerow([k, v.name, f"{t:.2f}", f"{sc:.3f}", owner_label(v.name, t), ""])

    # 一覧画像：上位のコマを取り出し直す（キャッシュには画像を持たないため）
    tiles = []
    for k, (sc, v, t) in enumerate(top, 1):
        proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(v), "-frames:v", "1",
                               "-vf", "scale=320:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                              capture_output=True)
        img = cv2.imdecode(np.frombuffer(proc.stdout, np.uint8), cv2.IMREAD_COLOR) if proc.stdout else None
        if img is None:
            img = np.zeros((180, 320, 3), np.uint8)
        img = cv2.resize(img, (320, round(img.shape[0] * 320 / img.shape[1])))
        img = cv2.copyMakeBorder(img, 0, max(0, 240 - img.shape[0]), 0, 0, cv2.BORDER_CONSTANT)[:240]
        lab = owner_label(v.name, t)
        text = f"#{k} {sc:.2f} {v.stem[9:]} t{t:.1f} {lab}"
        cv2.putText(img, text, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
        cv2.putText(img, text, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (38, 38, 220) if lab == "S1" else (255, 255, 255), 1)
        tiles.append(img)
    cols = 5
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    grid = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    ok, buf = cv2.imencode(".jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ok:
        buf.tofile(str(out_dir / "trick_all_top.jpg"))

    n_frames = len(all_rows)
    print(f"採点したコマ: {n_frames}（{n_frames / FPS:.0f}秒分）、山の数: {len(peaks)}")
    for th in (0.97, 0.95, 0.93, 0.90):
        print(f"点数 {th} 以上の山: {sum(s >= th for s, _, _ in peaks)}個")
    print(f"上位{len(top)}個のうちオーナーが使った区間に入っているもの: "
          + ", ".join(f"{lab or '使っていない'} {sum(owner_label(v.name, t) == lab for _, v, t in top)}"
                      for lab in ("S1", "S2", "S3", "S4", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
