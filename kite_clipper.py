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


@dataclass
class FrameAnalysis:
    path: Path
    width: int
    height: int
    blur_score: float
    brightness: float
    megapixels: float
    is_blurry: bool
    is_dark: bool
    is_low_resolution: bool
    has_person_candidate: bool
    has_kite_candidate: bool
    is_empty_scene: bool
    is_ng: bool
    ng_reason: str

    @property
    def score(self) -> float:
        sharpness_score = min(self.blur_score / 500.0, 1.0) * 40.0
        brightness_score = brightness_quality_score(self.brightness) * 25.0
        resolution_score = min(self.megapixels / 8.0, 1.0) * 15.0
        subject_score = 20.0 if (self.has_person_candidate or self.has_kite_candidate) else 0.0
        penalty = 0.0
        if self.is_blurry:
            penalty += 15.0
        if self.is_dark:
            penalty += 10.0
        if self.is_low_resolution:
            penalty += 8.0
        if self.is_empty_scene:
            penalty += 20.0
        return max(0.0, sharpness_score + brightness_score + resolution_score + subject_score - penalty)


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
    parser.add_argument("--cleanup-review", action="store_true", help="review配下の確認済み候補を整理します")
    parser.add_argument("--dry-run", action="store_true", help="削除せず、削除予定だけを表示します")
    parser.add_argument("--confirm-delete", action="store_true", help="review/photos と review/frames のファイル削除を実行します")
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


def analyze_frame(
    path: Path,
    blur_threshold: float,
    dark_threshold: float,
    min_width: int,
    min_height: int,
) -> FrameAnalysis:
    image = read_image(path)
    if image is None:
        raise ValueError(f"画像として読み込めません: {path}")

    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    megapixels = (width * height) / 1_000_000.0
    has_person = detect_person_candidate(image)
    has_kite = detect_kite_candidate(image)
    is_empty = detect_empty_scene(image)
    ng_reasons: list[str] = []
    if not has_person and not has_kite:
        ng_reasons.append("no_person_or_kite_candidate")
    if is_empty:
        ng_reasons.append("empty_scene_candidate")

    return FrameAnalysis(
        path=path,
        width=width,
        height=height,
        blur_score=blur_score,
        brightness=brightness,
        megapixels=megapixels,
        is_blurry=blur_score < blur_threshold,
        is_dark=brightness < dark_threshold,
        is_low_resolution=width < min_width or height < min_height,
        has_person_candidate=has_person,
        has_kite_candidate=has_kite,
        is_empty_scene=is_empty,
        is_ng=bool(ng_reasons),
        ng_reason=";".join(ng_reasons),
    )


def resized_for_detection(image: np.ndarray, max_width: int = 960) -> np.ndarray:
    height, width = image.shape[:2]
    if width <= max_width:
        return image
    scale = max_width / width
    return cv2.resize(image, (max_width, int(height * scale)), interpolation=cv2.INTER_AREA)


def detect_person_candidate(image: np.ndarray) -> bool:
    detection_image = resized_for_detection(image, 640)
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    rects, weights = hog.detectMultiScale(
        detection_image,
        winStride=(8, 8),
        padding=(8, 8),
        scale=1.05,
    )
    return any(float(weight) > 0.25 for weight in weights) if len(rects) else False


def detect_kite_candidate(image: np.ndarray) -> bool:
    detection_image = resized_for_detection(image, 960)
    height, width = detection_image.shape[:2]
    upper = detection_image[: max(1, int(height * 0.65)), :]
    hsv = cv2.cvtColor(upper, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    color_mask = cv2.inRange(saturation, 70, 255)
    bright_mask = cv2.inRange(value, 80, 255)
    mask = cv2.bitwise_and(color_mask, bright_mask)
    mask = cv2.medianBlur(mask, 5)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = upper.shape[0] * upper.shape[1]

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < image_area * 0.00025 or area > image_area * 0.08:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w < 12 or h < 8:
            continue
        aspect_ratio = w / max(h, 1)
        if 0.5 <= aspect_ratio <= 8.0:
            return True
    return False


def detect_empty_scene(image: np.ndarray) -> bool:
    detection_image = resized_for_detection(image, 640)
    gray = cv2.cvtColor(detection_image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(detection_image, cv2.COLOR_BGR2HSV)
    edges = cv2.Canny(gray, 80, 160)
    edge_density = float(np.count_nonzero(edges)) / edges.size
    saturation_mean = float(np.mean(hsv[:, :, 1]))
    return edge_density < 0.012 and saturation_mean < 55.0


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
        "has_person_candidate",
        "has_kite_candidate",
        "is_empty_scene",
        "is_ng",
        "ng_reason",
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
        "has_person_candidate": "",
        "has_kite_candidate": "",
        "is_empty_scene": "",
        "is_ng": "",
        "ng_reason": "",
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
        "has_person_candidate": "",
        "has_kite_candidate": "",
        "is_empty_scene": "",
        "is_ng": "",
        "ng_reason": "",
        "score": "",
        "status": "error",
        "message": message,
    }


def frame_row(video_path: Path, frame: FrameAnalysis) -> dict[str, object]:
    return {
        "type": "frame",
        "source_path": str(video_path),
        "output_path": str(frame.path),
        "group_id": "",
        "selected": "",
        "width": frame.width,
        "height": frame.height,
        "blur_score": round(frame.blur_score, 3),
        "brightness": round(frame.brightness, 3),
        "megapixels": round(frame.megapixels, 3),
        "is_blurry": frame.is_blurry,
        "is_dark": frame.is_dark,
        "is_low_resolution": frame.is_low_resolution,
        "has_person_candidate": frame.has_person_candidate,
        "has_kite_candidate": frame.has_kite_candidate,
        "is_empty_scene": frame.is_empty_scene,
        "is_ng": frame.is_ng,
        "ng_reason": frame.ng_reason,
        "score": round(frame.score, 3),
        "status": "ok",
        "message": "",
    }


def cleanup_review(root: Path, confirm_delete: bool, dry_run: bool) -> int:
    logs = root / "logs"
    setup_logging(logs)
    cleanup_targets = [root / "review" / "photos", root / "review" / "frames"]
    actual_delete = confirm_delete and not dry_run
    rows: list[dict[str, object]] = []

    for target_dir in cleanup_targets:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            for target in sorted(path for path in target_dir.rglob("*") if path.is_file()):
                if target.name == ".gitkeep":
                    rows.append(cleanup_row(target, target_dir, "skipped", "ok", "keep marker"))
                    continue
                if not is_relative_to(target.resolve(), target_dir.resolve()):
                    rows.append(cleanup_row(target, target_dir, "skipped", "error", "outside cleanup directory"))
                    continue
                try:
                    if actual_delete:
                        target.unlink()
                        rows.append(cleanup_row(target, target_dir, "deleted", "ok", ""))
                    else:
                        rows.append(cleanup_row(target, target_dir, "would_delete", "ok", "dry run"))
                except Exception as exc:
                    logging.exception("review整理エラー: %s", target)
                    rows.append(cleanup_row(target, target_dir, "error", "error", str(exc)))
        except Exception as exc:
            logging.exception("review整理ディレクトリエラー: %s", target_dir)
            rows.append(cleanup_row(target_dir, target_dir, "error", "error", str(exc)))

    write_cleanup_csv(logs / "cleanup.csv", rows)
    action_label = "削除" if actual_delete else "削除予定"
    count = sum(1 for row in rows if row["action"] in {"deleted", "would_delete"})
    print(f"{action_label}: {count} 件")
    print(f"CSV: {logs / 'cleanup.csv'}")
    print(f"エラーログ: {logs / 'error.log'}")
    if not actual_delete:
        print("実際に削除するには --cleanup-review --confirm-delete を指定してください。")
    return 0


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def cleanup_row(
    path: Path,
    target_dir: Path,
    action: str,
    status: str,
    message: str,
) -> dict[str, object]:
    return {
        "path": str(path),
        "cleanup_dir": str(target_dir),
        "action": action,
        "status": status,
        "message": message,
    }


def write_cleanup_csv(csv_path: Path, rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["path", "cleanup_dir", "action", "status", "message"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if args.cleanup_review:
        return cleanup_review(root, args.confirm_delete, args.dry_run)

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
            for frame_path in frames:
                try:
                    frame = analyze_frame(
                        frame_path,
                        args.blur_threshold,
                        args.dark_threshold,
                        args.min_width,
                        args.min_height,
                    )
                    rows.append(frame_row(video_path, frame))
                except Exception as exc:
                    logging.exception("フレーム判定エラー: %s", frame_path)
                    rows.append(error_row("frame", frame_path, str(exc)))
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
