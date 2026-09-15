"""動画専用UIのセッション状態と安全な stream-copy 処理。"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}
SCHEMA_VERSION = 1


class SessionError(RuntimeError):
    """セッションまたは動画処理の入力・検証エラー。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str, fallback: str = "clip") -> str:
    cleaned = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠._-]+", "_", value).strip("._")
    return cleaned or fallback


@dataclass
class Material:
    material_id: str
    display_name: str
    source_path: str
    source_size: int
    source_mtime_ns: int
    duration_sec: float = 0.0
    streams: dict[str, Any] = field(default_factory=dict)
    status: str = "registered"

    @classmethod
    def from_path(cls, path: str | Path, material_id: str | None = None) -> "Material":
        source = Path(path).resolve()
        if not source.is_file() or source.suffix.lower() not in VIDEO_EXTENSIONS:
            raise SessionError(f"動画ファイルではありません: {source}")
        stat = source.stat()
        return cls(uuid.uuid4().hex if material_id is None else material_id, source.name, str(source), stat.st_size, stat.st_mtime_ns)

    def changed(self) -> bool:
        source = Path(self.source_path)
        try:
            stat = source.stat()
        except OSError:
            return True
        return stat.st_size != self.source_size or stat.st_mtime_ns != self.source_mtime_ns


    def require_current(self) -> None:
        """保存・再生に使う前に、登録時と同じ素材であることを確認する。"""
        if self.changed():
            raise SessionError(f"元動画が見つからないか変更されています。再指定・再解析してください: {self.display_name}")


@dataclass
class Candidate:
    candidate_id: str
    material_id: str
    requested_start_sec: float
    requested_end_sec: float
    effective_start_sec: float | None = None
    effective_end_sec: float | None = None
    accepted: bool = True
    order: int = 0
    persons: list[str] = field(default_factory=list)
    tricks: list[str] = field(default_factory=list)
    generation: int = 0
    preview_generation: int = 0
    preview_path: str | None = None
    preview_duration_sec: float | None = None
    preview_status: str = "unprepared"
    error: str | None = None
    thumbnail_path: str | None = None

    def set_requested_range(self, start: float, end: float) -> int:
        start, end = float(start), float(end)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise SessionError("切り出し範囲は0以上、開始<終了で指定してください")
        self.requested_start_sec, self.requested_end_sec = start, end
        self.effective_start_sec = None
        self.effective_end_sec = None
        self.generation += 1
        self.preview_generation = self.generation
        self.preview_path = None
        self.preview_status = "stale"
        return self.generation


@dataclass
class Session:
    session_id: str
    sport_id: str = "kite"
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    current_view: str = "start"
    current_candidate_id: str | None = None
    materials: list[Material] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    save_directory: str | None = None

    @property
    def root(self) -> Path:
        raise AttributeError("rootはSessionStore.rootを使用してください")

    def material(self, material_id: str) -> Material:
        for item in self.materials:
            if item.material_id == material_id:
                return item
        raise SessionError(f"素材IDが見つかりません: {material_id}")

    def accepted_candidates(self) -> list[Candidate]:
        return sorted((item for item in self.candidates if item.accepted), key=lambda item: item.order)


class SessionStore:
    """out/sessions/<id> を専用作業領域にする。原子的保存で破損状態を保護する。"""

    def __init__(self, output_root: str | Path, session_id: str | None = None) -> None:
        base = Path(output_root).resolve()
        chosen = session_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", chosen):
            raise SessionError("セッションIDが不正です")
        sessions_root = (base / "sessions").resolve()
        self.root = (sessions_root / chosen).resolve()
        if self.root.parent != sessions_root:
            raise SessionError("セッション保存先がroot外です")
        self.root.mkdir(parents=True, exist_ok=True)
        self.json_path = self.root / "session.json"

    def save(self, session: Session) -> None:
        session.updated_at = utc_now()
        payload = asdict(session)
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="session-", suffix=".tmp", dir=str(self.root), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.json_path)
        finally:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass

    def load(self) -> Session:
        if not self.json_path.is_file():
            raise SessionError(f"セッション状態がありません: {self.json_path}")
        try:
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
                raise SessionError("対応していないセッション形式です")
            raw_materials, raw_candidates = data.get("materials", []), data.get("candidates", [])
            if not isinstance(raw_materials, list) or not isinstance(raw_candidates, list):
                raise SessionError("セッション配列が不正です")
            if data.get("session_id") != self.root.name or not isinstance(data.get("session_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", data["session_id"]):
                raise SessionError("セッションIDが不正です")
            materials = [Material(**item) for item in raw_materials]
            candidates = [Candidate(**item) for item in raw_candidates]
            if any(not isinstance(item.material_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", item.material_id) for item in materials):
                raise SessionError("素材IDが不正です")
            material_ids = {item.material_id for item in materials}
            if any(not isinstance(item.candidate_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", item.candidate_id) for item in candidates):
                raise SessionError("候補IDが不正です")
            candidate_ids = [item.candidate_id for item in candidates]
            if len(material_ids) != len(materials) or len(set(candidate_ids)) != len(candidate_ids):
                raise SessionError("素材または候補IDが重複しています")
            for item in candidates:
                if item.preview_path is not None and not is_relative_to(Path(item.preview_path).resolve(), self.root):
                    raise SessionError("プレビュー参照がセッションroot外です")
                material = next((m for m in materials if m.material_id == item.material_id), None)
                if material is None or not math.isfinite(material.duration_sec) or material.duration_sec < 0:
                    raise SessionError("素材durationまたは参照が不正です")
                if item.requested_end_sec > material.duration_sec + 0.05:
                    raise SessionError("候補範囲が素材durationを超えています")
                if item.material_id not in material_ids or not math.isfinite(item.requested_start_sec) or not math.isfinite(item.requested_end_sec) or item.requested_start_sec < 0 or item.requested_end_sec <= item.requested_start_sec:
                    raise SessionError("候補の参照または範囲が不正です")
            data["materials"], data["candidates"] = materials, candidates
            data["schema_version"] = SCHEMA_VERSION
            return Session(**data)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SessionError(f"セッション状態を読み込めません: {exc}") from exc


def _tool(root: str | Path, name: str) -> str:
    candidate = Path(root).resolve() / "vendor" / "ffmpeg" / "windows-x64" / f"{name}.exe"
    return str(candidate) if candidate.is_file() else name


def probe(path: str | Path, app_root: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    if not source.is_file():
        raise SessionError(f"動画が見つかりません: {source}")
    command = [_tool(app_root, "ffprobe"), "-v", "error", "-show_format", "-show_streams", "-show_data_hash", "sha256", "-of", "json", str(source)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except subprocess.TimeoutExpired as exc:
        raise SessionError("ffprobeがタイムアウトしました") from exc
    except OSError as exc:
        raise SessionError(f"ffprobeを起動できません: {exc}") from exc
    if result.returncode != 0:
        raise SessionError(result.stderr.strip() or "ffprobeに失敗しました")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SessionError("ffprobeの結果を解釈できません") from exc
    if not isinstance(data, dict):
        raise SessionError("ffprobeの結果が不正です")
    return data


def _stream_signature(data: dict[str, Any]) -> tuple[Any, ...]:
    stable_keys = ("codec_type", "codec_name", "profile", "level", "codec_tag_string", "codec_tag", "width", "height", "pix_fmt", "field_order", "sample_aspect_ratio", "display_aspect_ratio", "time_base", "sample_fmt", "sample_rate", "channels", "channel_layout", "extradata_size", "extradata_hash")
    values = []
    for stream in data.get("streams") or []:
        if not isinstance(stream, dict): continue
        item = {key: stream.get(key) for key in stable_keys}
        tags = stream.get("tags") or {}
        if "rotate" in tags: item["rotate"] = tags.get("rotate")
        values.append(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    return tuple(values)

def compatible_for_concat(paths: Iterable[str | Path], app_root: str | Path) -> tuple[bool, str]:
    files = [Path(path).resolve() for path in paths]
    if not files:
        return False, "結合対象がありません"
    signatures = []
    for path in files:
        try:
            signatures.append(_stream_signature(probe(path, app_root)))
        except SessionError as exc:
            return False, str(exc)
    if any(signature != signatures[0] for signature in signatures[1:]):
        return False, "映像・音声コーデック、解像度、時間基準などが一致しません"
    return True, ""


def snap_range(source: str | Path, start_sec: float, end_sec: float, app_root: str | Path) -> tuple[float, float]:
    """stream copyで利用できるキーフレーム境界へ範囲を寄せる。"""
    start, end = float(start_sec), float(end_sec)
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start: raise SessionError("切り出し範囲が不正です")
    command = [_tool(app_root, "ffprobe"), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(Path(source).resolve())]
    try: result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
    except subprocess.TimeoutExpired as exc: raise SessionError("キーフレーム境界の取得がタイムアウトしました") from exc
    except OSError as exc: raise SessionError(f"キーフレーム境界を取得できません: {exc}") from exc
    points = []
    for line in result.stdout.splitlines():
        try: points.append(float(line.strip().split(",", 1)[0]))
        except (ValueError, IndexError): pass
    points = sorted(x for x in points if math.isfinite(x) and x >= 0)
    if not points: raise SessionError("キーフレーム境界を取得できない形式です")
    snapped_start = max((x for x in points if x <= start), default=points[0])
    after_end = [x for x in points if x >= end]
    snapped_end = after_end[0] if after_end else end
    return (snapped_start, snapped_end if snapped_end > snapped_start else end)

def unique_output(directory: str | Path, stem: str, suffix: str) -> Path:
    target_dir = Path(directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_name(stem)
    suffix = suffix if suffix.startswith(".") else "." + suffix
    for index in range(1, 10000):
        candidate = target_dir / f"{safe}{suffix}" if index == 1 else target_dir / f"{safe}_{index:03d}{suffix}"
        if not candidate.exists():
            return candidate
    raise SessionError("保存先に一意なファイル名を作れません")


def copy_file_exclusive(source: str | Path, output: str | Path) -> Path:
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    if not source_path.is_file():
        raise SessionError(f"コピー元が見つかりません: {source_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise SessionError(f"既存ファイルを上書きしません: {output_path}")
    partial = output_path.with_name(output_path.name + ".partial-" + uuid.uuid4().hex)
    source_stat = source_path.stat()
    try:
        with source_path.open("rb") as src, partial.open("xb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if partial.stat().st_size != source_stat.st_size:
            raise SessionError("コピー後のサイズ検証に失敗しました")
        if output_path.exists():
            raise SessionError(f"既存ファイルを上書きしません: {output_path}")
        os.rename(partial, output_path)
        return output_path
    except FileExistsError as exc:
        raise SessionError(f"既存ファイルを上書きしません: {output_path}") from exc

def stream_copy_clip(source: str | Path, output: str | Path, start_sec: float, end_sec: float, app_root: str | Path) -> Path:
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    if not source_path.is_file():
        raise SessionError(f"元動画が見つかりません: {source_path}")
    if output_path.exists():
        raise SessionError(f"既存ファイルを上書きしません: {output_path}")
    start, end = float(start_sec), float(end_sec)
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise SessionError("切り出し範囲が不正です")
    source_stat = source_path.stat()
    metadata = probe(source_path, app_root)
    try:
        duration = float((metadata.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        raise SessionError("元動画のdurationを取得できません")
    if not math.isfinite(duration) or duration <= 0 or end > duration + 0.05:
        raise SessionError("切り出し範囲が元動画の長さを超えています")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.stem + ".partial-" + uuid.uuid4().hex + output_path.suffix)
    command = [_tool(app_root, "ffmpeg"), "-hide_banner", "-loglevel", "error", "-n", "-ss", f"{start:.6f}", "-i", str(source_path), "-t", f"{end-start:.6f}", "-map", "0", "-c", "copy", str(temp_path)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode != 0 or not temp_path.is_file() or temp_path.stat().st_size == 0:
        raise SessionError(result.stderr.strip() or "stream copyに失敗しました")
    if source_path.stat().st_size != source_stat.st_size or source_path.stat().st_mtime_ns != source_stat.st_mtime_ns:
        raise SessionError("コピー中に元動画が変更されました")
    verified = probe(temp_path, app_root)
    streams = verified.get("streams") or []
    if not any(item.get("codec_type") == "video" for item in streams):
        raise SessionError("出力に映像ストリームがありません")
    if output_path.exists():
        raise SessionError(f"既存ファイルを上書きしません: {output_path}")
    os.rename(temp_path, output_path)
    return output_path