"""UIから呼び出す動画解析API。TkとSessionには依存しません。"""

from __future__ import annotations

import hashlib
import math
import uuid
from pathlib import Path
from threading import Event
from typing import Any

import cv2
import joblib
import numpy as np

import frame_scorer
import kite_clipper
import sport_profile


def _cancelled(cancel_event: Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _material_id(source: Path) -> str:
    """同名動画でも解析成果物が衝突しない安定IDを作る。"""
    stat = source.stat()
    token = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(token).hexdigest()[:20]


def _thumbnail(frame: np.ndarray, path: Path) -> Path:
    path = path.with_suffix(".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = frame.shape[:2]
    scale = min(480.0 / max(width, 1), 270.0 / max(height, 1), 1.0)
    if scale < 1.0:
        frame = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError(f"サムネイルを書き込めません: {path}")
    encoded.tofile(str(path))
    return path


def _extract_samples(source: Path, out_dir: Path, interval: float, cancel_event: Event | None) -> tuple[list[float], list[Path], list[np.ndarray], float]:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"動画を開けません: {source}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        duration = count / fps if fps > 0 and count > 0 else 0.0
        if duration <= 0:
            raise RuntimeError("動画の長さを取得できません")
        times: list[float] = []
        frame_paths: list[Path] = []
        thumbnails: list[np.ndarray] = []
        t = 0.0
        while t <= duration + 0.001:
            if _cancelled(cancel_event):
                break
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
            ok, frame = capture.read()
            if not ok:
                break
            times.append(round(t, 3))
            sample_path = out_dir / f"{source.stem}__sample_{len(frame_paths):06d}.jpg"
            if sample_path.exists() or not cv2.imwrite(str(sample_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 90]):
                raise RuntimeError(f"フレームを書き込めません: {sample_path}")
            frame_paths.append(sample_path)
            height, width = frame.shape[:2]
            scale = min(480.0 / max(width, 1), 270.0 / max(height, 1), 1.0)
            thumbnails.append(cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA) if scale < 1.0 else frame.copy())
            t += max(interval, 0.1)
        if not frame_paths and not _cancelled(cancel_event):
            raise RuntimeError("動画からフレームを読み込めません")
        return times, frame_paths, thumbnails, duration
    finally:
        capture.release()


def _raw_rows(source: Path, times: list[float], frame_paths: list[Path], profile: sport_profile.SportProfile, cancel_event: Event | None) -> tuple[list[dict[str, Any]], list[Path]]:
    rows: list[dict[str, Any]] = []
    paths: list[Path] = []
    for index, (time_sec, path) in enumerate(zip(times, frame_paths)):
        if _cancelled(cancel_event):
            break
        _path, analysis, error = kite_clipper.analyze_frame_task(
            path,
            float(profile.cv.get("blur_threshold", kite_clipper.DEFAULT_BLUR_THRESHOLD)),
            float(profile.cv.get("dark_threshold", kite_clipper.DEFAULT_DARK_THRESHOLD)),
            int(profile.cv.get("min_width", kite_clipper.DEFAULT_MIN_WIDTH)),
            int(profile.cv.get("min_height", kite_clipper.DEFAULT_MIN_HEIGHT)),
            profile,
        )
        if analysis is None:
            raise RuntimeError(f"フレーム解析に失敗しました: {error}")
        rows.append(kite_clipper.frame_row(source, analysis) | {"time_sec": time_sec, "heuristic_score": analysis.score})
        paths.append(path)
    return rows, paths


def analyze_material(
    source_path: str | Path,
    app_root: str | Path,
    work_root: str | Path,
    sport_id: str = "kite",
    cancel_event: Event | None = None,
) -> dict[str, Any]:
    """動画をサンプリングし、既存モデルで採点して区間候補を返します。"""
    source = Path(source_path).resolve()
    try:
        material_id = uuid.uuid5(uuid.NAMESPACE_URL, _material_id(source)).hex[:20] if source.exists() else uuid.uuid4().hex[:20]
    except OSError:
        material_id = uuid.uuid4().hex[:20]
    result: dict[str, Any] = {
        "status": "error", "material_id": material_id, "source_path": str(source),
        "duration_sec": 0.0, "thumbnail_path": None, "candidates": [], "error": None,
    }
    try:
        if _cancelled(cancel_event):
            result["status"] = "cancelled"
            return result
        if not source.is_file():
            raise FileNotFoundError(f"動画が見つかりません: {source}")
        profile = sport_profile.load_profile(Path(app_root).resolve(), sport_id)
        output_dir = Path(work_root).resolve() / "video_analysis" / f"{material_id}-{uuid.uuid4().hex[:8]}"
        output_dir.mkdir(parents=True, exist_ok=True)
        model_path = profile.model_path
        if not model_path.is_file():
            capture = cv2.VideoCapture(str(source))
            try:
                ok, frame = capture.read()
                duration = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0) / float(capture.get(cv2.CAP_PROP_FPS) or 1.0)
            finally:
                capture.release()
            if not math.isfinite(duration) or duration <= 0:
                raise RuntimeError("動画の長さが不正です")
            result["duration_sec"] = duration
            if ok:
                result["thumbnail_path"] = str(_thumbnail(frame, output_dir / "material_thumbnail.jpg"))
            result["status"] = "cancelled" if _cancelled(cancel_event) else "no_model"
            result["error"] = f"学習済みモデルが見つかりません: {model_path}"
            return result
        times, frame_paths, thumbnails, duration = _extract_samples(source, output_dir, float(profile.cv.get("frame_interval", 1.0)), cancel_event)
        result["duration_sec"] = duration
        if thumbnails:
            result["thumbnail_path"] = str(_thumbnail(thumbnails[0], output_dir / "material_thumbnail.jpg"))
        if _cancelled(cancel_event):
            result["status"] = "cancelled"
            return result
        rows, frame_paths = _raw_rows(source, times, frame_paths, profile, cancel_event)
        if _cancelled(cancel_event):
            result["status"] = "cancelled"
            return result
        if not rows:
            result["status"] = "cancelled" if _cancelled(cancel_event) else "no_candidates"
            return result
        model_path = profile.model_path
        if not model_path.is_file():
            result["status"] = "no_model"
            result["error"] = f"学習済みモデルが見つかりません: {model_path}"
            return result
        bundle = joblib.load(model_path)
        model = bundle["model"]
        medians = bundle.get("medians", {})
        mode = bundle.get("features", "classic")
        vectors = np.asarray([frame_scorer.build_feature_row({key: str(value) for key, value in row.items()}, medians) for row in rows], dtype=float)
        if mode != "classic":
            clip_rel = bundle.get("clip_model_path") or frame_scorer.CLIP_MODEL_RELATIVE_PATH
            clip_path = Path(clip_rel)
            if not clip_path.is_absolute():
                clip_path = Path(app_root).resolve() / clip_path
            if not clip_path.is_file():
                result["status"] = "no_model"
                result["error"] = f"CLIPモデルが見つかりません: {clip_path}"
                return result
            embedder = frame_scorer.ClipEmbedder(model_path=clip_path, cache_dir=output_dir / "embeddings")
            clip_vectors = embedder.embed(frame_paths).astype(float)
            vectors = clip_vectors if mode == "clip" else np.hstack([vectors, clip_vectors])
        scores = np.asarray(model.predict_proba(vectors)[:, 1], dtype=float)
        enter = float(profile.segment.get("enter", frame_scorer.DEFAULT_SEGMENT_ENTER))
        exit_ = float(profile.segment.get("exit", frame_scorer.DEFAULT_SEGMENT_EXIT))
        window = int(profile.segment.get("smooth_window", frame_scorer.DEFAULT_SEGMENT_SMOOTH_WINDOW))
        segments = frame_scorer.build_segments_for_video(times[:len(scores)], scores.tolist(), enter, exit_, window)
        for index, (start, end, count, mean_score) in enumerate(segments, 1):
            indices = [i for i, time_sec in enumerate(times[:len(scores)]) if start <= time_sec <= end]
            if not indices:
                continue
            representative = max(indices, key=lambda i: scores[i])
            end_sec = min(duration, times[indices[-1]] + float(profile.cv.get("frame_interval", 1.0)))
            candidate_dir = output_dir / f"candidate_{index:03d}"
            result["candidates"].append({
                "candidate_id": f"{material_id}-{index:03d}", "start_sec": float(start),
                "representative_sec": float(times[representative]), "end_sec": float(end_sec),
                "score": float(mean_score), "frame_count": int(count),
                "start_thumbnail_path": str(_thumbnail(thumbnails[indices[0]], candidate_dir / "start.jpg")),
                "representative_thumbnail_path": str(_thumbnail(thumbnails[representative], candidate_dir / "representative.jpg")),
                "end_thumbnail_path": str(_thumbnail(thumbnails[indices[-1]], candidate_dir / "end.jpg")),
            })
        result["status"] = "ok" if result["candidates"] else "no_candidates"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
