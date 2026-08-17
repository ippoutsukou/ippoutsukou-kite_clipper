from __future__ import annotations

import argparse
import csv
import logging
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

import sport_profile

try:
    cv2.setLogLevel(0)
except AttributeError:
    pass


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}
FRAME_NAME_PATTERN = re.compile(r"^(?P<video_stem>.+)__t(?P<time>\d+(?:\.\d+)?)$")
LEGACY_FRAME_NAME_PATTERN = re.compile(r"^(?P<video_stem>.+)_(?P<number>\d{6})$")

# マルチスポーツ対応前からのハードコード既定値。プロファイル（profiles/<sport>.json）に
# 該当キーが無かった場合の最終フォールバックとしてのみ使う（優先順位: CLI明示 > プロファイル > これ）。
DEFAULT_BLUR_THRESHOLD = 120.0
DEFAULT_DARK_THRESHOLD = 70.0
DEFAULT_MIN_WIDTH = 1280
DEFAULT_MIN_HEIGHT = 720
DEFAULT_SIMILARITY_THRESHOLD = 10
DEFAULT_FRAME_INTERVAL = 1.0
DEFAULT_SPLIT_GAP = 2.0
DEFAULT_PRE_ROLL = 1.0
DEFAULT_POST_ROLL = 1.0


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_tool_path(tool_name: str) -> str:
    bundled = app_base_dir() / "vendor" / "ffmpeg" / "windows-x64" / f"{tool_name}.exe"
    if bundled.exists():
        return str(bundled)
    return tool_name


def ffmpeg_path(path: Path) -> str:
    return path.resolve().as_posix()


_VIDEO_ENCODER: str | None = None


def detect_video_encoder() -> str:
    # 対応していればハードウェアエンコーダ（Quick Sync / Media Foundation）を使い、
    # 使えない環境ではソフトウェアエンコーダにフォールバックします。速度差が大きいため、
    # プロセス内で一度だけ判定してキャッシュします。
    global _VIDEO_ENCODER
    if _VIDEO_ENCODER is not None:
        return _VIDEO_ENCODER
    for candidate in ("h264_qsv", "h264_mf", "libopenh264"):
        if candidate == "libopenh264" or _can_encode(candidate):
            _VIDEO_ENCODER = candidate
            break
    return _VIDEO_ENCODER


def _can_encode(encoder_name: str) -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        probe_output = Path(temp_dir) / "probe.mp4"
        command = [
            bundled_tool_path("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=size=320x240:rate=10:duration=0.3",
            "-pix_fmt",
            "yuv420p",
            "-frames:v",
            "3",
            "-c:v",
            encoder_name,
            ffmpeg_path(probe_output),
        ]
        try:
            result = run_ffmpeg(command)
        except Exception:
            return False
        return result.returncode == 0 and probe_output.exists() and probe_output.stat().st_size > 0


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


@dataclass
class SelectedFrame:
    path: Path
    video_stem: str
    time_sec: float


@dataclass
class CutJob:
    video_stem: str
    video_path: Path
    output_path: Path
    start_sec: float
    end_sec: float
    first_frame: Path
    last_frame: Path
    frame_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="カイトボードの写真・動画をOpenCVベースで整理します。"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="作業ルートフォルダ")
    parser.add_argument(
        "--sport",
        type=str,
        default=None,
        help=f"使用するスポーツプロファイルID（profiles/<id>.json。既定: {sport_profile.DEFAULT_SPORT_ID}）",
    )
    parser.add_argument(
        "--allow-profile-switch",
        action="store_true",
        help="前回と異なる競技を指定した際の混在ガードをバイパスします（中間生成物の混在を承知のうえで続行）",
    )
    parser.add_argument(
        "--blur-threshold", type=float, default=None, help="これ未満をブレ候補にします（既定: プロファイル由来）"
    )
    parser.add_argument(
        "--dark-threshold",
        type=float,
        default=None,
        help="平均輝度がこれ未満なら暗い候補にします（既定: プロファイル由来）",
    )
    parser.add_argument("--min-width", type=int, default=None, help="最低幅（既定: プロファイル由来）")
    parser.add_argument("--min-height", type=int, default=None, help="最低高さ（既定: プロファイル由来）")
    parser.add_argument(
        "--similarity-threshold",
        type=int,
        default=None,
        help="pHashのハミング距離しきい値（既定: プロファイル由来）",
    )
    parser.add_argument(
        "--frame-interval", type=float, default=None, help="動画フレーム抽出間隔（秒）（既定: プロファイル由来）"
    )
    parser.add_argument("--cleanup-review", action="store_true", help="review配下の確認済み候補を整理します")
    parser.add_argument("--dry-run", action="store_true", help="削除せず、削除予定だけを表示します")
    parser.add_argument("--confirm-delete", action="store_true", help="review/photos と review/frames のファイル削除を実行します")
    parser.add_argument("--cut-from-selected-frames", action="store_true", help="selected_frames の連続フレーム範囲から動画を切り出します")
    parser.add_argument(
        "--cut-from-ai-segments",
        action="store_true",
        help="logs/ai_segments.csv のAI提案区間から直接動画を切り出します（おまかせモード用）",
    )
    parser.add_argument(
        "--split-gap",
        type=float,
        default=None,
        help="この秒数より離れた selected_frames を別クリップにします（既定: プロファイル由来）",
    )
    parser.add_argument(
        "--pre-roll", type=float, default=None, help="切り出し開始を選択範囲の何秒前にするか（既定: プロファイル由来）"
    )
    parser.add_argument(
        "--post-roll", type=float, default=None, help="切り出し終了を選択範囲の何秒後にするか（既定: プロファイル由来）"
    )
    parser.add_argument("--fast-copy", action="store_true", help="再エンコードせず高速に切り出します。カット位置は少しずれる場合があります")
    parser.add_argument(
        "--only-video",
        type=str,
        default=None,
        help="ファイル名またはstemを指定して、その動画だけを処理対象にします（フレーム抽出・--cut-from-ai-segmentsで有効）",
    )
    parser.add_argument("--cleanup-after-cut", action="store_true", help="動画切り出し後に不要になった選択フレームを整理します")
    parser.add_argument("--include-review-frames", action="store_true", help="cleanup-after-cutでreview/framesも整理対象にします")
    parser.add_argument("--include-review-photos", action="store_true", help="cleanup-after-cutでreview/photosも整理対象にします")
    parser.add_argument("--include-work", action="store_true", help="cleanup-after-cutでwork配下の一時ファイルも整理対象にします")
    parser.add_argument("--include-selected-clips", action="store_true", help="cleanup-after-cutでselected_clipsも整理対象にします")
    parser.add_argument("--concat-selected-clips", action="store_true", help="selected_clips の動画を1本に結合します")
    parser.add_argument("--concat-order-file", type=Path, default=None, help="結合順を1行1ファイルで指定するテキスト")
    parser.add_argument("--concat-output", type=Path, default=None, help="結合後の出力ファイル")
    parser.add_argument(
        "--normalize",
        choices=["original", "vertical", "horizontal", "square"],
        default="vertical",
        help="結合前に全クリップを同じサイズへ正規化します",
    )
    parser.add_argument(
        "--fit",
        choices=["contain", "cover"],
        default="contain",
        help="正規化時の表示方法。containは全体表示、coverは画面いっぱいです",
    )
    return parser.parse_args()


def resolve_sport_profile(args: argparse.Namespace) -> sport_profile.SportProfile:
    """`--sport` からプロファイルを読み込み、cv/segment系の未指定引数にプロファイル値を埋める。

    優先順位は 明示指定(CLI) > プロファイル値 > 従来のハードコード既定値。
    `args` はこの関数の中で書き換える（呼び出し側は以降 args.blur_threshold 等をそのまま使える）。
    """
    sport_id = args.sport if args.sport is not None else sport_profile.DEFAULT_SPORT_ID
    profile = sport_profile.load_profile(app_base_dir(), sport_id)

    cv = profile.cv
    segment = profile.segment

    if args.blur_threshold is None:
        args.blur_threshold = cv.get("blur_threshold", DEFAULT_BLUR_THRESHOLD)
    if args.dark_threshold is None:
        args.dark_threshold = cv.get("dark_threshold", DEFAULT_DARK_THRESHOLD)
    if args.min_width is None:
        args.min_width = cv.get("min_width", DEFAULT_MIN_WIDTH)
    if args.min_height is None:
        args.min_height = cv.get("min_height", DEFAULT_MIN_HEIGHT)
    if args.similarity_threshold is None:
        args.similarity_threshold = cv.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD)
    if args.frame_interval is None:
        args.frame_interval = cv.get("frame_interval", DEFAULT_FRAME_INTERVAL)
    if args.split_gap is None:
        args.split_gap = segment.get("split_gap", DEFAULT_SPLIT_GAP)
    if args.pre_roll is None:
        args.pre_roll = segment.get("pre_roll", DEFAULT_PRE_ROLL)
    if args.post_roll is None:
        args.post_roll = segment.get("post_roll", DEFAULT_POST_ROLL)

    return profile


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "error.log"
    log_file.touch(exist_ok=True)
    logging.basicConfig(
        filename=str(log_file),
        level=logging.ERROR,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )


def ensure_runtime_dirs(root: Path) -> None:
    for relative in [
        "input/photos",
        "input/videos",
        "review/photos",
        "review/frames",
        "selected_frames",
        "selected_clips",
        "final",
        "logs",
        "work",
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)


def write_dict_csv(csv_path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file = csv_path.open("w", newline="", encoding="utf-8-sig")
    try:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        try:
            file.close()
        except OSError as exc:
            if exc.errno != 9:
                raise


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
) -> PhotoAnalysis:
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
    profile: sport_profile.SportProfile,
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
    # has_kite_candidate: 競技固有の器材候補フラグ。互換性のため列名・フィールド名は kite のまま。
    # 検出アルゴリズム本体（detect_kite_candidate）は変更せず、equipment_detector によって
    # 呼ぶかどうかだけを sport_profile.detect_equipment_candidate() が振り分ける。
    has_kite = sport_profile.detect_equipment_candidate(image, profile, kite_detector=detect_kite_candidate)
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


def analyze_frame_task(
    frame_path: Path,
    blur_threshold: float,
    dark_threshold: float,
    min_width: int,
    min_height: int,
    profile: sport_profile.SportProfile,
) -> tuple[Path, FrameAnalysis | None, str]:
    # ProcessPoolExecutorの子プロセスで実行するため、例外は文字列にして持ち帰ります。
    try:
        analysis = analyze_frame(frame_path, blur_threshold, dark_threshold, min_width, min_height, profile)
        return frame_path, analysis, ""
    except Exception as exc:
        return frame_path, None, str(exc)


def resized_for_detection(image: np.ndarray, max_width: int = 960) -> np.ndarray:
    height, width = image.shape[:2]
    if width <= max_width:
        return image
    scale = max_width / width
    return cv2.resize(image, (max_width, int(height * scale)), interpolation=cv2.INTER_AREA)


_HOG_DETECTOR: cv2.HOGDescriptor | None = None


def get_hog_detector() -> cv2.HOGDescriptor:
    # HOGDescriptorの生成は重いので、最初の1回だけ作って使い回します。
    global _HOG_DETECTOR
    if _HOG_DETECTOR is None:
        _HOG_DETECTOR = cv2.HOGDescriptor()
        _HOG_DETECTOR.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return _HOG_DETECTOR


def detect_person_candidate(image: np.ndarray) -> bool:
    detection_image = resized_for_detection(image, 640)
    hog = get_hog_detector()
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


def extract_video_frames(video_path: Path, output_dir: Path, interval: float) -> list[Path]:
    if interval <= 0:
        raise ValueError("--frame-interval は 0 より大きい値にしてください")

    output_dir.mkdir(parents=True, exist_ok=True)

    # ffmpegでの抽出はOpenCVのシーク方式より大幅に速いため、まずffmpegを試します。
    # 1秒以上の間隔ならキーフレームのみのデコード（最速）で近い間隔の抽出ができます。
    if interval >= 1.0:
        try:
            return extract_frames_keyframe(video_path, output_dir, interval)
        except Exception:
            logging.exception("キーフレーム抽出に失敗したためfps方式で再試行します: %s", video_path)
    try:
        return extract_frames_ffmpeg_fps(video_path, output_dir, interval)
    except Exception:
        logging.exception("ffmpeg抽出に失敗したためOpenCV方式で再試行します: %s", video_path)
        return extract_frames_opencv(video_path, output_dir, interval)


SHOWINFO_PTS_PATTERN = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def thin_by_interval(times: list[float], interval: float) -> list[int]:
    # interval未満の間隔で並ぶキーフレームを間引き、採用するインデックスを返します。
    selected: list[int] = []
    last_kept = None
    for index, time_sec in enumerate(times):
        if last_kept is None or time_sec - last_kept >= interval - 0.5:
            selected.append(index)
            last_kept = time_sec
    return selected


def extract_frames_keyframe(video_path: Path, output_dir: Path, interval: float) -> list[Path]:
    video_stem = safe_stem(video_path)

    with tempfile.TemporaryDirectory(dir=str(output_dir), prefix=".extract_") as temp_name:
        temp_dir = Path(temp_name)
        # showinfoフィルタで「実際に出力したフレームの時刻」をstderrに出させて、
        # 出力ファイルと1対1でペアリングします（事前予測だと数がズレることがあるため）。
        command = [
            bundled_tool_path("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "info",
            "-nostats",
            "-y",
            "-discard",
            "nokey",
            "-i",
            ffmpeg_path(video_path),
            "-map",
            "0:v:0",
            "-vf",
            "showinfo",
            "-fps_mode",
            "passthrough",
            "-q:v",
            "2",
            ffmpeg_path(temp_dir / "frame_%06d.jpg"),
        ]
        result = run_ffmpeg(command)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg keyframe extract failed")

        frame_times = [float(value) for value in SHOWINFO_PTS_PATTERN.findall(result.stderr)]
        extracted = sorted(temp_dir.glob("frame_*.jpg"))
        if not extracted or len(extracted) != len(frame_times):
            raise RuntimeError(
                f"フレーム時刻を取得できません: 出力{len(extracted)}枚 時刻{len(frame_times)}件"
            )

        selected_indexes = set(thin_by_interval(frame_times, interval))
        frame_paths: list[Path] = []
        for index, extracted_path in enumerate(extracted):
            if index not in selected_indexes:
                continue
            frame_path = output_dir / f"{video_stem}__t{format_timestamp(frame_times[index])}.jpg"
            shutil.move(str(extracted_path), str(frame_path))
            frame_paths.append(frame_path)
        return frame_paths


def extract_frames_ffmpeg_fps(video_path: Path, output_dir: Path, interval: float) -> list[Path]:
    video_stem = safe_stem(video_path)
    with tempfile.TemporaryDirectory(dir=str(output_dir), prefix=".extract_") as temp_name:
        temp_dir = Path(temp_name)
        command = [
            bundled_tool_path("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            ffmpeg_path(video_path),
            "-map",
            "0:v:0",
            "-vf",
            f"fps=1/{interval}",
            "-q:v",
            "2",
            ffmpeg_path(temp_dir / "frame_%06d.jpg"),
        ]
        result = run_ffmpeg(command)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg fps extract failed")

        extracted = sorted(temp_dir.glob("frame_*.jpg"))
        if not extracted:
            raise RuntimeError(f"フレームを抽出できません: {video_path}")

        frame_paths: list[Path] = []
        for index, extracted_path in enumerate(extracted):
            time_sec = index * interval
            frame_path = output_dir / f"{video_stem}__t{format_timestamp(time_sec)}.jpg"
            shutil.move(str(extracted_path), str(frame_path))
            frame_paths.append(frame_path)
        return frame_paths


def extract_frames_opencv(video_path: Path, output_dir: Path, interval: float) -> list[Path]:
    video_stem = safe_stem(video_path)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"動画を開けません: {video_path}")

    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        if duration <= 0:
            raise ValueError(f"動画の長さを取得できません: {video_path}")

        frame_paths: list[Path] = []
        time_sec = 0.0
        while time_sec <= duration + 0.001:
            capture.set(cv2.CAP_PROP_POS_MSEC, time_sec * 1000.0)
            success, frame = capture.read()
            if not success:
                break
            frame_path = output_dir / f"{video_stem}__t{format_timestamp(time_sec)}.jpg"
            write_image(frame_path, frame)
            frame_paths.append(frame_path)
            time_sec += interval
        return frame_paths
    finally:
        capture.release()


def safe_stem(path: Path) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in path.stem)


def video_name_candidates(video_path: Path) -> set[str]:
    # --only-video の照合用に、ファイル名 / 拡張子なしstem / safe_stem の
    # いずれで指定されても一致するよう候補を作ります。
    return {video_path.name.lower(), video_path.stem.lower(), safe_stem(video_path).lower()}


def only_video_matches(candidates: set[str], only_value: str) -> bool:
    value = only_value.strip().lower()
    if not value:
        return False
    if value in candidates:
        return True
    if "." in value:
        return value.rsplit(".", 1)[0] in candidates
    return False


def filter_videos_by_only(videos: list[Path], only_value: str | None) -> list[Path]:
    # --only-video 未指定時は従来どおり全動画を処理します。
    if not only_value:
        return videos
    return [video for video in videos if only_video_matches(video_name_candidates(video), only_value)]


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(path.suffix or ".jpg", image)
    if not success:
        raise ValueError(f"画像を書き出せません: {path}")
    encoded.tofile(str(path))


def format_timestamp(seconds: float) -> str:
    return f"{max(0.0, seconds):09.2f}"


def format_seconds(seconds: float) -> str:
    return f"{max(0.0, seconds):.3f}"


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
    write_dict_csv(csv_path, fieldnames, rows)


def merge_result_rows_for_only_video(
    csv_path: Path,
    new_rows: list[dict[str, object]],
    targeted_video_paths: list[Path],
) -> list[dict[str, object]]:
    # --only-video 指定時はこの実行で処理した動画分しか new_rows に含まれないため、
    # 既存の result.csv から「対象外の動画のフレーム行」を読み直して残し、
    # 他の動画の採点・選別情報を消さないようにします。写真行は毎回全件処理される
    # ため、旧い写真行は今回の結果で置き換えます。
    if not csv_path.exists():
        return new_rows

    targeted = {str(path.resolve()) for path in targeted_video_paths}
    kept_rows: list[dict[str, object]] = []
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                row_type = row.get("type", "")
                if row_type in ("photo", "photo_copy"):
                    continue
                source_path = row.get("source_path", "")
                if source_path in targeted:
                    continue
                kept_rows.append(dict(row))
    except Exception:
        logging.exception("result.csv の読み込みに失敗したため、今回の結果のみで上書きします: %s", csv_path)
        return new_rows

    return kept_rows + new_rows


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


def run_cut_job(job: CutJob, fast_copy: bool) -> dict[str, object]:
    try:
        cut_video_clip(job.video_path, job.output_path, job.start_sec, job.end_sec, fast_copy)
        return clip_cut_row(
            job.video_stem,
            job.video_path,
            job.first_frame,
            job.last_frame,
            job.start_sec,
            job.end_sec,
            job.output_path,
            "ok",
            f"{job.frame_count} selected frames",
        )
    except Exception as exc:
        logging.exception("動画切り出しエラー: %s", job.video_path)
        return clip_cut_row(
            job.video_stem,
            job.video_path,
            job.first_frame,
            job.last_frame,
            job.start_sec,
            job.end_sec,
            job.output_path,
            "error",
            str(exc),
        )


def clip_worker_count(fast_copy: bool) -> int:
    cpu_count = os.cpu_count() or 2
    if fast_copy:
        # 再エンコードなし（ストリームコピー）はCPU負荷が軽いため、コア数に応じて並列化します。
        return max(1, min(4, cpu_count))
    if detect_video_encoder() in ("h264_qsv", "h264_mf", "h264_amf", "h264_nvenc"):
        # ハードウェアエンコーダは同時セッション数に制限があることが多いため、控えめにします。
        return 2
    return max(1, min(4, cpu_count - 1))


def run_cut_jobs(jobs: list[CutJob], fast_copy: bool) -> list[dict[str, object]]:
    if not jobs:
        return []
    worker_count = clip_worker_count(fast_copy)
    if worker_count <= 1 or len(jobs) <= 1:
        return [run_cut_job(job, fast_copy) for job in jobs]
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        return list(pool.map(lambda job: run_cut_job(job, fast_copy), jobs))


def cut_from_selected_frames(
    root: Path,
    split_gap: float,
    pre_roll: float,
    post_roll: float,
    fast_copy: bool,
) -> int:
    logs = root / "logs"
    setup_logging(logs)
    input_videos = root / "input" / "videos"
    selected_frames = root / "selected_frames"
    selected_clips = root / "selected_clips"
    selected_clips.mkdir(parents=True, exist_ok=True)

    videos_by_stem = {safe_stem(video): video for video in iter_files(input_videos, VIDEO_EXTENSIONS)}
    frames_by_video: dict[str, list[SelectedFrame]] = {}
    rows: list[dict[str, object]] = []

    for frame_path in iter_files_recursive(selected_frames, PHOTO_EXTENSIONS):
        try:
            selected = parse_selected_frame(frame_path)
            frames_by_video.setdefault(selected.video_stem, []).append(selected)
        except Exception as exc:
            logging.exception("selected_frames解析エラー: %s", frame_path)
            rows.append(clip_cut_row("", "", "", "", "", "", "", "error", str(exc)))

    jobs: list[CutJob] = []
    for video_stem, frames in sorted(frames_by_video.items()):
        video_path = videos_by_stem.get(video_stem)
        if not video_path:
            message = f"元動画が見つかりません: {video_stem}"
            logging.error(message)
            for frame in frames:
                rows.append(clip_cut_row(video_stem, "", frame.path, "", "", "", "", "error", message))
            continue

        duration = get_video_duration(video_path)
        groups = group_selected_frames(frames, split_gap)
        for index, group in enumerate(groups, start=1):
            first = min(group, key=lambda item: item.time_sec)
            last = max(group, key=lambda item: item.time_sec)
            start_sec = max(0.0, first.time_sec - max(0.0, pre_roll))
            end_sec = last.time_sec + max(0.0, post_roll)
            if duration > 0:
                end_sec = min(duration, end_sec)
            if end_sec <= start_sec:
                end_sec = start_sec + 0.5

            output_name = (
                f"{video_stem}__clip{index:03d}"
                f"__t{format_timestamp(start_sec)}_t{format_timestamp(end_sec)}.mp4"
            )
            output_path = unique_output_path(selected_clips, output_name)
            jobs.append(
                CutJob(
                    video_stem=video_stem,
                    video_path=video_path,
                    output_path=output_path,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    first_frame=first.path,
                    last_frame=last.path,
                    frame_count=len(group),
                )
            )

    rows.extend(run_cut_jobs(jobs, fast_copy))

    write_clip_cuts_csv(logs / "clip_cuts.csv", rows)
    ok_count = sum(1 for row in rows if row["status"] == "ok")
    error_count = sum(1 for row in rows if row["status"] == "error")
    print(f"切り出し完了: {ok_count} 件")
    print(f"エラー: {error_count} 件")
    print(f"CSV: {logs / 'clip_cuts.csv'}")
    print(f"出力先: {selected_clips}")
    # 1件でも失敗したら異常終了にします（GUIは終了コードで成否を判定するため）。
    return 1 if error_count else 0


def read_ai_segments_csv(
    csv_path: Path,
    videos_by_stem: dict[str, Path],
    only_video: str | None,
) -> dict[str, list[tuple[int, float, float, int, float]]]:
    # (segment_index, start_sec, end_sec, frame_count, mean_score) を動画stemごとに集めます。
    segments_by_video: dict[str, list[tuple[int, float, float, int, float]]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            video_stem = row.get("video_stem") or ""
            if not video_stem:
                continue
            if only_video:
                candidates = {video_stem.lower()}
                video_path = videos_by_stem.get(video_stem)
                if video_path is not None:
                    candidates |= video_name_candidates(video_path)
                if not only_video_matches(candidates, only_video):
                    continue
            try:
                segment_index = int(row["segment_index"])
                start_sec = float(row["start_sec"])
                end_sec = float(row["end_sec"])
                frame_count = int(row["frame_count"])
                mean_score = float(row["mean_score"])
            except (KeyError, TypeError, ValueError):
                logging.exception("ai_segments.csv の行を解析できません: %s", row)
                continue
            segments_by_video.setdefault(video_stem, []).append(
                (segment_index, start_sec, end_sec, frame_count, mean_score)
            )
    return segments_by_video


def cut_from_ai_segments(
    root: Path,
    pre_roll: float,
    post_roll: float,
    fast_copy: bool,
    only_video: str | None,
) -> int:
    # logs/ai_segments.csv (frame_scorer.py predict の区間提案)から直接切り出す、
    # おまかせモード用のモードです。selected_frames を経由しません。
    logs = root / "logs"
    setup_logging(logs)
    input_videos = root / "input" / "videos"
    selected_clips = root / "selected_clips"
    selected_clips.mkdir(parents=True, exist_ok=True)
    auto_clip_cuts_csv = logs / "auto_clip_cuts.csv"

    segments_csv = logs / "ai_segments.csv"
    if not segments_csv.exists():
        message = f"区間提案CSVが見つかりません: {segments_csv}"
        logging.error(message)
        print(f"エラー: {message}")
        print("先に frame_scorer.py predict を実行してください。")
        return 1

    videos_by_stem = {safe_stem(video): video for video in iter_files(input_videos, VIDEO_EXTENSIONS)}

    try:
        segments_by_video = read_ai_segments_csv(segments_csv, videos_by_stem, only_video)
    except Exception as exc:
        logging.exception("ai_segments.csv の読み込みエラー")
        print(f"エラー: 区間提案CSVを読み込めません: {exc}")
        return 1

    rows: list[dict[str, object]] = []

    if not segments_by_video:
        message = "区間提案が0件です（切り出し対象なし）"
        print(message)
        write_clip_cuts_csv(auto_clip_cuts_csv, rows)
        print(f"CSV: {auto_clip_cuts_csv}")
        print(f"出力先: {selected_clips}")
        return 0

    jobs: list[CutJob] = []
    for video_stem, segments in sorted(segments_by_video.items()):
        video_path = videos_by_stem.get(video_stem)
        if not video_path:
            message = f"元動画が見つかりません: {video_stem}"
            logging.error(message)
            for segment_index, start_sec, end_sec, _frame_count, _mean_score in segments:
                rows.append(
                    clip_cut_row(video_stem, "", "", "", start_sec, end_sec, "", "error", message)
                )
            continue

        duration = get_video_duration(video_path)
        for segment_index, start_sec, end_sec, frame_count, mean_score in sorted(segments):
            clipped_start = max(0.0, start_sec - max(0.0, pre_roll))
            clipped_end = end_sec + max(0.0, post_roll)
            if duration > 0:
                clipped_end = min(duration, clipped_end)
            clipped_start = min(clipped_start, clipped_end)
            if clipped_end <= clipped_start:
                clipped_end = clipped_start + 0.5

            output_name = (
                f"{video_stem}__aiclip{segment_index:03d}"
                f"__t{format_timestamp(clipped_start)}_t{format_timestamp(clipped_end)}.mp4"
            )
            output_path = unique_output_path(selected_clips, output_name)
            jobs.append(
                CutJob(
                    video_stem=video_stem,
                    video_path=video_path,
                    output_path=output_path,
                    start_sec=clipped_start,
                    end_sec=clipped_end,
                    first_frame=Path(f"ai_segment{segment_index}_score{mean_score:.3f}"),
                    last_frame=Path(f"ai_frames{frame_count}"),
                    frame_count=frame_count,
                )
            )

    rows.extend(run_cut_jobs(jobs, fast_copy))

    write_clip_cuts_csv(auto_clip_cuts_csv, rows)
    ok_count = sum(1 for row in rows if row["status"] == "ok")
    error_count = sum(1 for row in rows if row["status"] == "error")
    print(f"AI区間からの切り出し完了: {ok_count} 件")
    print(f"エラー: {error_count} 件")
    print(f"CSV: {auto_clip_cuts_csv}")
    print(f"出力先: {selected_clips}")
    # 1件でも失敗したら異常終了にします（GUIは終了コードで成否を判定するため）。
    return 1 if error_count else 0


def iter_files_recursive(directory: Path, extensions: set[str]) -> Iterable[Path]:
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    )


def parse_selected_frame(path: Path) -> SelectedFrame:
    match = FRAME_NAME_PATTERN.match(path.stem)
    if match:
        return SelectedFrame(
            path=path,
            video_stem=match.group("video_stem"),
            time_sec=float(match.group("time")),
        )

    legacy_match = LEGACY_FRAME_NAME_PATTERN.match(path.stem)
    if legacy_match:
        frame_number = int(legacy_match.group("number"))
        return SelectedFrame(
            path=path,
            video_stem=legacy_match.group("video_stem"),
            time_sec=max(0.0, float(frame_number - 1)),
        )

    raise ValueError(f"フレーム名から動画名と秒数を読めません: {path.name}")


def group_selected_frames(frames: list[SelectedFrame], split_gap: float) -> list[list[SelectedFrame]]:
    if not frames:
        return []
    sorted_frames = sorted(frames, key=lambda item: item.time_sec)
    groups: list[list[SelectedFrame]] = [[sorted_frames[0]]]
    gap = max(0.0, split_gap)
    for frame in sorted_frames[1:]:
        previous = groups[-1][-1]
        if frame.time_sec - previous.time_sec <= gap:
            groups[-1].append(frame)
        else:
            groups.append([frame])
    return groups


def get_video_duration(video_path: Path) -> float:
    command = [
        bundled_tool_path("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        ffmpeg_path(video_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return 0.0
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


@lru_cache(maxsize=None)
def get_video_resolution(video_path: Path) -> tuple[int, int]:
    # 同じ動画から多数のクリップを切り出すため、パスごとに結果をキャッシュします。
    # 取得に失敗した場合は 1920x1080（12M相当）を安全側の既定値として返します。
    command = [
        bundled_tool_path("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        ffmpeg_path(video_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return 1920, 1080
    if result.returncode != 0:
        return 1920, 1080
    try:
        first_line = result.stdout.strip().splitlines()[0]
        width_str, height_str = first_line.split(",")[:2]
        return int(width_str), int(height_str)
    except (ValueError, IndexError):
        return 1920, 1080


def bitrate_for_resolution(width: int, height: int) -> str:
    # 出力解像度に応じた再エンコードのビットレート。ピクセル数で判定する
    # （縦横どちら向きでも同じ扱いにするため）。
    pixels = width * height
    if pixels >= int(3840 * 2160 * 0.9):
        return "40M"
    if pixels >= int(2560 * 1440 * 0.9):
        return "24M"
    if pixels >= int(1920 * 1080 * 0.9):
        return "12M"
    return "8M"


# H.264 の上限。一辺は実装側（QSV等）が 4096px で頭打ちになり、総画素は Level 6.2 の
# MaxFS 139264 マクロブロック（約35.6メガピクセル）が上限です。GoPro の 8:7 フルセンサー
# 素材（5312x4648）は横幅が 4096px を超えるため再エンコードできません。
H264_MAX_SIDE = 4096
H264_MAX_PIXELS = 139264 * 16 * 16


def exceeds_h264_limit(width: int, height: int) -> bool:
    # 規格上の制約なので、エンコーダを qsv から libopenh264 に替えても解決しません。
    return max(width, height) > H264_MAX_SIDE or width * height > H264_MAX_PIXELS


def cut_video_clip(
    video_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
    fast_copy: bool,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, end_sec - start_sec)
    command = [
        bundled_tool_path("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        format_seconds(start_sec),
        "-i",
        ffmpeg_path(video_path),
        "-t",
        format_seconds(duration),
    ]
    width, height = get_video_resolution(video_path)
    # H.264 に収まらない解像度は再エンコードできないため、ストリームコピーへ切り替えます。
    if fast_copy or exceeds_h264_limit(width, height):
        command.extend(["-c", "copy"])
    else:
        command.extend([
            "-c:v",
            detect_video_encoder(),
            "-b:v",
            bitrate_for_resolution(width, height),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
        ])
    command.append(ffmpeg_path(output_path))
    result = run_ffmpeg(command)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "ffmpeg failed"
        raise RuntimeError(message)


def run_ffmpeg(command: list[str]) -> subprocess.CompletedProcess[str]:
    # Windowsの既定エンコーディング(cp932)だとffmpegのUTF-8出力が化けるため明示します。
    return subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")


def clip_cut_row(
    video_stem: object,
    video_path: object,
    first_frame: object,
    last_frame: object,
    start_sec: object,
    end_sec: object,
    output_path: object,
    status: str,
    message: str,
) -> dict[str, object]:
    duration = ""
    if isinstance(start_sec, (int, float)) and isinstance(end_sec, (int, float)):
        duration = round(max(0.0, end_sec - start_sec), 3)
    return {
        "video_stem": str(video_stem) if video_stem else "",
        "video_path": str(video_path) if video_path else "",
        "first_frame": str(first_frame) if first_frame else "",
        "last_frame": str(last_frame) if last_frame else "",
        "start_sec": round(start_sec, 3) if isinstance(start_sec, (int, float)) else "",
        "end_sec": round(end_sec, 3) if isinstance(end_sec, (int, float)) else "",
        "duration_sec": duration,
        "output_path": str(output_path) if output_path else "",
        "status": status,
        "message": message,
    }


def write_clip_cuts_csv(csv_path: Path, rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "video_stem",
        "video_path",
        "first_frame",
        "last_frame",
        "start_sec",
        "end_sec",
        "duration_sec",
        "output_path",
        "status",
        "message",
    ]
    write_dict_csv(csv_path, fieldnames, rows)


def concat_selected_clips(
    root: Path,
    fast_copy: bool,
    order_file: Path | None,
    output_path: Path | None,
    normalize: str,
    fit: str,
) -> int:
    logs = root / "logs"
    setup_logging(logs)
    selected_clips = root / "selected_clips"
    final_dir = root / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    if output_path:
        output = output_path
    else:
        # 既定の出力名を日時付きにして、過去の結合結果を上書きしないようにします。
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = final_dir / f"final_video_{timestamp}.mp4"
    if not output.is_absolute():
        output = root / output

    clips = read_concat_order(order_file, selected_clips) if order_file else list(iter_files(selected_clips, VIDEO_EXTENSIONS))
    rows: list[dict[str, object]] = []

    if not clips:
        message = "selected_clips に結合対象の動画がありません"
        logging.error(message)
        write_concat_csv(logs / "concat_clips.csv", rows)
        print(message)
        return 1

    concat_inputs = clips
    try:
        if normalize != "original":
            concat_inputs = normalize_clips(root, clips, normalize, fit)
            fast_copy = False

        concat_list = logs / "concat_list.txt"
        write_ffmpeg_concat_list(concat_list, concat_inputs)
        # concat_inputs の先頭クリップ（normalize済みならその出力ファイル）を probe して
        # ビットレートを決めることで、正規化の有無にかかわらず一律に扱えます。
        concat_width, concat_height = get_video_resolution(concat_inputs[0])
        concat_bitrate = bitrate_for_resolution(concat_width, concat_height)
        # normalize=original のまま 8:7 素材（5312x4648）を結合すると H.264 に収まらないため、
        # 切り出しと同じくストリームコピーへ切り替えます。
        if exceeds_h264_limit(concat_width, concat_height):
            fast_copy = True
        concat_video_clips(concat_list, output, fast_copy, concat_bitrate)
        for index, clip in enumerate(clips, start=1):
            rows.append(concat_row(index, clip, get_video_duration(clip), output, "ok", f"{normalize}/{fit}"))
    except Exception as exc:
        logging.exception("動画結合エラー")
        for index, clip in enumerate(clips, start=1):
            rows.append(concat_row(index, clip, get_video_duration(clip), output, "error", str(exc)))
        write_concat_csv(logs / "concat_clips.csv", rows)
        print(f"結合エラー: {exc}")
        return 1

    write_concat_csv(logs / "concat_clips.csv", rows)
    print(f"結合完了: {output}")
    print(f"クリップ数: {len(clips)}")
    print(f"CSV: {logs / 'concat_clips.csv'}")
    return 0


def read_concat_order(order_file: Path, selected_clips: Path) -> list[Path]:
    clips: list[Path] = []
    base = selected_clips.resolve()
    with order_file.open("r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.strip().strip('"')
            if not line:
                continue
            clip = Path(line)
            if not clip.is_absolute():
                clip = selected_clips / clip
            resolved = clip.resolve()
            if not is_relative_to(resolved, base):
                raise ValueError(f"selected_clips 外のファイルは結合できません: {clip}")
            if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError(f"動画ファイルではありません: {clip}")
            if not resolved.exists():
                raise FileNotFoundError(f"結合対象が見つかりません: {clip}")
            clips.append(resolved)
    return clips


def write_ffmpeg_concat_list(list_path: Path, clips: list[Path]) -> None:
    list_path.parent.mkdir(parents=True, exist_ok=True)
    with list_path.open("w", encoding="utf-8") as file:
        for clip in clips:
            normalized = str(clip.resolve()).replace("\\", "/").replace("'", "'\\''")
            file.write(f"file '{normalized}'\n")


def normalize_clips(root: Path, clips: list[Path], normalize: str, fit: str) -> list[Path]:
    width, height = normalized_size(normalize)
    output_dir = root / "work" / "normalized_clips"
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_paths: list[Path] = []

    for index, clip in enumerate(clips, start=1):
        output = output_dir / f"{index:03d}_{safe_stem(clip)}_{normalize}_{fit}.mp4"
        normalize_clip(clip, output, width, height, fit)
        normalized_paths.append(output)
    return normalized_paths


def normalized_size(normalize: str) -> tuple[int, int]:
    if normalize == "vertical":
        return 1080, 1920
    if normalize == "horizontal":
        return 1920, 1080
    if normalize == "square":
        return 1080, 1080
    raise ValueError(f"未対応の正規化形式です: {normalize}")


def normalize_clip(input_path: Path, output_path: Path, width: int, height: int, fit: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fit == "contain":
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            "fps=30,format=yuv420p"
        )
    elif fit == "cover":
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            "fps=30,format=yuv420p"
        )
    else:
        raise ValueError(f"未対応のfitです: {fit}")

    command = [
        bundled_tool_path("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        ffmpeg_path(input_path),
        "-vf",
        video_filter,
        "-c:v",
        detect_video_encoder(),
        "-b:v",
        bitrate_for_resolution(width, height),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        ffmpeg_path(output_path),
    ]
    result = run_ffmpeg(command)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "ffmpeg normalize failed"
        raise RuntimeError(message)


def concat_video_clips(
    concat_list: Path,
    output_path: Path,
    fast_copy: bool,
    video_bitrate: str = "8M",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        bundled_tool_path("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        ffmpeg_path(concat_list),
    ]
    if fast_copy:
        command.extend(["-c", "copy"])
    else:
        command.extend([
            "-c:v",
            detect_video_encoder(),
            "-b:v",
            video_bitrate,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
        ])
    command.append(ffmpeg_path(output_path))
    result = run_ffmpeg(command)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "ffmpeg failed"
        raise RuntimeError(message)


def concat_row(
    index: int,
    clip_path: Path,
    duration_sec: float,
    output_path: Path,
    status: str,
    message: str,
) -> dict[str, object]:
    return {
        "order": index,
        "clip_path": str(clip_path),
        "duration_sec": round(duration_sec, 3) if duration_sec else "",
        "output_path": str(output_path),
        "status": status,
        "message": message,
    }


def write_concat_csv(csv_path: Path, rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["order", "clip_path", "duration_sec", "output_path", "status", "message"]
    write_dict_csv(csv_path, fieldnames, rows)


def cleanup_after_cut(
    root: Path,
    confirm_delete: bool,
    dry_run: bool,
    include_review_frames: bool,
    include_review_photos: bool,
    include_work: bool,
    include_selected_clips: bool,
) -> int:
    logs = root / "logs"
    setup_logging(logs)
    cleanup_targets = [root / "selected_frames"]
    if include_review_frames:
        cleanup_targets.append(root / "review" / "frames")
    if include_review_photos:
        cleanup_targets.append(root / "review" / "photos")
    if include_work:
        cleanup_targets.append(root / "work")
    if include_selected_clips:
        cleanup_targets.append(root / "selected_clips")

    actual_delete = confirm_delete and not dry_run
    rows: list[dict[str, object]] = []

    for target_dir in cleanup_targets:
        rows.extend(cleanup_files_in_directory(target_dir, actual_delete))

    write_cleanup_csv(logs / "cleanup_after_cut.csv", rows)
    action_label = "削除" if actual_delete else "削除予定"
    count = sum(1 for row in rows if row["action"] in {"deleted", "would_delete"})
    print(f"{action_label}: {count} 件")
    print(f"CSV: {logs / 'cleanup_after_cut.csv'}")
    print(f"エラーログ: {logs / 'error.log'}")
    if not actual_delete:
        print("実際に削除するには --cleanup-after-cut --confirm-delete を指定してください。")
    return 0


def cleanup_files_in_directory(target_dir: Path, actual_delete: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
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
                logging.exception("整理エラー: %s", target)
                rows.append(cleanup_row(target, target_dir, "error", "error", str(exc)))
    except Exception as exc:
        logging.exception("整理ディレクトリエラー: %s", target_dir)
        rows.append(cleanup_row(target_dir, target_dir, "error", "error", str(exc)))
    return rows


def cleanup_review(root: Path, confirm_delete: bool, dry_run: bool) -> int:
    logs = root / "logs"
    setup_logging(logs)
    cleanup_targets = [root / "review" / "photos", root / "review" / "frames"]
    actual_delete = confirm_delete and not dry_run
    rows: list[dict[str, object]] = []

    for target_dir in cleanup_targets:
        rows.extend(cleanup_files_in_directory(target_dir, actual_delete))

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
    write_dict_csv(csv_path, fieldnames, rows)


def analyze_frames(
    frame_jobs: list[tuple[Path, Path]],
    blur_threshold: float,
    dark_threshold: float,
    min_width: int,
    min_height: int,
    profile: sport_profile.SportProfile,
) -> list[dict[str, object]]:
    if not frame_jobs:
        return []

    video_by_frame = {frame_path: video_path for video_path, frame_path in frame_jobs}
    frame_paths = [frame_path for _video_path, frame_path in frame_jobs]
    # 判定（特にHOG人物検出）が最も重いため、フレーム数が多いときは並列実行します。
    # 1コアはOSとGUIのために空けておきます。
    max_workers = max(1, (os.cpu_count() or 2) - 1)
    rows: list[dict[str, object]] = []

    if max_workers == 1 or len(frame_paths) <= 4:
        results = [
            analyze_frame_task(frame_path, blur_threshold, dark_threshold, min_width, min_height, profile)
            for frame_path in frame_paths
        ]
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            results = list(
                pool.map(
                    analyze_frame_task,
                    frame_paths,
                    [blur_threshold] * len(frame_paths),
                    [dark_threshold] * len(frame_paths),
                    [min_width] * len(frame_paths),
                    [min_height] * len(frame_paths),
                    [profile] * len(frame_paths),
                )
            )

    for frame_path, analysis, error_message in results:
        video_path = video_by_frame[frame_path]
        if analysis is not None:
            rows.append(frame_row(video_path, analysis))
        else:
            logging.error("フレーム判定エラー: %s (%s)", frame_path, error_message)
            rows.append(error_row("frame", frame_path, error_message))
    return rows


def main() -> int:
    args = parse_args()
    try:
        profile = resolve_sport_profile(args)
    except sport_profile.ProfileError as exc:
        print(f"スポーツプロファイルの読み込みに失敗しました: {exc}", file=sys.stderr)
        return 2
    root = args.root.resolve()
    ensure_runtime_dirs(root)

    # 混在ガード（仕様書 2.4）。--cleanup-review は混在を解消するための救済コマンドなので対象外にする
    # （ここでブロックすると、ガードで示した対処法そのものが実行できなくなってしまう）。
    if not args.cleanup_review:
        try:
            sport_profile.check_profile_switch(root, profile.id, args.allow_profile_switch)
        except sport_profile.ProfileSwitchGuardError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if args.cleanup_review:
        return cleanup_review(root, args.confirm_delete, args.dry_run)
    if args.cleanup_after_cut:
        return cleanup_after_cut(
            root,
            args.confirm_delete,
            args.dry_run,
            args.include_review_frames,
            args.include_review_photos,
            args.include_work,
            args.include_selected_clips,
        )
    if args.cut_from_selected_frames:
        return cut_from_selected_frames(
            root,
            args.split_gap,
            args.pre_roll,
            args.post_roll,
            args.fast_copy,
        )
    if args.cut_from_ai_segments:
        return cut_from_ai_segments(
            root,
            args.pre_roll,
            args.post_roll,
            args.fast_copy,
            args.only_video,
        )
    if args.concat_selected_clips:
        return concat_selected_clips(
            root,
            args.fast_copy,
            args.concat_order_file,
            args.concat_output,
            args.normalize,
            args.fit,
        )

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
            photos.append(
                analyze_photo(
                    photo_path,
                    args.blur_threshold,
                    args.dark_threshold,
                    args.min_width,
                    args.min_height,
                )
            )
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

    target_videos = filter_videos_by_only(iter_files(input_videos, VIDEO_EXTENSIONS), args.only_video)
    if args.only_video and not target_videos:
        print(f"対象動画が見つかりません: {args.only_video}")

    frame_jobs: list[tuple[Path, Path]] = []
    for video_path in target_videos:
        try:
            frames = extract_video_frames(video_path, review_frames, args.frame_interval)
            frame_jobs.extend((video_path, frame_path) for frame_path in frames)
        except Exception as exc:
            logging.exception("動画フレーム抽出エラー: %s", video_path)
            rows.append(error_row("video", video_path, str(exc)))

    rows.extend(
        analyze_frames(
            frame_jobs,
            args.blur_threshold,
            args.dark_threshold,
            args.min_width,
            args.min_height,
            profile,
        )
    )

    if args.only_video:
        # --only-video 指定時は対象外の動画のフレーム行を result.csv から
        # 消さないよう、既存の内容とマージしてから書き出します。
        rows = merge_result_rows_for_only_video(logs / "result.csv", rows, target_videos)

    write_result_csv(logs / "result.csv", rows)
    print(f"完了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"CSV: {logs / 'result.csv'}")
    print(f"エラーログ: {logs / 'error.log'}")
    # 混在ガード（仕様書 2.4）用に、今回使用した競技を記録する。
    sport_profile.write_active_profile(root, profile.id)
    return 0


if __name__ == "__main__":
    # PyInstallerでexe化した際にProcessPoolExecutorの子プロセスを正しく起動するために必要です。
    multiprocessing.freeze_support()
    raise SystemExit(main())
