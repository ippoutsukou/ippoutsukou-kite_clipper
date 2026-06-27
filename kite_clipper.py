from __future__ import annotations

import argparse
import csv
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}


@dataclass
class PhotoAnalysis:
    path: Path
    width: int
    height: int
    blur_score: float
    brightness: float
    megapixels: float
    is_blurry: bool
    is_dark: bool
    is_low_resolution: bool
    hash_bits: np.ndarray
    group_id: int | None = None
    selected: bool = False
    copied_to: Path | None = None

    @property
    def score(self) -> float:
        sharpness_score = min(self.blur_score / 500.0, 1.0) * 45.0
        brightness_score = brightness_quality_score(self.brightness) * 30.0
        resolution_score = min(self.megapixels / 8.0, 1.0) * 25.0
        penalty = 0.0
        if self.is_blurry:
            penalty += 20.0
        if self.is_dark:
            penalty += 15.0
        if self.is_low_resolution:
            penalty += 10.0
        return max(0.0, sharpness_score + brightness_score + resolution_score - penalty)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="カイトボードの写真・動画をOpenCVベースで整理します。"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="作業ルートフォルダ")
    parser.add_argument("--blur-threshold", type=float, default=120.0, help="これ未満をブレ候補にします")
    parser.add_argument("--dark-threshold", type=float, default=70.0, help="平均輝度がこれ未満なら暗い候補にします")
    parser.add_argument("--min-width", type=int, default=1280, help="最低幅")
    parser.add_argument("--min-height", type=int, default=720, help="最低高さ")
    parser.add_argument("--similarity-threshold", type=int, default=10, help="pHashのハミング距離しきい値")
    parser.add_argument("--frame-interval", type=int, default=1, help="動画フレーム抽出間隔（秒）")
    return parser.parse_args()


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "error.log",
        level=logging.ERROR,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )


def iter_files(directory: Path, extensions: set[str]) -> Iterable[Path]:
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    )


def read_image(path: Path) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def analyze_photo(
    path: Path,
    blur_threshold: float,
    dark_threshold: float,
    min_width: int,
    min_height: int,
) -> PhotoAnalysis | None:
    image = read_image(path)
    if image is None:
        raise ValueError(f"画像として読み込めません: {path}")

    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    megapixels = (width * height) / 1_000_000.0

    return PhotoAnalysis(
        path=path,
        width=width,
        height=height,
        blur_score=blur_score,
        brightness=brightness,
        megapixels=megapixels,
        is_blurry=blur_score < blur_threshold,
        is_dark=brightness < dark_threshold,
        is_low_resolution=width < min_width or height < min_height,
        hash_bits=average_hash(gray),
    )


def average_hash(gray: np.ndarray, size: int = 16) -> np.ndarray:
    resized = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    return resized > resized.mean()


def hamming_distance(left: np.ndarray, right: np.ndarray) -> int:
    return int(np.count_nonzero(left.flatten() != right.flatten()))


def brightness_quality_score(brightness: float) -> float:
    # 120前後を最も扱いやすい明るさとして、暗すぎ・明るすぎを下げます。
    distance = abs(brightness - 120.0)
    return max(0.0, 1.0 - distance / 120.0)


def group_photos(photos: list[PhotoAnalysis], threshold: int) -> None:
    representatives: list[PhotoAnalysis] = []
    for photo in photos:
        matched_group = None
        for representative in representatives:
            if hamming_distance(photo.hash_bits, representative.hash_bits) <= threshold:
                matched_group = representative.group_id
                break
        if matched_group is None:
            matched_group = len(representatives) + 1
            photo.group_id = matched_group
            representatives.append(photo)
        else:
            photo.group_id = matched_group


def select_best_photos(photos: list[PhotoAnalysis]) -> list[PhotoAnalysis]:
    selected: list[PhotoAnalysis] = []
    group_ids = sorted({photo.group_id for photo in photos if photo.group_id is not None})
    for group_id in group_ids:
        group = [photo for photo in photos if photo.group_id == group_id]
        best = max(group, key=lambda item: (item.score, item.blur_score, item.megapixels))
        best.selected = True
        selected.append(best)
    return selected


def unique_output_path(directory: Path, source_name: str) -> Path:
    candidate = directory / source_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def copy_selected_photos(selected: list[PhotoAnalysis], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for photo in selected:
        destination = unique_output_path(output_dir, photo.path.name)
        shutil.copy2(photo.path, destination)
        photo.copied_to = destination


def extract_video_frames(video_path: Path, output_dir: Path, interval: int) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    video_stem = safe_stem(video_path)
    pattern = output_dir / f"{video_stem}_%06d.jpg"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval}",
        "-q:v",
        "2",
        str(pattern),
    ]
    subprocess.run(command, check=True)
    return sorted(output_dir.glob(f"{video_stem}_*.jpg"))


def safe_stem(path: Path) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in path.stem)


def write_result_csv(csv_path: Path, rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "type",
        "source_path",
        "output_path",
        "group_id",
        "selected",
        "width",
        "height",
        "blur_score",
        "brightness",
        "megapixels",
        "is_blurry",
        "is_dark",
        "is_low_resolution",
        "score",
        "status",
        "message",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def photo_row(photo: PhotoAnalysis) -> dict[str, object]:
    return {
        "type": "photo",
        "source_path": str(photo.path),
        "output_path": str(photo.copied_to) if photo.copied_to else "",
        "group_id": photo.group_id or "",
        "selected": photo.selected,
        "width": photo.width,
        "height": photo.height,
        "blur_score": round(photo.blur_score, 3),
        "brightness": round(photo.brightness, 3),
        "megapixels": round(photo.megapixels, 3),
        "is_blurry": photo.is_blurry,
        "is_dark": photo.is_dark,
        "is_low_resolution": photo.is_low_resolution,
        "score": round(photo.score, 3),
        "status": "ok",
        "message": "",
    }


def error_row(item_type: str, source_path: Path, message: str) -> dict[str, object]:
    return {
        "type": item_type,
        "source_path": str(source_path),
        "output_path": "",
        "group_id": "",
        "selected": "",
        "width": "",
        "height": "",
        "blur_score": "",
        "brightness": "",
        "megapixels": "",
        "is_blurry": "",
        "is_dark": "",
        "is_low_resolution": "",
        "score": "",
        "status": "error",
        "message": message,
    }


def frame_row(video_path: Path, frame_path: Path) -> dict[str, object]:
    return {
        "type": "frame",
        "source_path": str(video_path),
        "output_path": str(frame_path),
        "group_id": "",
        "selected": "",
        "width": "",
        "height": "",
        "blur_score": "",
        "brightness": "",
        "megapixels": "",
        "is_blurry": "",
        "is_dark": "",
        "is_low_resolution": "",
        "score": "",
        "status": "ok",
        "message": "",
    }


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    input_photos = root / "input" / "photos"
    input_videos = root / "input" / "videos"
    review_photos = root / "review" / "photos"
    review_frames = root / "review" / "frames"
    logs = root / "logs"

    setup_logging(logs)
    rows: list[dict[str, object]] = []
    photos: list[PhotoAnalysis] = []

    for photo_path in iter_files(input_photos, PHOTO_EXTENSIONS):
        try:
            analysis = analyze_photo(
                photo_path,
                args.blur_threshold,
                args.dark_threshold,
                args.min_width,
                args.min_height,
            )
            if analysis:
                photos.append(analysis)
        except Exception as exc:
            logging.exception("写真処理エラー: %s", photo_path)
            rows.append(error_row("photo", photo_path, str(exc)))

    if photos:
        group_photos(photos, args.similarity_threshold)
        selected = select_best_photos(photos)
        try:
            copy_selected_photos(selected, review_photos)
        except Exception as exc:
            logging.exception("写真コピーエラー")
            rows.append(error_row("photo_copy", review_photos, str(exc)))
        rows.extend(photo_row(photo) for photo in photos)

    for video_path in iter_files(input_videos, VIDEO_EXTENSIONS):
        try:
            frames = extract_video_frames(video_path, review_frames, args.frame_interval)
            rows.extend(frame_row(video_path, frame_path) for frame_path in frames)
        except Exception as exc:
            logging.exception("動画フレーム抽出エラー: %s", video_path)
            rows.append(error_row("video", video_path, str(exc)))

    write_result_csv(logs / "result.csv", rows)
    print(f"完了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"CSV: {logs / 'result.csv'}")
    print(f"エラーログ: {logs / 'error.log'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
