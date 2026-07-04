from __future__ import annotations

import base64
import csv
import os
import re
import shutil
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from tkinter import messagebox, ttk
from typing import Callable

import cv2
import numpy as np

# kite_clipper.py のフレーム命名規則と同じもの。GUIはCLIをsubprocess経由で使う
# 構成のため、CLIモジュールをimportせずここに複製しています。
FRAME_NAME_PATTERN = re.compile(r"^(?P<video_stem>.+)__t(?P<time>\d+(?:\.\d+)?)$")
LEGACY_FRAME_NAME_PATTERN = re.compile(r"^(?P<video_stem>.+)_(?P<number>\d{6})$")
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

# 正方形ボックスに収めることで、縦動画のフレームも横動画のフレームも
# アスペクト比を保ったまま同じグリッドに並べられます。
SIZE_OPTIONS = {"小": 150, "中": 220, "大": 300}
DEFAULT_SIZE = "中"
CACHE_BOX = 480
GAP = 10
LABEL_H = 22

SELECT_COLOR = "#2e8b57"
NORMAL_COLOR = "#c8c8c8"
FOCUS_COLOR = "#1976d2"
NG_COLOR = "#d32f2f"
PLACEHOLDER_FILL = "#e6e6e6"


@dataclass
class FrameCell:
    path: Path
    video_stem: str
    time_sec: float
    is_ng: bool = False
    ng_reason: str = ""
    thumb: tk.PhotoImage | None = field(default=None, repr=False)
    rect_id: int = 0
    img_id: int = 0
    check_id: int = 0


def parse_frame_name(path: Path) -> tuple[str, float]:
    match = FRAME_NAME_PATTERN.match(path.stem)
    if match:
        return match.group("video_stem"), float(match.group("time"))
    legacy = LEGACY_FRAME_NAME_PATTERN.match(path.stem)
    if legacy:
        return legacy.group("video_stem"), max(0.0, float(int(legacy.group("number")) - 1))
    raise ValueError(path.name)


def load_ng_info(csv_path: Path) -> dict[str, tuple[bool, str]]:
    if not csv_path.exists():
        return {}
    info: dict[str, tuple[bool, str]] = {}
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                if row.get("type") != "frame":
                    continue
                name = Path(row.get("output_path") or "").name
                if name:
                    info[name] = (row.get("is_ng") == "True", row.get("ng_reason") or "")
    except Exception:
        return {}
    return info


def fit_within(image: np.ndarray, box: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(box / width, box / height, 1.0)
    if scale >= 1.0:
        return image
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def read_image(path: Path) -> np.ndarray | None:
    # 日本語などのパスでも読めるように np.fromfile 経由でデコードします。
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "00:00.00"
    minutes = int(seconds // 60)
    remain = seconds - minutes * 60
    return f"{minutes:02d}:{remain:05.2f}"


class FrameSelectorWindow(tk.Toplevel):
    """review/frames のサムネイル一覧から selected_frames への採用を行うビューア。

    採用 = selected_frames へコピー。解除 = selected_frames 内のコピーを削除。
    review/frames の元画像は一切変更しません。
    """

    def __init__(
        self,
        master: tk.Misc,
        root: Path,
        get_cut_params: Callable[[], tuple[float, float, float]],
        on_selection_change: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.title("フレーム選別 (review/frames → selected_frames)")
        self.geometry("1200x800")
        self.minsize(720, 480)

        self.root_dir = root
        self.review_dir = root / "review" / "frames"
        self.selected_dir = root / "selected_frames"
        self.cache_dir = root / "work" / "frame_thumbs"
        self.get_cut_params = get_cut_params
        self.on_selection_change = on_selection_change

        self.ng_info = load_ng_info(root / "logs" / "result.csv")
        self.all_cells: list[FrameCell] = []
        self.cells: list[FrameCell] = []
        self.selected_names: set[str] = set()
        self.columns = 1
        self.load_gen = 0
        self.result_queue: Queue[tuple[int, int, str | None]] = Queue()
        self.focus_index: int | None = None
        self.focus_rect_id = 0
        self.anchor_index: int | None = None
        self.anchor_state = True
        self._relayout_pending = False

        self.filter_var = tk.StringVar(value="すべて")
        self.size_var = tk.StringVar(value=DEFAULT_SIZE)
        self.hide_ng_var = tk.BooleanVar(value=False)
        self.stats_var = tk.StringVar(value="")

        self._build_widgets()
        self.reload_from_disk()
        self.after(80, self._poll_results)

    # ---------------------------------------------------------------- UI構築

    def _build_widgets(self) -> None:
        bar = ttk.Frame(self, padding=(10, 8, 10, 4))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="動画").pack(side=tk.LEFT)
        self.filter_box = ttk.Combobox(
            bar, textvariable=self.filter_var, state="readonly", width=32
        )
        self.filter_box.pack(side=tk.LEFT, padx=(4, 12))
        self.filter_box.bind("<<ComboboxSelected>>", lambda _e: self.rebuild())
        ttk.Label(bar, text="サムネイル").pack(side=tk.LEFT)
        size_box = ttk.Combobox(
            bar,
            textvariable=self.size_var,
            state="readonly",
            values=list(SIZE_OPTIONS),
            width=4,
        )
        size_box.pack(side=tk.LEFT, padx=(4, 12))
        size_box.bind("<<ComboboxSelected>>", lambda _e: self.on_size_change())
        self.hide_ng_check = ttk.Checkbutton(
            bar, text="NG候補を隠す", variable=self.hide_ng_var, command=self.rebuild
        )
        self.hide_ng_check.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(bar, text="再読み込み", command=self.reload_from_disk).pack(side=tk.LEFT)

        info = ttk.Frame(self, padding=(10, 0, 10, 4))
        info.pack(fill=tk.X)
        ttk.Label(info, textvariable=self.stats_var, font=("", 11, "bold")).pack(side=tk.LEFT)
        ttk.Label(
            info,
            text="クリック=採用/解除  Shift+クリック=範囲  矢印=移動  Space=採用/解除  右クリック=元画像を開く",
            foreground="#666666",
        ).pack(side=tk.RIGHT)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(body)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas = tk.Canvas(
            body, background="#f5f5f5", highlightthickness=0, yscrollincrement=20
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Shift-Button-1>", self.on_shift_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.bind("<MouseWheel>", self.on_mousewheel)
        self.bind("<Left>", lambda _e: self.move_focus(-1))
        self.bind("<Right>", lambda _e: self.move_focus(1))
        self.bind("<Up>", lambda _e: self.move_focus(-self.columns))
        self.bind("<Down>", lambda _e: self.move_focus(self.columns))
        self.bind("<space>", lambda _e: self.toggle_focus())
        self.bind("<Shift-space>", lambda _e: self.range_toggle_to_focus())
        self.bind("<Prior>", lambda _e: self.canvas.yview_scroll(-20, "units"))
        self.bind("<Next>", lambda _e: self.canvas.yview_scroll(20, "units"))

    # ------------------------------------------------------------ データ読込

    def reload_from_disk(self) -> None:
        self.ng_info = load_ng_info(self.root_dir / "logs" / "result.csv")
        self.rescan_selected()
        cells: list[FrameCell] = []
        if self.review_dir.exists():
            for path in sorted(self.review_dir.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS:
                    continue
                try:
                    stem, time_sec = parse_frame_name(path)
                except ValueError:
                    stem, time_sec = path.stem, -1.0
                is_ng, reason = self.ng_info.get(path.name, (False, ""))
                cells.append(
                    FrameCell(
                        path=path,
                        video_stem=stem,
                        time_sec=time_sec,
                        is_ng=is_ng,
                        ng_reason=reason,
                    )
                )
        cells.sort(key=lambda c: (c.video_stem, c.time_sec, c.path.name))
        self.all_cells = cells

        stems = sorted({cell.video_stem for cell in cells})
        values = ["すべて", *stems]
        self.filter_box.configure(values=values)
        if self.filter_var.get() not in values:
            self.filter_var.set("すべて")
        has_ng = any(cell.is_ng for cell in cells)
        self.hide_ng_check.configure(state=tk.NORMAL if has_ng else tk.DISABLED)
        if not has_ng:
            self.hide_ng_var.set(False)
        self.rebuild()

    def rescan_selected(self) -> None:
        names: set[str] = set()
        if self.selected_dir.exists():
            for path in self.selected_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS:
                    names.add(path.name)
        self.selected_names = names

    def rebuild(self) -> None:
        target = self.filter_var.get()
        cells = [
            cell
            for cell in self.all_cells
            if (target == "すべて" or cell.video_stem == target)
            and not (self.hide_ng_var.get() and cell.is_ng)
        ]
        self.cells = cells
        self.focus_index = 0 if cells else None
        self.anchor_index = None
        self.columns = self.compute_columns()
        self.redraw_all()
        self.update_stats()
        self.start_loading()

    def on_size_change(self) -> None:
        for cell in self.all_cells:
            cell.thumb = None
        self.rebuild()

    # -------------------------------------------------------------- 描画

    @property
    def box(self) -> int:
        return SIZE_OPTIONS.get(self.size_var.get(), SIZE_OPTIONS[DEFAULT_SIZE])

    def compute_columns(self) -> int:
        width = max(self.canvas.winfo_width(), 1)
        return max(1, (width - GAP) // (self.box + GAP))

    def cell_geometry(self, index: int) -> tuple[int, int]:
        row, col = divmod(index, self.columns)
        x = GAP + col * (self.box + GAP)
        y = GAP + row * (self.box + LABEL_H + GAP)
        return x, y

    def redraw_all(self) -> None:
        self.canvas.delete("all")
        self.focus_rect_id = 0
        box = self.box
        show_stem = self.filter_var.get() == "すべて"
        for index, cell in enumerate(self.cells):
            x, y = self.cell_geometry(index)
            cell.img_id = 0
            cell.check_id = 0
            fill = "" if cell.thumb is not None else PLACEHOLDER_FILL
            selected = cell.path.name in self.selected_names
            cell.rect_id = self.canvas.create_rectangle(
                x,
                y,
                x + box,
                y + box,
                fill=fill,
                outline=SELECT_COLOR if selected else NORMAL_COLOR,
                width=4 if selected else 1,
            )
            if cell.thumb is not None:
                cell.img_id = self.canvas.create_image(
                    x + box // 2, y + box // 2, image=cell.thumb
                )
                self.canvas.tag_lower(cell.img_id, cell.rect_id)
            label = f"{cell.time_sec:.1f}s" if cell.time_sec >= 0 else cell.path.stem[:16]
            if show_stem and cell.time_sec >= 0:
                stem = cell.video_stem
                label = f"{stem[:12]}… {label}" if len(stem) > 12 else f"{stem} {label}"
            self.canvas.create_text(
                x + box // 2,
                y + box + 4,
                text=label,
                anchor=tk.N,
                font=("", 9),
                fill="#333333",
            )
            if selected:
                cell.check_id = self._create_check(x, y)
            if cell.is_ng:
                self.canvas.create_text(
                    x + box - 6,
                    y + 6,
                    text="NG",
                    anchor=tk.NE,
                    font=("", 10, "bold"),
                    fill=NG_COLOR,
                )
        rows = (len(self.cells) + self.columns - 1) // self.columns if self.cells else 0
        total_height = GAP + rows * (box + LABEL_H + GAP)
        self.canvas.configure(scrollregion=(0, 0, 1, max(total_height, 1)))
        if not self.cells:
            self.canvas.create_text(
                20,
                20,
                anchor=tk.NW,
                text="review/frames に表示できる画像がありません。\n先に「生成」を実行してください。",
                font=("", 11),
                fill="#666666",
            )
        self.update_focus_rect()

    def _create_check(self, x: int, y: int) -> int:
        return self.canvas.create_text(
            x + 8,
            y + 4,
            text="✔",
            anchor=tk.NW,
            font=("", 13, "bold"),
            fill=SELECT_COLOR,
        )

    def update_focus_rect(self) -> None:
        if self.focus_rect_id:
            self.canvas.delete(self.focus_rect_id)
            self.focus_rect_id = 0
        if self.focus_index is None or self.focus_index >= len(self.cells):
            return
        x, y = self.cell_geometry(self.focus_index)
        box = self.box
        self.focus_rect_id = self.canvas.create_rectangle(
            x - 3,
            y - 3,
            x + box + 3,
            y + box + 3,
            outline=FOCUS_COLOR,
            width=2,
            dash=(4, 2),
        )

    def on_canvas_configure(self, _event: object) -> None:
        if self._relayout_pending:
            return
        self._relayout_pending = True
        self.after_idle(self._relayout_if_needed)

    def _relayout_if_needed(self) -> None:
        self._relayout_pending = False
        columns = self.compute_columns()
        if columns != self.columns:
            self.columns = columns
            self.redraw_all()

    # ------------------------------------------------------ サムネイル読込

    def start_loading(self) -> None:
        self.load_gen += 1
        gen = self.load_gen
        pending = [(index, cell.path) for index, cell in enumerate(self.cells) if cell.thumb is None]
        if not pending:
            return
        threading.Thread(
            target=self._load_worker, args=(gen, pending, self.box), daemon=True
        ).start()

    def _load_worker(self, gen: int, pending: list[tuple[int, Path]], box: int) -> None:
        for index, path in pending:
            if gen != self.load_gen:
                return
            self.result_queue.put((gen, index, self._make_thumb_b64(path, box)))

    def _make_thumb_b64(self, path: Path, box: int) -> str | None:
        try:
            image = None
            cached = self.cache_dir / f"{path.stem}_{int(path.stat().st_mtime)}.png"
            if cached.exists():
                image = read_image(cached)
            if image is None:
                source = read_image(path)
                if source is None:
                    return None
                image = fit_within(source, CACHE_BOX)
                ok, encoded = cv2.imencode(".png", image)
                if ok:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    encoded.tofile(str(cached))
            display = fit_within(image, box)
            ok, encoded = cv2.imencode(".png", display)
            if not ok:
                return None
            return base64.b64encode(encoded.tobytes()).decode("ascii")
        except Exception:
            return None

    def _poll_results(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                gen, index, data = self.result_queue.get_nowait()
                if gen != self.load_gen or data is None or index >= len(self.cells):
                    continue
                cell = self.cells[index]
                try:
                    cell.thumb = tk.PhotoImage(data=data)
                except Exception:
                    continue
                self._place_image(index)
        except Empty:
            pass
        self.after(80, self._poll_results)

    def _place_image(self, index: int) -> None:
        cell = self.cells[index]
        if not cell.rect_id or cell.thumb is None:
            return
        x, y = self.cell_geometry(index)
        box = self.box
        cell.img_id = self.canvas.create_image(x + box // 2, y + box // 2, image=cell.thumb)
        self.canvas.tag_lower(cell.img_id, cell.rect_id)
        self.canvas.itemconfigure(cell.rect_id, fill="")

    # ------------------------------------------------------------ 選択操作

    def index_at(self, event: tk.Event) -> int | None:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        box = self.box
        pitch_x = box + GAP
        pitch_y = box + LABEL_H + GAP
        col = int((x - GAP) // pitch_x)
        row = int((y - GAP) // pitch_y)
        if col < 0 or col >= self.columns or row < 0:
            return None
        if (x - GAP) % pitch_x > box or (y - GAP) % pitch_y > box + LABEL_H:
            return None
        index = row * self.columns + col
        if index >= len(self.cells):
            return None
        return index

    def on_click(self, event: tk.Event) -> None:
        index = self.index_at(event)
        if index is None:
            return
        target = self.cells[index].path.name not in self.selected_names
        self.set_selected(index, target)
        self.anchor_index = index
        self.anchor_state = target
        self.focus_index = index
        self.update_focus_rect()
        self.after_toggle()

    def on_shift_click(self, event: tk.Event) -> None:
        index = self.index_at(event)
        if index is None:
            return
        if self.anchor_index is None:
            self.on_click(event)
            return
        low, high = sorted((self.anchor_index, index))
        for position in range(low, high + 1):
            self.set_selected(position, self.anchor_state)
        self.focus_index = index
        self.update_focus_rect()
        self.after_toggle()

    def on_right_click(self, event: tk.Event) -> None:
        index = self.index_at(event)
        if index is None:
            return
        try:
            os.startfile(self.cells[index].path)
        except OSError as exc:
            messagebox.showerror("画像を開けません", str(exc), parent=self)

    def toggle_focus(self) -> None:
        if self.focus_index is None or self.focus_index >= len(self.cells):
            return
        index = self.focus_index
        target = self.cells[index].path.name not in self.selected_names
        self.set_selected(index, target)
        self.anchor_index = index
        self.anchor_state = target
        self.after_toggle()

    def range_toggle_to_focus(self) -> None:
        if self.focus_index is None or self.anchor_index is None:
            return
        low, high = sorted((self.anchor_index, self.focus_index))
        for position in range(low, high + 1):
            self.set_selected(position, self.anchor_state)
        self.after_toggle()

    def move_focus(self, delta: int) -> None:
        if not self.cells:
            return
        current = self.focus_index if self.focus_index is not None else 0
        self.focus_index = max(0, min(len(self.cells) - 1, current + delta))
        self.update_focus_rect()
        self.scroll_focus_into_view()

    def scroll_focus_into_view(self) -> None:
        if self.focus_index is None:
            return
        _x, y = self.cell_geometry(self.focus_index)
        cell_height = self.box + LABEL_H + GAP
        top = self.canvas.canvasy(0)
        view_height = max(self.canvas.winfo_height(), 1)
        _s0, _s1, _s2, total = (float(v) for v in self.canvas.cget("scrollregion").split())
        total = max(total, 1.0)
        if y - GAP < top:
            self.canvas.yview_moveto(max(0.0, (y - GAP) / total))
        elif y + cell_height > top + view_height:
            self.canvas.yview_moveto(max(0.0, (y + cell_height - view_height) / total))

    def on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120) * 3, "units")

    def set_selected(self, index: int, target: bool) -> None:
        cell = self.cells[index]
        name = cell.path.name
        already = name in self.selected_names
        if target == already:
            return
        try:
            if target:
                self.selected_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cell.path, self.selected_dir / name)
                self.selected_names.add(name)
            else:
                # selected_frames 内のコピーだけを削除します。review/frames の
                # 元画像は変更しません。
                for copy_path in list(self.selected_dir.rglob("*")):
                    if copy_path.is_file() and copy_path.name == name:
                        copy_path.unlink()
                self.selected_names.discard(name)
        except OSError as exc:
            messagebox.showerror("selected_frames を更新できません", str(exc), parent=self)
            return
        if cell.rect_id:
            self.canvas.itemconfigure(
                cell.rect_id,
                outline=SELECT_COLOR if target else NORMAL_COLOR,
                width=4 if target else 1,
            )
        if target and not cell.check_id:
            x, y = self.cell_geometry(index)
            cell.check_id = self._create_check(x, y)
        elif not target and cell.check_id:
            self.canvas.delete(cell.check_id)
            cell.check_id = 0

    def after_toggle(self) -> None:
        self.update_stats()
        try:
            self.on_selection_change()
        except Exception:
            pass

    # ------------------------------------------------------------ 統計表示

    def update_stats(self) -> None:
        split_gap, pre_roll, post_roll = self.get_cut_params()
        times_by_video: dict[str, list[float]] = {}
        parsed = 0
        for name in self.selected_names:
            try:
                stem, time_sec = parse_frame_name(Path(name))
            except ValueError:
                continue
            parsed += 1
            times_by_video.setdefault(stem, []).append(time_sec)
        clip_count = 0
        total = 0.0
        for times in times_by_video.values():
            times.sort()
            group_start = times[0]
            previous = times[0]
            for time_sec in times[1:]:
                if time_sec - previous > split_gap:
                    clip_count += 1
                    total += (previous - group_start) + pre_roll + post_roll
                    group_start = time_sec
                previous = time_sec
            clip_count += 1
            total += (previous - group_start) + pre_roll + post_roll
        self.stats_var.set(
            f"選択 {len(self.selected_names)}枚 / 推定クリップ {clip_count}本 / "
            f"推定合計 {format_duration(total)} (前後余白込み)"
        )
