from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

try:
    cv2.setLogLevel(0)
except AttributeError:
    pass

# kite_clipper.py のフレーム命名規則と同じもの。record_labels.py は完全に独立した
# スクリプトにするため、kite_clipper側の定義はimportせずここに複製しています。
FRAME_NAME_PATTERN = re.compile(r"^(?P<video_stem>.+)__t(?P<time>\d+(?:\.\d+)?)$")
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

# dataset/labels.csv の列（この順で固定）
LABEL_FIELDNAMES = [
    "run_id",
    "recorded_at",
    "video_stem",
    "frame_name",
    "time_sec",
    "label",
    "blur_score",
    "brightness",
    "width",
    "height",
    "megapixels",
    "is_blurry",
    "is_dark",
    "is_low_resolution",
    "has_person_candidate",
    "has_kite_candidate",
    "is_empty_scene",
    "heuristic_is_ng",
    "heuristic_ng_reason",
    "heuristic_score",
    "audio_peak",
    "image_path",
]

# result.csv の列名 -> labels.csv の列名（そのまま転記する特徴量）
RESULT_FEATURE_COLUMNS = {
    "blur_score": "blur_score",
    "brightness": "brightness",
    "width": "width",
    "height": "height",
    "megapixels": "megapixels",
    "is_blurry": "is_blurry",
    "is_dark": "is_dark",
    "is_low_resolution": "is_low_resolution",
    "has_person_candidate": "has_person_candidate",
    "has_kite_candidate": "has_kite_candidate",
    "is_empty_scene": "is_empty_scene",
    "is_ng": "heuristic_is_ng",
    "ng_reason": "heuristic_ng_reason",
    "score": "heuristic_score",
}


@dataclass
class FrameInfo:
    path: Path
    basename: str
    video_stem: str
    time_sec: float


@dataclass
class LabelEntry:
    video_stem: str
    frame_name: str
    time_sec: float
    label: int
    source_path: Path | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="review/frames と selected_frames の選別結果を教師データ(dataset/labels.csv)として記録します。"
    )
    parser.add_argument("--root", type=Path, default=None, help="プロジェクトルート（既定はこのスクリプトのあるフォルダ）")
    parser.add_argument("--dry-run", action="store_true", help="書き込み・コピーを一切せず、記録予定の件数と集計だけ表示します")
    parser.add_argument("--no-images", action="store_true", help="CSVのみ記録し、画像コピーをしません")
    parser.add_argument("--max-side", type=int, default=640, help="コピーする画像の長辺ピクセル（0以下なら縮小せず原寸コピー）")
    parser.add_argument(
        "--allow-empty-selection",
        action="store_true",
        help="採用フレーム(selected_frames)が0件でも記録を許可し、全フレームをNG(label=0)として記録します",
    )
    return parser.parse_args()


def iter_files_recursive(directory: Path, extensions: set[str]) -> Iterable[Path]:
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    )


def parse_frame_name(stem: str) -> tuple[str, float]:
    match = FRAME_NAME_PATTERN.match(stem)
    if not match:
        raise ValueError(f"フレーム名の形式が不正です: {stem}")
    return match.group("video_stem"), float(match.group("time"))


def collect_frames(directory: Path) -> tuple[dict[str, FrameInfo], list[str]]:
    """ディレクトリを再帰走査し、basename -> FrameInfo の辞書と警告メッセージ一覧を返します。"""
    frames: dict[str, FrameInfo] = {}
    warnings: list[str] = []
    for path in iter_files_recursive(directory, PHOTO_EXTENSIONS):
        try:
            video_stem, time_sec = parse_frame_name(path.stem)
        except ValueError:
            warnings.append(f"警告: フレーム名の形式が不正なためスキップします: {path}")
            continue
        frames[path.name] = FrameInfo(
            path=path,
            basename=path.name,
            video_stem=video_stem,
            time_sec=time_sec,
        )
    return frames, warnings


def load_result_features(csv_path: Path) -> dict[str, dict[str, str]]:
    """logs/result.csv の type=='frame' 行を output_path のファイル名(basename)をキーにして読み込みます。"""
    features: dict[str, dict[str, str]] = {}
    if not csv_path.exists():
        return features
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row.get("type") != "frame":
                continue
            basename = Path(row.get("output_path") or "").name
            if not basename:
                continue
            features[basename] = {
                target: row.get(source, "") or ""
                for source, target in RESULT_FEATURE_COLUMNS.items()
            }
    return features


def find_latest_audio_peaks_file(work_audio_dir: Path, video_stem: str) -> Path | None:
    if not work_audio_dir.exists():
        return None
    candidates = sorted(
        work_audio_dir.glob(f"{video_stem}_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_audio_peaks(path: Path) -> set[int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(value) for value in data}
    except Exception:
        return set()


class AudioPeakLookup:
    """動画ごとの音量ピーク秒集合を遅延読み込みしてキャッシュします。"""

    def __init__(self, work_audio_dir: Path) -> None:
        self.work_audio_dir = work_audio_dir
        self._cache: dict[str, set[int] | None] = {}

    def get(self, video_stem: str, time_sec: float) -> str:
        if video_stem not in self._cache:
            peaks_file = find_latest_audio_peaks_file(self.work_audio_dir, video_stem)
            self._cache[video_stem] = load_audio_peaks(peaks_file) if peaks_file else None
        peaks = self._cache[video_stem]
        if peaks is None:
            return ""
        second = int(time_sec)
        is_peak = second in peaks or (second - 1) in peaks or (second + 1) in peaks
        return "1" if is_peak else "0"


def read_image(path: Path) -> np.ndarray | None:
    # 日本語などのパスでも読めるように np.fromfile 経由でデコードします（kite_clipper.py と同じ方式）。
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def copy_frame_image(source_path: Path, dest_path: Path, max_side: int) -> str:
    """フレーム画像を dataset/frames 配下へコピーします。戻り値は 'copied' か 'skipped'。"""
    if dest_path.exists():
        return "skipped"
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if max_side <= 0:
        shutil.copy2(source_path, dest_path)
        return "copied"

    image = read_image(source_path)
    if image is None:
        raise ValueError(f"画像として読み込めません: {source_path}")

    height, width = image.shape[:2]
    long_side = max(height, width)
    if long_side <= max_side:
        shutil.copy2(source_path, dest_path)
        return "copied"

    scale = max_side / long_side
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)

    ext = dest_path.suffix.lower() or ".jpg"
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, 85] if ext in (".jpg", ".jpeg") else []
    success, encoded = cv2.imencode(ext, resized, encode_params)
    if not success:
        raise ValueError(f"画像を書き出せません: {dest_path}")
    encoded.tofile(str(dest_path))
    return "copied"


def append_labels_csv(csv_path: Path, rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()
    mode = "a" if file_exists else "w"
    with csv_path.open(mode, encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=LABEL_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def build_label_entries(
    review_frames: dict[str, FrameInfo],
    selected_frames: dict[str, FrameInfo],
) -> list[LabelEntry]:
    entries: list[LabelEntry] = []
    all_basenames = sorted(set(review_frames) | set(selected_frames))
    for basename in all_basenames:
        review_info = review_frames.get(basename)
        selected_info = selected_frames.get(basename)
        info = review_info or selected_info
        assert info is not None
        label = 1 if selected_info is not None else 0
        # コピー元は review/frames を優先し、review側に無ければ selected_frames側を使う
        source_path = review_info.path if review_info is not None else selected_info.path
        entries.append(
            LabelEntry(
                video_stem=info.video_stem,
                frame_name=basename,
                time_sec=info.time_sec,
                label=label,
                source_path=source_path,
            )
        )
    entries.sort(key=lambda entry: (entry.video_stem, entry.time_sec, entry.frame_name))
    return entries


def summarize_by_video(entries: list[LabelEntry]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for entry in entries:
        stats = summary.setdefault(entry.video_stem, {"selected": 0, "not_selected": 0})
        if entry.label == 1:
            stats["selected"] += 1
        else:
            stats["not_selected"] += 1
    return summary


def print_summary(entries: list[LabelEntry], dry_run: bool) -> None:
    summary = summarize_by_video(entries)
    label = "記録予定" if dry_run else "記録"
    print(f"--- 動画ごとの集計（{label}） ---")
    for video_stem in sorted(summary):
        stats = summary[video_stem]
        total = stats["selected"] + stats["not_selected"]
        print(f"{video_stem}: 採用 {stats['selected']} / 非採用 {stats['not_selected']} / 合計 {total}")
    total_selected = sum(stats["selected"] for stats in summary.values())
    total_not_selected = sum(stats["not_selected"] for stats in summary.values())
    print(f"全体: 採用 {total_selected} / 非採用 {total_not_selected} / 合計 {total_selected + total_not_selected}")


def main() -> int:
    args = parse_args()
    root = args.root.resolve() if args.root else Path(__file__).resolve().parent

    review_frames_dir = root / "review" / "frames"
    selected_frames_dir = root / "selected_frames"
    result_csv_path = root / "logs" / "result.csv"
    work_audio_dir = root / "work" / "audio_peaks"
    dataset_dir = root / "dataset"
    labels_csv_path = dataset_dir / "labels.csv"
    dataset_frames_dir = dataset_dir / "frames"

    if not review_frames_dir.exists():
        print(f"エラー: review/frames が見つかりません: {review_frames_dir}")
        return 1

    review_frames, review_warnings = collect_frames(review_frames_dir)
    for warning in review_warnings:
        print(warning)

    if not review_frames:
        print(f"エラー: review/frames にフレームが0件です: {review_frames_dir}")
        return 1

    selected_frames, selected_warnings = collect_frames(selected_frames_dir)
    for warning in selected_warnings:
        print(warning)

    if not selected_frames and not args.allow_empty_selection:
        print("採用フレームが0件です。全フレームをNGとして記録する場合は --allow-empty-selection を付けてください")
        return 1

    entries = build_label_entries(review_frames, selected_frames)

    features_by_basename = load_result_features(result_csv_path)
    audio_lookup = AudioPeakLookup(work_audio_dir)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    recorded_at = datetime.now().isoformat()

    rows: list[dict[str, object]] = []
    copied_count = 0
    skipped_count = 0

    for entry in entries:
        features = features_by_basename.get(entry.frame_name, {})
        audio_peak = audio_lookup.get(entry.video_stem, entry.time_sec)

        image_path = ""
        if not args.no_images:
            image_path = (Path("frames") / entry.video_stem / entry.frame_name).as_posix()

        row: dict[str, object] = {
            "run_id": run_id,
            "recorded_at": recorded_at,
            "video_stem": entry.video_stem,
            "frame_name": entry.frame_name,
            "time_sec": entry.time_sec,
            "label": entry.label,
            "audio_peak": audio_peak,
            "image_path": image_path,
        }
        for column in RESULT_FEATURE_COLUMNS.values():
            row[column] = features.get(column, "")
        rows.append(row)

        if args.dry_run or args.no_images:
            continue
        if entry.source_path is None:
            skipped_count += 1
            continue
        dest_path = dataset_frames_dir / entry.video_stem / entry.frame_name
        try:
            result = copy_frame_image(entry.source_path, dest_path, args.max_side)
        except Exception as exc:
            print(f"警告: 画像コピーに失敗しました: {entry.source_path} ({exc})")
            skipped_count += 1
            continue
        if result == "copied":
            copied_count += 1
        else:
            skipped_count += 1

    print_summary(entries, args.dry_run)

    if args.dry_run:
        print(f"記録予定件数: {len(rows)}")
        print("dry-run のため、CSV書き込み・画像コピーは行っていません。")
        return 0

    append_labels_csv(labels_csv_path, rows)
    print(f"CSV: {labels_csv_path}")
    if args.no_images:
        print("画像コピー: --no-images のため実施していません")
    else:
        print(f"コピーした画像: {copied_count} 枚")
        print(f"スキップした画像: {skipped_count} 枚")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
