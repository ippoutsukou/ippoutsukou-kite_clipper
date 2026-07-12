from __future__ import annotations

import base64
import csv
import json
import os
import re
import shutil
import subprocess
import sys
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
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}

# 正方形ボックスに収めることで、縦動画のフレームも横動画のフレームも
# アスペクト比を保ったまま同じグリッドに並べられます。
SIZE_OPTIONS = {"小": 150, "中": 220, "大": 300, "特大": 420}
DEFAULT_SIZE = "中"
CACHE_BOX = 480
GAP = 10
LABEL_H = 22

SELECT_COLOR = "#2e8b57"
NORMAL_COLOR = "#c8c8c8"
FOCUS_COLOR = "#1976d2"
NG_COLOR = "#d32f2f"
AUDIO_COLOR = "#f57c00"
PLACEHOLDER_FILL = "#e6e6e6"

# AIスコアバッジの色分け閾値（0〜100）
AI_SCORE_HIGH_COLOR = "#2e7d32"
AI_SCORE_MID_COLOR = "#ef6c00"
AI_SCORE_LOW_COLOR = "#757575"
AI_SCORE_HIGH_THRESHOLD = 70
AI_SCORE_MID_THRESHOLD = 40

# AI区間提案(logs/ai_segments.csv)のバンド表示。同一動画内で隣り合う区間を
# 見分けられるよう、区間インデックス(0始まり)の偶奇で2色を交互に使います。
# 既存のバッジ色(✔=SELECT_COLOR、NG=NG_COLOR、♪=AUDIO_COLOR、AI=AI_SCORE_*)と
# 衝突しない色を選んでいます。
SEGMENT_BAND_COLOR_EVEN = "#3949ab"
SEGMENT_BAND_COLOR_ODD = "#00897b"
SEGMENT_BAND_HEIGHT = 5
# バンドがある場合、✔/NGバッジをこの分だけ下にずらして重なりを避けます。
SEGMENT_BADGE_TOP_OFFSET = SEGMENT_BAND_HEIGHT + 3
# フレーム時刻が区間に属するとみなす前後の許容秒数。
SEGMENT_MATCH_TOLERANCE = 0.25

# 「AI区間:」ドロップダウンの選択肢。
SEGMENT_FILTER_ALL = "すべて"
SEGMENT_FILTER_ONLY = "提案区間のみ"
SEGMENT_FILTER_EXCLUDE = "提案区間以外"
SEGMENT_FILTER_VALUES = [SEGMENT_FILTER_ALL, SEGMENT_FILTER_ONLY, SEGMENT_FILTER_EXCLUDE]

# 音量ピーク判定: 1秒ごとのRMS音量(dB)が「動画全体の中央値+6dB」を超え、
# かつ無音に近くない(-45dB以上)秒をピークとみなします。風切り音のような
# 定常ノイズは中央値側に入るため、歓声や声のスパイクだけが残ります。
AUDIO_PEAK_OVER_MEDIAN_DB = 6.0
AUDIO_PEAK_FLOOR_DB = -45.0
AUDIO_SAMPLE_RATE = 8000

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# kite_clipper_gui.py の _accent_button と同じ配色（青のアクセントボタン）。
# このファイルは kite_clipper_gui.py に依存しない単独のTopLevelウィンドウ
# のため、色定義とヘルパー関数をここに複製しています。
ACCENT_BG = "#2a78d6"
ACCENT_FG = "white"
ACCENT_ACTIVE_BG = "#1d5aa8"
ACCENT_ACTIVE_FG = "white"


def _accent_button(parent: tk.Widget, text: str, command) -> tk.Button:
    """kite_clipper_gui.py の _accent_button と同じ配色の tk.Button を生成します。"""
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=ACCENT_BG,
        fg=ACCENT_FG,
        activebackground=ACCENT_ACTIVE_BG,
        activeforeground=ACCENT_ACTIVE_FG,
        relief=tk.RAISED,
        padx=14,
        pady=3,
        font=("", 9, "bold"),
    )


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


def load_ai_scores(csv_path: Path) -> dict[str, float]:
    """frame_scorer.py predict が出力する logs/ai_scores.csv を読み込みます。

    ファイルが無い場合や壊れている場合は空辞書を返します（＝バッジ表示なし、
    従来どおりの挙動）。
    """
    if not csv_path.exists():
        return {}
    scores: dict[str, float] = {}
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                name = row.get("frame_name") or ""
                if not name:
                    continue
                try:
                    scores[name] = float(row.get("ai_score") or "")
                except (TypeError, ValueError):
                    continue
    except Exception:
        return {}
    return scores


def load_ai_segments(csv_path: Path) -> dict[str, list[tuple[float, float]]]:
    """frame_scorer.py predict が出力する logs/ai_segments.csv を読み込みます。

    ファイルが無い場合や壊れている場合は空辞書を返します（＝区間バンド表示なし、
    従来どおりの挙動）。戻り値は video_stem -> [(start_sec, end_sec), ...]
    (start_sec昇順)です。
    """
    if not csv_path.exists():
        return {}
    segments: dict[str, list[tuple[float, float]]] = {}
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                stem = row.get("video_stem") or ""
                if not stem:
                    continue
                try:
                    start_sec = float(row.get("start_sec") or "")
                    end_sec = float(row.get("end_sec") or "")
                except (TypeError, ValueError):
                    continue
                segments.setdefault(stem, []).append((start_sec, end_sec))
    except Exception:
        return {}
    for stem in segments:
        segments[stem].sort(key=lambda item: item[0])
    return segments


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


def safe_stem(path: Path) -> str:
    # kite_clipper.py の safe_stem と同じ変換。フレーム名の video_stem と
    # input/videos の動画を対応付けるために使います。
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in path.stem)


def bundled_tool_path(root: Path, tool_name: str) -> str:
    bundled = root / "vendor" / "ffmpeg" / "windows-x64" / f"{tool_name}.exe"
    if bundled.exists():
        return str(bundled)
    return tool_name


def compute_audio_peaks(video_path: Path, ffmpeg: str) -> set[int]:
    """動画の音声から音量ピークの秒(0始まり)を返します。音声なしなら空。"""
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-f",
        "s16le",
        "-",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=900, creationflags=CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0 or not result.stdout:
        return set()
    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    seconds = len(samples) // AUDIO_SAMPLE_RATE
    if seconds == 0:
        return set()
    windows = samples[: seconds * AUDIO_SAMPLE_RATE].reshape(seconds, AUDIO_SAMPLE_RATE)
    rms = np.sqrt(np.mean(windows * windows, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    threshold = max(float(np.median(db)) + AUDIO_PEAK_OVER_MEDIAN_DB, AUDIO_PEAK_FLOOR_DB)
    return {int(index) for index in np.nonzero(db >= threshold)[0]}


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
        self.ai_scores = load_ai_scores(root / "logs" / "ai_scores.csv")
        self.ai_segments = load_ai_segments(root / "logs" / "ai_segments.csv")
        self.all_cells: list[FrameCell] = []
        self.cells: list[FrameCell] = []
        self.selected_names: set[str] = set()
        self.columns = 1
        self.load_gen = 0
        self.result_queue: Queue[tuple[int, int, str | None]] = Queue()
        self.focus_index: int | None = None
        self.focus_rect_id = 0
        self.anchor_index: int | None = None
        self._relayout_pending = False
        self.audio_peaks: dict[str, set[int]] = {}
        self.audio_gen = 0
        self.audio_queue: Queue[tuple[int, str, set[int]]] = Queue()
        self.scoring_running = False
        self.scoring_queue: Queue[tuple[int, str, str]] = Queue()

        self.filter_var = tk.StringVar(value="すべて")
        self.size_var = tk.StringVar(value=DEFAULT_SIZE)
        self.hide_ng_var = tk.BooleanVar(value=False)
        self.segment_filter_var = tk.StringVar(value=SEGMENT_FILTER_ALL)
        self.stats_var = tk.StringVar(value="")

        self._build_widgets()
        self.reload_from_disk()
        self.after(80, self._poll_results)
        # 初回ロード直後だけ、AI提案区間の一括採用を確認します（手動採用が既にある
        # 場合は何も聞きません）。ウィンドウの初回描画が終わってから出すため
        # after_idle を使います。
        self.after_idle(self._maybe_offer_auto_adopt)

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
        ttk.Label(bar, text="AI区間:").pack(side=tk.LEFT)
        self.segment_filter_box = ttk.Combobox(
            bar,
            textvariable=self.segment_filter_var,
            state="readonly",
            values=SEGMENT_FILTER_VALUES,
            width=10,
        )
        self.segment_filter_box.pack(side=tk.LEFT, padx=(4, 12))
        self.segment_filter_box.bind("<<ComboboxSelected>>", lambda _e: self.rebuild())
        self.adopt_segments_button = ttk.Button(
            bar, text="AI提案を採用", command=self.adopt_ai_segments
        )
        self.adopt_segments_button.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(bar, text="再読み込み", command=self.reload_from_disk).pack(
            side=tk.LEFT, padx=(0, 12)
        )
        self.run_scoring_button = ttk.Button(
            bar, text="AI採点を実行", command=self.run_ai_scoring
        )
        self.run_scoring_button.pack(side=tk.LEFT)

        info = ttk.Frame(self, padding=(10, 0, 10, 4))
        info.pack(fill=tk.X)
        ttk.Label(info, textvariable=self.stats_var, font=("", 11, "bold")).pack(side=tk.LEFT)
        ttk.Label(
            info,
            text=(
                "クリック=採用/解除  矢印=移動  Space=採用/解除  "
                "Shift+Space / Shift+クリック=そこまで範囲適用  右クリック=元画像  ♪=音量ピーク"
            ),
            foreground="#666666",
        ).pack(side=tk.RIGHT)

        # 下部固定フッター: 選別を終える導線。body（キャンバス）より先にpackする
        # ことで下端に固定し、bodyが残りの領域を占めるようにします。
        footer = ttk.Frame(self, padding=(10, 6))
        ttk.Label(
            footer,
            text="✓ 選択は自動で保存されます。選び終えたら「選別を終える」を押すか、そのままウィンドウを閉じてください。",
            foreground="#666666",
        ).pack(side=tk.LEFT)
        _accent_button(footer, "選別を終える（閉じる）", self.destroy).pack(side=tk.RIGHT)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(side=tk.BOTTOM, fill=tk.X)

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
        # キー操作はキャンバスにフォーカスがあるときだけ効かせます。ウィンドウ
        # 全体にバインドすると、コンボボックスやボタンにフォーカスがある状態で
        # Spaceや矢印がグリッド操作と二重に効いてしまうためです。
        self.canvas.configure(takefocus=1)
        self.canvas.bind("<Left>", lambda _e: self.move_focus(-1))
        self.canvas.bind("<Right>", lambda _e: self.move_focus(1))
        self.canvas.bind("<Up>", lambda _e: self.move_focus(-self.columns))
        self.canvas.bind("<Down>", lambda _e: self.move_focus(self.columns))
        self.canvas.bind("<space>", lambda _e: self.toggle_focus())
        self.canvas.bind("<Shift-space>", lambda _e: self.range_toggle_to_focus())
        self.canvas.bind("<Prior>", lambda _e: self.canvas.yview_scroll(-20, "units"))
        self.canvas.bind("<Next>", lambda _e: self.canvas.yview_scroll(20, "units"))
        self.canvas.focus_set()

    # ------------------------------------------------------------ データ読込

    def reload_from_disk(self) -> None:
        self.ng_info = load_ng_info(self.root_dir / "logs" / "result.csv")
        self.ai_scores = load_ai_scores(self.root_dir / "logs" / "ai_scores.csv")
        self.ai_segments = load_ai_segments(self.root_dir / "logs" / "ai_segments.csv")
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
        has_segments = any(self.ai_segments.values())
        self.segment_filter_box.configure(state="readonly" if has_segments else tk.DISABLED)
        if not has_segments:
            self.segment_filter_var.set(SEGMENT_FILTER_ALL)
        self.adopt_segments_button.configure(state=tk.NORMAL if has_segments else tk.DISABLED)
        self.audio_peaks = {}
        self.start_audio_analysis()
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
        segment_filter = self.segment_filter_var.get()
        cells = [
            cell
            for cell in self.all_cells
            if (target == "すべて" or cell.video_stem == target)
            and not (self.hide_ng_var.get() and cell.is_ng)
            and self._matches_segment_filter(cell, segment_filter)
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
            segment_index = self.segment_index_for(cell)
            if segment_index is not None:
                band_color = (
                    SEGMENT_BAND_COLOR_EVEN if segment_index % 2 == 0 else SEGMENT_BAND_COLOR_ODD
                )
                self.canvas.create_rectangle(
                    x,
                    y,
                    x + box,
                    y + SEGMENT_BAND_HEIGHT,
                    fill=band_color,
                    outline="",
                )
            top_offset = SEGMENT_BADGE_TOP_OFFSET if segment_index is not None else 0
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
                cell.check_id = self._create_check(x, y, top_offset)
            if cell.is_ng:
                self.canvas.create_text(
                    x + box - 6,
                    y + 6 + top_offset,
                    text="NG",
                    anchor=tk.NE,
                    font=("", 10, "bold"),
                    fill=NG_COLOR,
                )
            if self.is_audio_peak(cell):
                self.canvas.create_text(
                    x + box - 6,
                    y + box - 4,
                    text="♪",
                    anchor=tk.SE,
                    font=("", 13, "bold"),
                    fill=AUDIO_COLOR,
                )
            ai_score = self.ai_scores.get(cell.path.name)
            if ai_score is not None:
                self.canvas.create_text(
                    x + 6,
                    y + box - 4,
                    text=f"AI {round(ai_score * 100):.0f}",
                    anchor=tk.SW,
                    font=("", 10, "bold"),
                    fill=self._ai_score_color(ai_score),
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

    def _create_check(self, x: int, y: int, top_offset: int = 0) -> int:
        return self.canvas.create_text(
            x + 8,
            y + 4 + top_offset,
            text="✔",
            anchor=tk.NW,
            font=("", 13, "bold"),
            fill=SELECT_COLOR,
        )

    def segment_index_for(self, cell: FrameCell) -> int | None:
        """cell が logs/ai_segments.csv の提案区間に属していれば、その動画内での
        区間インデックス(0始まり)を返します。属していなければ None です。
        時刻の前後 SEGMENT_MATCH_TOLERANCE 秒までは同じ区間とみなします。
        """
        segments = self.ai_segments.get(cell.video_stem)
        if not segments or cell.time_sec < 0:
            return None
        for index, (start_sec, end_sec) in enumerate(segments):
            if start_sec - SEGMENT_MATCH_TOLERANCE <= cell.time_sec <= end_sec + SEGMENT_MATCH_TOLERANCE:
                return index
        return None

    def _matches_segment_filter(self, cell: FrameCell, filter_value: str) -> bool:
        """「AI区間:」ドロップダウンの選択肢に応じて cell を表示するかどうかを返します。"""
        if filter_value == SEGMENT_FILTER_ONLY:
            return self.segment_index_for(cell) is not None
        if filter_value == SEGMENT_FILTER_EXCLUDE:
            return self.segment_index_for(cell) is None
        return True

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
        audio_updated = False
        try:
            while True:
                gen, stem, peaks = self.audio_queue.get_nowait()
                if gen != self.audio_gen:
                    continue
                self.audio_peaks[stem] = peaks
                if peaks:
                    audio_updated = True
        except Empty:
            pass
        if audio_updated:
            self.redraw_all()
        try:
            while True:
                returncode, stdout, stderr = self.scoring_queue.get_nowait()
                self._handle_scoring_result(returncode, stdout, stderr)
        except Empty:
            pass
        self.after(80, self._poll_results)

    # -------------------------------------------------------- 音量ピーク解析

    def _ai_score_color(self, ai_score: float) -> str:
        score_100 = ai_score * 100
        if score_100 >= AI_SCORE_HIGH_THRESHOLD:
            return AI_SCORE_HIGH_COLOR
        if score_100 >= AI_SCORE_MID_THRESHOLD:
            return AI_SCORE_MID_COLOR
        return AI_SCORE_LOW_COLOR

    def is_audio_peak(self, cell: FrameCell) -> bool:
        peaks = self.audio_peaks.get(cell.video_stem)
        if not peaks or cell.time_sec < 0:
            return False
        second = int(cell.time_sec)
        # 歓声とフレーム時刻のずれを吸収するため前後1秒まで許容します。
        return second in peaks or (second - 1) in peaks or (second + 1) in peaks

    def start_audio_analysis(self) -> None:
        self.audio_gen += 1
        gen = self.audio_gen
        videos_dir = self.root_dir / "input" / "videos"
        videos: dict[str, Path] = {}
        if videos_dir.exists():
            for path in sorted(videos_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                    videos[safe_stem(path)] = path
        stems = sorted({cell.video_stem for cell in self.all_cells})
        tasks = [(stem, videos[stem]) for stem in stems if stem in videos]
        if not tasks:
            return
        threading.Thread(target=self._audio_worker, args=(gen, tasks), daemon=True).start()

    def _audio_worker(self, gen: int, tasks: list[tuple[str, Path]]) -> None:
        ffmpeg = bundled_tool_path(self.root_dir, "ffmpeg")
        for stem, video_path in tasks:
            if gen != self.audio_gen:
                return
            self.audio_queue.put((gen, stem, self._load_or_compute_peaks(video_path, ffmpeg)))

    def _load_or_compute_peaks(self, video_path: Path, ffmpeg: str) -> set[int]:
        cache_dir = self.root_dir / "work" / "audio_peaks"
        try:
            cache_file = cache_dir / f"{safe_stem(video_path)}_{int(video_path.stat().st_mtime)}.json"
        except OSError:
            return set()
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return {int(value) for value in data}
            except Exception:
                pass
        peaks = compute_audio_peaks(video_path, ffmpeg)
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(sorted(peaks)), encoding="utf-8")
        except OSError:
            pass
        return peaks

    def _place_image(self, index: int) -> None:
        cell = self.cells[index]
        if not cell.rect_id or cell.thumb is None:
            return
        x, y = self.cell_geometry(index)
        box = self.box
        cell.img_id = self.canvas.create_image(x + box // 2, y + box // 2, image=cell.thumb)
        self.canvas.tag_lower(cell.img_id, cell.rect_id)
        self.canvas.itemconfigure(cell.rect_id, fill="")

    # -------------------------------------------------------------- AI採点の実行

    def run_ai_scoring(self) -> None:
        """「AI採点を実行」ボタンの処理です。frame_scorer.py predict をバック
        グラウンドのサブプロセスで実行し、完了後に reload_from_disk() で結果を
        反映します。UIはブロックしません。
        """
        if self.scoring_running:
            return
        if not messagebox.askyesno(
            "AI採点を実行",
            "AI採点(区間提案つき)を実行します。フレーム数によって数十秒かかることがあります。よろしいですか?",
            parent=self,
        ):
            return
        self.scoring_running = True
        self.run_scoring_button.configure(state=tk.DISABLED, text="AI採点 実行中...")
        threading.Thread(target=self._ai_scoring_worker, daemon=True).start()

    def _ai_scoring_worker(self) -> None:
        script_path = self.root_dir / "frame_scorer.py"
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            result = subprocess.run(
                [sys.executable, str(script_path), "predict"],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
            self.scoring_queue.put((result.returncode, result.stdout or "", result.stderr or ""))
        except (OSError, subprocess.SubprocessError) as exc:
            self.scoring_queue.put((-1, "", str(exc)))

    def _handle_scoring_result(self, returncode: int, stdout: str, stderr: str) -> None:
        self.scoring_running = False
        if not self.winfo_exists():
            return
        self.run_scoring_button.configure(state=tk.NORMAL, text="AI採点を実行")
        if returncode == 0:
            self.reload_from_disk()
            segment_count = sum(len(segments) for segments in self.ai_segments.values())
            messagebox.showinfo(
                "AI採点を実行",
                f"AI採点が完了しました。(提案区間 {segment_count} 件)",
                parent=self,
            )
        else:
            detail = self._tail_lines(stderr) or self._tail_lines(stdout) or "(詳細不明)"
            messagebox.showerror(
                "AI採点を実行",
                f"AI採点に失敗しました(終了コード {returncode})。\n\n{detail}",
                parent=self,
            )

    @staticmethod
    def _tail_lines(text: str, count: int = 8) -> str:
        lines = [line for line in text.splitlines() if line.strip()]
        return "\n".join(lines[-count:])

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

    def anchor_selected_state(self) -> bool | None:
        """アンカーセルの「現在の」採用状態。範囲適用はこの状態に揃えます。"""
        if self.anchor_index is None or self.anchor_index >= len(self.cells):
            return None
        return self.cells[self.anchor_index].path.name in self.selected_names

    def apply_range(self, end_index: int) -> None:
        state = self.anchor_selected_state()
        if state is None:
            return
        low, high = sorted((self.anchor_index, end_index))
        for position in range(low, high + 1):
            self.set_selected(position, state)
        self.focus_index = end_index
        self.update_focus_rect()
        self.after_toggle()

    def on_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        index = self.index_at(event)
        if index is None:
            return
        target = self.cells[index].path.name not in self.selected_names
        self.set_selected(index, target)
        self.anchor_index = index
        self.focus_index = index
        self.update_focus_rect()
        self.after_toggle()

    def on_shift_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        index = self.index_at(event)
        if index is None:
            return
        if self.anchor_index is None:
            self.on_click(event)
            return
        self.apply_range(index)

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
        self.after_toggle()

    def range_toggle_to_focus(self) -> None:
        if self.focus_index is None or self.focus_index >= len(self.cells):
            return
        if self.anchor_index is None:
            # アンカーがまだ無ければ、Spaceと同じ動きで起点を作ります。
            self.toggle_focus()
            return
        self.apply_range(self.focus_index)

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

    def _set_frame_selected_state(self, cell: FrameCell, target: bool) -> bool:
        """cell の採用状態を実際に変更します(selected_frames へのコピー/削除、
        selected_namesの更新)。キャンバス描画は行いません（呼び出し側の責務）。

        状態が変わった場合はTrue、既に同じ状態だった場合やエラー時はFalseを返します。
        """
        name = cell.path.name
        already = name in self.selected_names
        if target == already:
            return False
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
            return False
        return True

    def set_selected(self, index: int, target: bool) -> None:
        cell = self.cells[index]
        changed = self._set_frame_selected_state(cell, target)
        if not changed:
            return
        if cell.rect_id:
            self.canvas.itemconfigure(
                cell.rect_id,
                outline=SELECT_COLOR if target else NORMAL_COLOR,
                width=4 if target else 1,
            )
        if target and not cell.check_id:
            x, y = self.cell_geometry(index)
            segment_index = self.segment_index_for(cell)
            top_offset = SEGMENT_BADGE_TOP_OFFSET if segment_index is not None else 0
            cell.check_id = self._create_check(x, y, top_offset)
        elif not target and cell.check_id:
            self.canvas.delete(cell.check_id)
            cell.check_id = 0

    def after_toggle(self) -> None:
        self.update_stats()
        try:
            self.on_selection_change()
        except Exception:
            pass

    # -------------------------------------------------------- AI提案区間の採用

    def _collect_unselected_segment_cells(self) -> list[FrameCell]:
        """all_cells の中で、提案区間に属していてまだ未採用のセルを返します
        (現在の絞り込み表示に関係なく、全フレームが対象です)。
        """
        return [
            cell
            for cell in self.all_cells
            if cell.path.name not in self.selected_names and self.segment_index_for(cell) is not None
        ]

    def _adopt_cells(self, cells: list[FrameCell]) -> None:
        """指定セルをまとめて採用します(selected_frames へコピー)。
        set_selected と同じ採用処理(_set_frame_selected_state)を再利用し、
        完了後にビューア全体を再構築して統計表示も更新します。
        """
        changed = False
        for cell in cells:
            if self._set_frame_selected_state(cell, True):
                changed = True
        if not changed:
            return
        self.rebuild()
        try:
            self.on_selection_change()
        except Exception:
            pass

    def adopt_ai_segments(self) -> None:
        """「AI提案を採用」ボタンの処理です。提案区間に属する未採用フレームを
        確認の上、一括採用します。
        """
        cells = self._collect_unselected_segment_cells()
        if not cells:
            messagebox.showinfo(
                "AI提案を採用", "採用できるAI提案区間のフレームがありません。", parent=self
            )
            return
        if not messagebox.askyesno(
            "AI提案を採用",
            f"AI提案区間の {len(cells)} フレームを採用します。よろしいですか?",
            parent=self,
        ):
            return
        self._adopt_cells(cells)

    def _maybe_offer_auto_adopt(self) -> None:
        """ビューアを開いた直後、AI提案区間があり、かつ現在の採用が0件のときだけ、
        一括採用してよいか自動で確認します。既に手動採用がある場合は何も聞きません
        （ユーザーの選択を壊さないため）。
        """
        if not self.winfo_exists() or self.selected_names:
            return
        cells = self._collect_unselected_segment_cells()
        if not cells:
            return
        if messagebox.askyesno(
            "AI提案区間を採用",
            f"AI提案区間の {len(cells)} フレームを採用します。よろしいですか?",
            parent=self,
        ):
            self._adopt_cells(cells)

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
