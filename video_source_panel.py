"""元動画を選び、切り出し範囲を指定する Tk パネル。"""
from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Sequence

from video_preview_backend import MciVideoPreview, PreviewBackendError


class SourcePanel(ttk.Frame):
    """素材一覧と元動画プレビューを提供するUI部品。

    Materialを変更せず、追加・再配置・再解析はcallbackで親へ通知する。
    """

    def __init__(
        self,
        parent: Any,
        app_root: str | Path,
        materials: Sequence[Any],
        on_add: Callable[[str, float, float], None],
        on_back: Callable[[], None],
        on_relocate: Callable[[str, str], None] | None = None,
        on_reanalyze: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_root = Path(app_root).resolve()
        self.materials = list(materials)
        self.on_add, self.on_back = on_add, on_back
        self.on_relocate, self.on_reanalyze = on_relocate, on_reanalyze
        self.current: Any | None = None
        self._destroying = False
        self._drag_handle: str | None = None
        self._duration_ms = 0
        self._canvas_width = 520
        self._canvas_left, self._canvas_right = 20, 500

        self.start_var = tk.StringVar(value="0.00")
        self.end_var = tk.StringVar(value="1.00")
        self.status_var = tk.StringVar(value="元動画を選択してください")
        self.range_var = tk.StringVar(value="開始 0.00秒 / 終了 1.00秒")
        self._build()
        self._refresh_materials()

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self, padding=8)
        left.grid(row=0, column=0, sticky="ns")
        ttk.Label(left, text="元動画").pack(anchor=tk.W)
        self.material_list = tk.Listbox(left, width=34, height=16, exportselection=False)
        self.material_list.pack(fill=tk.Y, expand=True, pady=(4, 8))
        self.material_list.bind("<<ListboxSelect>>", self._on_material_selected)
        ttk.Button(left, text="場所を指定…", command=self._relocate).pack(fill=tk.X, pady=2)
        self.reanalyze_button = ttk.Button(left, text="再解析", command=self._reanalyze)
        if self.on_reanalyze is not None:
            self.reanalyze_button.pack(fill=tk.X, pady=2)

        right = ttk.Frame(self, padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.preview_host = tk.Frame(right, background="black", height=360)
        self.preview_host.grid(row=0, column=0, sticky="nsew")
        self.preview_host.grid_propagate(False)
        self.preview_host.bind("<Configure>", self._on_preview_configure)
        self.player = MciVideoPreview(
            self.preview_host, app_root=self.app_root, on_error=self._on_player_error,
        )

        controls = ttk.Frame(right)
        controls.grid(row=1, column=0, sticky="ew", pady=(5, 2))
        self.play_button = ttk.Button(controls, text="再生", command=self._play)
        self.play_button.pack(side=tk.LEFT)
        self.pause_button = ttk.Button(controls, text="一時停止", command=self._pause)
        self.pause_button.pack(side=tk.LEFT, padx=4)
        self.range_play_button = ttk.Button(controls, text="選択範囲を再生", command=self._play_range)
        self.range_play_button.pack(side=tk.LEFT)
        ttk.Label(right, textvariable=self.status_var).grid(row=2, column=0, sticky="w", pady=(5, 2))
        self.timeline = tk.Canvas(right, height=58, background="#20252b", highlightthickness=0)
        self.timeline.grid(row=3, column=0, sticky="ew")
        self.timeline.bind("<Configure>", self._on_timeline_configure)
        self.timeline.bind("<Button-1>", self._timeline_press)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)

        range_row = ttk.Frame(right)
        range_row.grid(row=4, column=0, sticky="ew", pady=6)
        ttk.Label(range_row, text="開始秒").pack(side=tk.LEFT)
        ttk.Entry(range_row, textvariable=self.start_var, width=10).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(range_row, text="終了秒").pack(side=tk.LEFT)
        ttk.Entry(range_row, textvariable=self.end_var, width=10).pack(side=tk.LEFT, padx=4)
        ttk.Label(right, textvariable=self.range_var).grid(row=5, column=0, sticky="w")
        footer = ttk.Frame(right)
        footer.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(footer, text="戻る", command=self.on_back).pack(side=tk.LEFT)
        ttk.Button(footer, text="範囲を追加", command=self._add_range).pack(side=tk.RIGHT)

    def _material_state(self, material: Any) -> tuple[str, str | None]:
        source = Path(str(material.source_path))
        if not source.is_file():
            return "missing", "元動画が見つかりません。場所を指定してください。"
        try:
            if bool(material.changed()):
                return "changed", "元動画が変更されています。場所を指定してください。"
        except (AttributeError, OSError):
            pass
        return str(getattr(material, "status", "registered")), None

    def _refresh_materials(self) -> None:
        self.material_list.delete(0, tk.END)
        for material in self.materials:
            state, _reason = self._material_state(material)
            self.material_list.insert(tk.END, f"{material.display_name}  [{state}]")

    def _on_preview_configure(self, event: Any) -> None:
        width, height = int(event.width), int(event.height)
        if width > 0 and height > 0:
            try: self.player.resize(width, height)
            except PreviewBackendError as exc: self.status_var.set(str(exc))

    def _on_player_error(self, message: str) -> None:
        self.status_var.set(f"動画再生エラー: {message}")

    def _play(self) -> None:
        try: self.player.play()
        except PreviewBackendError as exc: self.status_var.set(f"再生できません: {exc}")

    def _pause(self) -> None:
        try: self.player.pause()
        except PreviewBackendError as exc: self.status_var.set(f"一時停止できません: {exc}")

    def _play_range(self) -> None:
        parsed = self._parse_range()
        if parsed is None: return
        start, end = parsed
        try: self.player.play(start_ms=round(start * 1000), end_ms=round(end * 1000))
        except PreviewBackendError as exc: self.status_var.set(f"範囲を再生できません: {exc}")

    def _on_material_selected(self, _event: Any = None) -> None:
        selection = self.material_list.curselection()
        if not selection:
            return
        self._select_material(self.materials[selection[0]])

    def _select_material(self, material: Any) -> None:
        self.current = material
        self.player.close()
        state, reason = self._material_state(material)
        if reason is not None:
            self._duration_ms = 0
            self.status_var.set(reason)
            self._draw_timeline()
            return
        try:
            self.player.open(material.source_path)
            self.player.resize(640, 360)
            self._duration_ms = self.player.duration_ms()
            end = min(1.0, self._duration_ms / 1000.0)
            self.start_var.set("0.00")
            self.end_var.set(f"{end:.2f}")
            self.status_var.set(f"{material.display_name} を再生できます")
            self._update_range_text()
            self._draw_timeline()
            self.player.seek(0)
        except PreviewBackendError as exc:
            self._duration_ms = 0
            self.status_var.set(f"再生を停止しました: {exc}")
            self._draw_timeline()

    def _parse_range(self) -> tuple[float, float] | None:
        if self.current is None or self._duration_ms <= 0:
            self.status_var.set("再生可能な元動画を選択してください")
            return None
        try:
            start, end = float(self.start_var.get()), float(self.end_var.get())
        except (TypeError, ValueError):
            self.status_var.set("開始・終了秒は数値で指定してください")
            return None
        duration = self._duration_ms / 1000.0
        if not math.isfinite(start) or not math.isfinite(end):
            self.status_var.set("NaNや無限大は指定できません")
            return None
        if start < 0 or end <= start or end > duration:
            self.status_var.set(f"範囲は0以上、開始<終了、終了<={duration:.3f}秒で指定してください")
            return None
        return start, end

    def _update_range_text(self) -> None:
        try:
            self.range_var.set(f"開始 {float(self.start_var.get()):.2f}秒 / 終了 {float(self.end_var.get()):.2f}秒")
        except (TypeError, ValueError):
            self.range_var.set("範囲を確認してください")

    def _add_range(self) -> None:
        parsed = self._parse_range()
        if parsed is None or self.current is None:
            return
        start, end = parsed
        self.on_add(str(self.current.material_id), start, end)

    def _timeline_bounds(self) -> tuple[float, float]:
        width = max(80, self.timeline.winfo_width())
        return 20.0, max(21.0, width - 20.0)

    def _x_for_ms(self, value_ms: int) -> float:
        left, right = self._timeline_bounds()
        if self._duration_ms <= 0:
            return left
        return left + (right - left) * max(0, min(value_ms, self._duration_ms)) / self._duration_ms

    def _ms_for_x(self, x: float) -> int:
        left, right = self._timeline_bounds()
        ratio = max(0.0, min(1.0, (x - left) / (right - left)))
        return round(ratio * self._duration_ms)

    def _draw_timeline(self) -> None:
        if not hasattr(self, "timeline"):
            return
        self.timeline.delete("all")
        left, right = self._timeline_bounds()
        y = 29
        self.timeline.create_line(left, y, right, y, fill="#aab4c3", width=5)
        try:
            start_ms = round(float(self.start_var.get()) * 1000)
            end_ms = round(float(self.end_var.get()) * 1000)
        except (TypeError, ValueError):
            start_ms, end_ms = 0, self._duration_ms
        self.timeline.create_line(self._x_for_ms(start_ms), y, self._x_for_ms(end_ms), y,
                                  fill="#4ea1ff", width=8)
        for name, x, color in (("start", self._x_for_ms(start_ms), "#54b7ff"),
                               ("end", self._x_for_ms(end_ms), "#ff8c69")):
            self.timeline.create_oval(x - 8, y - 8, x + 8, y + 8,
                                      fill=color, outline="white", tags=name)
        self._update_range_text()

    def _on_timeline_configure(self, event: Any) -> None:
        self._canvas_width = max(80, int(event.width))
        self._draw_timeline()

    def _timeline_press(self, event: Any) -> None:
        try:
            start_x = self._x_for_ms(round(float(self.start_var.get()) * 1000))
            end_x = self._x_for_ms(round(float(self.end_var.get()) * 1000))
        except (TypeError, ValueError):
            return
        self._drag_handle = "start" if abs(event.x - start_x) <= abs(event.x - end_x) else "end"
        self._timeline_drag(event)

    def _timeline_drag(self, event: Any) -> None:
        if self._drag_handle is None or self._duration_ms <= 0:
            return
        value_ms = self._ms_for_x(event.x)
        try:
            start_ms = round(float(self.start_var.get()) * 1000)
            end_ms = round(float(self.end_var.get()) * 1000)
        except (TypeError, ValueError):
            return
        if self._drag_handle == "start":
            start_ms = min(value_ms, max(0, end_ms - 1))
        else:
            end_ms = max(value_ms, min(self._duration_ms, start_ms + 1))
        self.start_var.set(f"{start_ms / 1000:.2f}")
        self.end_var.set(f"{end_ms / 1000:.2f}")
        self._draw_timeline()

    def _timeline_release(self, _event: Any) -> None:
        self._drag_handle = None
        parsed = self._parse_range()
        if parsed is not None:
            self.player.seek(round(parsed[0] * 1000))

    def _relocate(self) -> None:
        if self.current is None or self.on_relocate is None:
            return
        chosen = filedialog.askopenfilename(
            title="元動画の場所を指定",
            filetypes=[("動画", "*.mp4 *.mov *.avi *.mkv *.m4v *.wmv"), ("すべて", "*.*")],
        )
        if chosen:
            self.on_relocate(str(self.current.material_id), str(Path(chosen).resolve()))

    def _reanalyze(self) -> None:
        if self.current is not None and self.on_reanalyze is not None:
            self.on_reanalyze(str(self.current.material_id))

    @property
    def player(self) -> Any:
        return self._player

    @player.setter
    def player(self, value: Any) -> None:
        old = getattr(self, "_player", None)
        if old is not None and old is not value:
            try:
                old.close()
            except Exception:
                pass
        self._player = value

    def close(self) -> None:
        if not self._destroying:
            self.player.close()

    def destroy(self) -> None:
        if self._destroying:
            return
        self._destroying = True
        try:
            self.player.close()
            for attr in ("start_var", "end_var", "status_var", "range_var"):
                if hasattr(self, attr):
                    setattr(self, attr, None)
        finally:
            super().destroy()


__all__ = ["SourcePanel"]
