"""オーナーの完成動画と元素材を照合し、採用区間（正解）の一覧を作る（評価用の開発ツール）。

方式：
  1. 完成動画・元素材からコマを一定間隔で取り出す（FFmpeg）。
  2. 元素材の全コマの SIFT 特徴をまとめた索引を作り、完成動画の各コマで「似た特徴が多い元コマ」を投票で絞る。
  3. 上位候補だけ射影変換（RANSAC）で確かめ、一致点（inlier）が最も多い元コマを採る。
     SIFT は拡大縮小に強いので、完成側でズーム・切り抜きされていても照合できる。
  4. 同じ元素材で「元の時刻 − 完成の時刻」がほぼ一定の連続コマを1区間にまとめる。

出力（<day>/match/）：
  frames.csv   完成動画のコマごとの照合結果
  segments.csv 採用区間（元素材名・開始・終了秒）
  review_*.jpg 目視確認用（完成側と元素材側のコマを並べた一覧）

使い方：
  python -I owner_eval/match_final.py dataset/owner/2026-10-07_10
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def extract_frames(video: Path, out_dir: Path, fps: float, long_side: int) -> list[tuple[float, Path]]:
    """fps でコマを取り出す。既にあれば再利用。戻り値は (時刻秒, パス)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("*.jpg"))
    if not existing:
        scale = f"scale='if(gt(iw,ih),{long_side},-2)':'if(gt(iw,ih),-2,{long_side})'"
        run([
            "ffmpeg", "-v", "error", "-i", str(video),
            "-vf", f"fps={fps},{scale}", "-q:v", "3", str(out_dir / "%05d.jpg"),
        ])
        existing = sorted(out_dir.glob("*.jpg"))
    # fps フィルタの n 番目のコマは時刻 n/fps 付近（先頭は0）。
    return [(i / fps, p) for i, p in enumerate(existing)]


def read_gray(path: Path, mask_logo: bool = False) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if mask_logo:
        h, w = img.shape
        img[int(h * 0.88):, int(w * 0.94):] = 0  # 右下のチャンネル登録ロゴ
    return img


@dataclass
class RawFrame:
    clip: str
    t: float
    path: Path
    kp: np.ndarray  # (n,2) 座標
    des: np.ndarray


def sift_features(sift, img, max_kp: int):
    kps, des = sift.detectAndCompute(img, None)
    if des is None or len(kps) < 8:
        return np.zeros((0, 2), np.float32), np.zeros((0, 128), np.float32)
    order = np.argsort([-k.response for k in kps])[:max_kp]
    pts = np.array([kps[i].pt for i in order], np.float32)
    return pts, des[order].astype(np.float32)


def verify(final_kp, final_des, raw: RawFrame, matcher) -> int:
    """比率テスト＋射影変換RANSACの一致点数。"""
    if len(raw.des) < 8 or len(final_des) < 8:
        return 0
    pairs = matcher.knnMatch(final_des, raw.des, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.8 * n.distance]
    if len(good) < 8:
        return 0
    src = final_kp[[m.queryIdx for m in good]]
    dst = raw.kp[[m.trainIdx for m in good]]
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
    if H is None:
        return 0
    return int(mask.sum())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path, help="raw/ と final/ を含むフォルダ")
    ap.add_argument("--final-fps", type=float, default=4.0)
    ap.add_argument("--raw-fps", type=float, default=4.0)
    ap.add_argument("--top", type=int, default=12, help="RANSACで確かめる候補コマ数")
    ap.add_argument("--min-inliers", type=int, default=25, help="一致とみなす最小一致点数")
    ap.add_argument("--fill-min-inliers", type=int, default=10,
                    help="前後の区間から時刻を予測して探すときの最小一致点数（水しぶき等で特徴が少ないコマ用）")
    ap.add_argument("--title-end", type=float, default=0.0,
                    help="完成動画の冒頭でタイトル文字が重なる区間の終わり（秒）。この区間の照合は note に印を付ける")
    ap.add_argument("--max-gap", type=float, default=3.0, help="補う対象にする一致なし区間の最大長（秒）")
    args = ap.parse_args()

    day: Path = args.day_dir
    raw_videos = sorted(p for p in (day / "raw").iterdir() if p.suffix.lower() in VIDEO_EXTS)
    finals = sorted(p for p in (day / "final").iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not raw_videos or len(finals) != 1:
        print("エラー: raw/ に動画、final/ に完成動画1本が必要です")
        return 1
    work = day / "work"
    out = day / "match"
    out.mkdir(exist_ok=True)

    sift = cv2.SIFT_create(nfeatures=1500)
    matcher = cv2.BFMatcher(cv2.NORM_L2)

    # --- 元素材 ---
    raw_frames: list[RawFrame] = []
    durations: dict[str, float] = {}
    for i, video in enumerate(raw_videos, 1):
        durations[video.name] = probe_duration(video)
        frames = extract_frames(video, work / "raw" / video.stem, args.raw_fps, 960)
        for t, p in frames:
            kp, des = sift_features(sift, read_gray(p), 800)
            raw_frames.append(RawFrame(video.name, t, p, kp, des))
        print(f"元素材 {i}/{len(raw_videos)} {video.name}: {len(frames)}コマ", flush=True)

    all_des = np.concatenate([r.des for r in raw_frames if len(r.des)])
    owner = np.concatenate([np.full(len(r.des), i, np.int32) for i, r in enumerate(raw_frames) if len(r.des)])
    index = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 64})
    index.add([all_des])
    index.train()
    print(f"索引: 元コマ {len(raw_frames)} / 特徴 {len(all_des)}", flush=True)

    # --- 完成動画 ---
    final_frames = extract_frames(finals[0], work / "final", args.final_fps, 960)
    rows = []
    for j, (ft, fp) in enumerate(final_frames):
        kp, des = sift_features(sift, read_gray(fp, mask_logo=True), 800)
        best = (0, -1)
        if len(des) >= 8:
            nn = index.knnMatch(des, k=3)
            votes = np.zeros(len(raw_frames), np.float32)
            for cand in nn:
                for m in cand:
                    votes[owner[m.trainIdx]] += 1.0
            for ri in np.argsort(-votes)[: args.top]:
                inl = verify(kp, des, raw_frames[ri], matcher)
                if inl > best[0]:
                    best = (inl, int(ri))
        inl, ri = best
        hit = ri >= 0 and inl >= args.min_inliers
        rows.append({
            "final_t": round(ft, 2),
            "clip": raw_frames[ri].clip if hit else "",
            "raw_t": round(raw_frames[ri].t, 2) if hit else "",
            "inliers": inl,
            "method": "sift" if hit else "",
            "final_path": str(fp),
            "raw_path": str(raw_frames[ri].path) if hit else "",
        })
        if j % 40 == 0:
            print(f"完成 {j}/{len(final_frames)}", flush=True)

    filled = fill_gaps(rows, raw_frames, sift, matcher, args.fill_min_inliers, args.max_gap)
    print(f"前後のつながりで補ったコマ: {filled}", flush=True)

    with (out / "frames.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    segments = build_segments(rows, 1.0 / args.final_fps, 1.0 / args.raw_fps)
    with (out / "segments.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["clip", "raw_start", "raw_end", "final_start", "final_end", "speed", "frames",
                    "filled_frames", "median_inliers", "clip_duration", "note"])
        for s in segments:
            note = "タイトル背景" if s["final_start"] < args.title_end else ""
            w.writerow([s["clip"], s["raw_start"], s["raw_end"], s["final_start"], s["final_end"],
                        s["speed"], s["frames"], s["filled"], s["median_inliers"],
                        round(durations[s["clip"]], 2), note])

    make_review(rows, out)
    used = sum(s["raw_end"] - s["raw_start"] for s in segments)
    matched = sum(1 for r in rows if r["clip"])
    print(f"完了: 完成コマ {len(rows)} 中 {matched} 一致 / 区間 {len(segments)} / 元素材の採用合計 {used:.1f}秒"
          f"（元素材全体 {sum(durations.values()):.1f}秒）")
    return 0


def fill_gaps(rows, raw_frames: list[RawFrame], sift, matcher, min_inliers: int, max_gap: float) -> int:
    """一致なしのコマを、直前・直後の区間の続きとして探し直す。

    1. 前の区間の元素材なら「前の元時刻＋経過時間」、後ろなら「後の元時刻−残り時間」を予測し、
       その±1秒の元コマだけを低いしきい値で確かめる（method=fill）。
    2. それでも一致せず、前後が同じ元素材で時刻の進みが揃っていれば、間を直線で埋める（method=interp）。
    """
    by_clip: dict[str, list[RawFrame]] = {}
    for r in raw_frames:
        by_clip.setdefault(r.clip, []).append(r)

    def anchor(idx, step):
        k = idx + step
        while 0 <= k < len(rows) and not rows[k]["clip"]:
            k += step
        return rows[k] if 0 <= k < len(rows) else None

    filled = 0
    i = 0
    while i < len(rows):
        if rows[i]["clip"]:
            i += 1
            continue
        j = i
        while j < len(rows) and not rows[j]["clip"]:
            j += 1
        run = rows[i:j]
        prev, nxt = anchor(i, -1), anchor(j - 1, +1)
        if run[-1]["final_t"] - run[0]["final_t"] <= max_gap:
            for r in run:
                img = read_gray(Path(r["final_path"]), mask_logo=True)
                kp, des = sift_features(sift, img, 800)
                best = (0, None)
                for a, sign in ((prev, 1), (nxt, -1)):
                    if a is None or abs(r["final_t"] - a["final_t"]) > max_gap + 0.5:
                        continue
                    expect = a["raw_t"] + (r["final_t"] - a["final_t"])
                    for rf in by_clip[a["clip"]]:
                        if abs(rf.t - expect) <= 1.0:
                            n = verify(kp, des, rf, matcher)
                            if n > best[0]:
                                best = (n, rf)
                if best[1] is not None and best[0] >= min_inliers:
                    r.update(clip=best[1].clip, raw_t=round(best[1].t, 2), inliers=best[0],
                             method="fill", raw_path=str(best[1].path))
                    filled += 1
            # 確かめられなかったコマは、前後が同じ素材でつながる場合だけ直線で埋める。
            if prev and nxt and prev["clip"] == nxt["clip"]:
                span_f = nxt["final_t"] - prev["final_t"]
                span_r = nxt["raw_t"] - prev["raw_t"]
                if span_f > 0 and 0.2 <= span_r / span_f <= 1.5:
                    for r in run:
                        if not r["clip"]:
                            t = prev["raw_t"] + span_r * (r["final_t"] - prev["final_t"]) / span_f
                            r.update(clip=prev["clip"], raw_t=round(t, 2), method="interp")
                            filled += 1
        i = j
    return filled


def build_segments(rows, final_step: float, raw_step: float):
    """同じ元素材で時刻の進みが揃う連続コマを1区間にまとめる。

    1コマの外れ（照合ミス・ディゾルブ）は前後が同じ区間なら吸収する。
    速度は元時刻の傾き（スローなら1未満）。区間の端は元のコマ間隔の半分だけ広げる。
    """
    segs: list[dict] = []
    cur: list[dict] = []

    def flush():
        if len(cur) >= 2:
            ft = np.array([r["final_t"] for r in cur])
            rt = np.array([r["raw_t"] for r in cur], float)
            speed = float(np.polyfit(ft, rt, 1)[0]) if np.ptp(ft) > 0 else 1.0
            segs.append({
                "clip": cur[0]["clip"],
                "raw_start": round(max(0.0, rt.min() - raw_step / 2), 2),
                "raw_end": round(rt.max() + raw_step / 2, 2),
                "final_start": round(ft.min(), 2),
                "final_end": round(ft.max() + final_step, 2),
                "speed": round(speed, 2),
                "frames": len(cur),
                "median_inliers": int(np.median([r["inliers"] for r in cur])),
                "filled": sum(1 for r in cur if r["method"] != "sift"),
            })
        cur.clear()

    for r in rows:
        if not r["clip"]:
            continue
        if cur:
            last = cur[-1]
            gap_f = r["final_t"] - last["final_t"]
            d_raw = r["raw_t"] - last["raw_t"]
            same = (r["clip"] == last["clip"] and gap_f <= 3 * final_step + 1e-6
                    and -raw_step - 1e-6 <= d_raw <= gap_f * 1.5 + raw_step + 1e-6)
            if not same:
                flush()
        cur.append(r)
    flush()
    return segs


def make_review(rows, out: Path, per_sheet: int = 48) -> None:
    """完成コマ（左）と一致した元コマ（右）を並べた確認用一覧。2秒ごとに1組。"""
    picked = rows[::8]
    tiles = []
    for r in picked:
        f = cv2.imdecode(np.fromfile(r["final_path"], np.uint8), cv2.IMREAD_COLOR)
        f = cv2.resize(f, (320, 180))
        if r["raw_path"]:
            g = cv2.imdecode(np.fromfile(r["raw_path"], np.uint8), cv2.IMREAD_COLOR)
            g = letterbox(g, 320, 180)
            label = f'{r["clip"][4:15]} {r["raw_t"]}s n={r["inliers"]}'
        else:
            g = np.zeros((180, 320, 3), np.uint8)
            label = (f'{r["clip"][4:15]} {r["raw_t"]}s interp' if r["clip"] else f'no match n={r["inliers"]}')
        if r["method"] == "fill":
            label += " fill"
        pair = np.hstack([f, g])
        cv2.rectangle(pair, (0, 0), (640, 22), (0, 0, 0), -1)
        cv2.putText(pair, f'{r["final_t"]:.1f}s | {label}', (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        tiles.append(pair)
    cols = 3
    for s in range(0, len(tiles), per_sheet):
        chunk = tiles[s:s + per_sheet]
        while len(chunk) % cols:
            chunk.append(np.zeros_like(tiles[0]))
        grid = np.vstack([np.hstack(chunk[i:i + cols]) for i in range(0, len(chunk), cols)])
        cv2.imencode(".jpg", grid)[1].tofile(str(out / f"review_{s // per_sheet + 1}.jpg"))


def letterbox(img, w, h):
    ih, iw = img.shape[:2]
    s = min(w / iw, h / ih)
    r = cv2.resize(img, (int(iw * s), int(ih * s)))
    canvas = np.zeros((h, w, 3), np.uint8)
    y, x = (h - r.shape[0]) // 2, (w - r.shape[1]) // 2
    canvas[y:y + r.shape[0], x:x + r.shape[1]] = r
    return canvas


if __name__ == "__main__":
    sys.exit(main())
