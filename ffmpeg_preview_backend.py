"""旧 FfmpegTkPreview API の互換 wrapper。

実装本体は video_preview_backend.MciVideoPreview に統一する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from video_preview_backend import MciVideoPreview, PreviewBackendError


class FfmpegPreviewError(PreviewBackendError):
    """旧 backend 名での互換例外。"""


class FfmpegTkPreview:
    def __init__(self, parent: Any, app_root: str | Path, work_root: str | Path,
                 *, on_frame: Callable[[Any], None] | None = None,
                 on_state: Callable[[str], None] | None = None) -> None:
        self.parent = parent
        self.app_root = Path(app_root).resolve()
        self.work_root = Path(work_root).resolve()
        self._preview = MciVideoPreview(
            parent, app_root=self.app_root, on_frame=on_frame, on_state=on_state,
            on_error=lambda message: self._emit_error(message),
        )

    def _emit_error(self, message: str) -> None:
        callback = getattr(self, "on_error", None) or getattr(self.parent, "on_error", None)
        if callable(callback):
            callback(message)

    def open(self, path: str | Path) -> None:
        try:
            self._preview.open(path)
        except PreviewBackendError as exc:
            raise FfmpegPreviewError(str(exc)) from exc

    def play(self, start_sec: float | None = None) -> None:
        try:
            self._preview.play(start_ms=None if start_sec is None else round(float(start_sec) * 1000))
        except (PreviewBackendError, ValueError) as exc:
            raise FfmpegPreviewError(str(exc)) from exc

    def pause(self) -> None:
        self._preview.pause()

    def resume(self) -> None:
        self._preview.resume()

    def seek(self, position_sec: float) -> None:
        try:
            self._preview.seek(round(float(position_sec) * 1000))
        except (PreviewBackendError, ValueError) as exc:
            raise FfmpegPreviewError(str(exc)) from exc

    def stop(self) -> None:
        self._preview.stop()

    def close(self) -> None:
        self._preview.close()

    @property
    def duration_sec(self) -> float:
        return self._preview.duration_ms() / 1000.0

    @property
    def position_sec(self) -> float:
        return self._preview.position_ms() / 1000.0

    @property
    def video_label(self) -> Any:
        return self._preview.video_label


__all__ = ["FfmpegPreviewError", "FfmpegTkPreview"]
