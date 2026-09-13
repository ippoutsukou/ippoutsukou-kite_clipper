from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import messagebox, ttk
from tkinter import font as tkfont
from typing import Callable

import sport_profile
from frame_scorer_launcher import FRAME_SCORER_ARGV_FLAG, build_predict_command
from frame_selector import FrameSelectorWindow


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_base_dir()
PYTHON = Path(build_predict_command(ROOT)[0])
CLI_EXE = ROOT / "kite_clipper.exe"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}

# exe化したGUIから子プロセスを起動した際にコンソール窓が点滅しないようにします。
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CLI_WATCHDOG_TIMEOUT_SECONDS = 30 * 60
CLI_WATCHDOG_POLL_INTERVAL_SECONDS = 1.0
CLI_TERMINATE_GRACE_SECONDS = 5.0

CLIP_TAGS_FILE = ROOT / "logs" / "clip_tags.json"
THUMBNAIL_DIR = ROOT / "work" / "thumbnails"
THUMBNAIL_MAX_WIDTH = 320
THUMBNAIL_MAX_HEIGHT = 240

# おまかせモード（自動切り出し）関連の設定。
# AI採点モデルのパスは競技（スポーツプロファイル）ごとに異なるため、固定パスの定数は持たず、
# 選択中プロファイルの model_path を都度参照します（KiteClipperGui.current_model_path()）。
AI_SEGMENTS_CSV = ROOT / "logs" / "ai_segments.csv"
OMAKASE_PROCESSED_FILE = ROOT / "logs" / "omakase_processed.json"
OMAKASE_POLL_INTERVAL_MS = 3000
# おまかせモードで同じ動画を再処理する上限回数。これを超えたら処理済みとして記録し、
# 恒久的なエラー（対応外の解像度など）で無限に再試行するのを防ぎます。
OMAKASE_MAX_ATTEMPTS = 3

# GUIで選択中の競技（スポーツプロファイル）の永続化先。
GUI_SETTINGS_FILE = ROOT / "logs" / "gui_settings.json"

# アプリ全体のフォント。ttkの名前付きフォントと、サイズ指定つきの
# 個別フォントタプルの両方でこのファミリーを使います。
APP_FONT_FAMILY = "Meiryo UI"

# ワークフローのメイン実行ボタン用のアクセントカラー。
# ttkテーマ（Windowsのデフォルトテーマ）はbg/fg指定を無視するため、
# 各工程の主ボタンだけ classic な tk.Button を使って目立たせます。
ACCENT_BG = "#2a78d6"
ACCENT_FG = "white"
ACCENT_ACTIVE_BG = "#1d5aa8"
ACCENT_ACTIVE_FG = "white"
ACCENT_DISABLED_FG = "#c9d6ea"

# 左ステッパーの配色（ワイヤーフレーム 1b 準拠）
STEP_DONE_FG = "#2a9d5b"
STEP_ACTIVE_BG = "#eaf2fc"
STEP_IDLE_FG = "#1a1a1a"
STEP_SUMMARY_FG = "#888888"
DANGER_FG = "#c0392b"

# 工程番号 → ステッパーに表示する名前
STEP_LABELS = {
    1: "① 素材",
    2: "② フレーム生成",
    3: "③ 選別",
    4: "④ 切り出し",
    5: "⑤ 結合",
}

# 「最終サイズ」「表示方法」の内部値 ⇔ 画面表示用の日本語ラベル対応表。
# CLIへ渡す値（vertical/horizontal/square/original, contain/cover）は
# 一切変更せず、表示だけを日本語にするための変換に使います。
NORMALIZE_CHOICES: list[tuple[str, str]] = [
    ("vertical", "縦 9:16"),
    ("horizontal", "横 16:9"),
    ("square", "正方形 1:1"),
    ("original", "元動画のサイズ"),
]
FIT_CHOICES: list[tuple[str, str]] = [
    ("contain", "全体を収める"),
    ("cover", "画面いっぱい"),
]


def _label_for_value(choices: list[tuple[str, str]], value: str) -> str:
    for internal, label in choices:
        if internal == value:
            return label
    return value


def _value_for_label(choices: list[tuple[str, str]], label: str) -> str:
    for internal, choice_label in choices:
        if choice_label == label:
            return internal
    return choices[0][0]


def bundled_tool_path(tool_name: str) -> str:
    bundled = app_base_dir() / "vendor" / "ffmpeg" / "windows-x64" / f"{tool_name}.exe"
    if bundled.exists():
        return str(bundled)
    return tool_name


def _accent_button(parent: tk.Widget, text: str, command) -> tk.Button:
    """各工程のメイン実行ボタン用に色付けした tk.Button を生成します。"""
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=ACCENT_BG,
        fg=ACCENT_FG,
        activebackground=ACCENT_ACTIVE_BG,
        activeforeground=ACCENT_ACTIVE_FG,
        disabledforeground=ACCENT_DISABLED_FG,
        relief=tk.RAISED,
        padx=14,
        pady=3,
        font=(APP_FONT_FAMILY, 9, "bold"),
    )


def ffmpeg_path(path: Path) -> str:
    return path.resolve().as_posix()


def safe_stem(path: Path) -> str:
    # kite_clipper.py の safe_stem と同じロジックです。GUIはCLIをsubprocess経由で
    # 使う構成のため、CLIモジュールをimportせずここに複製しています
    # （frame_selector.py の FRAME_NAME_PATTERN 複製と同じ方針）。
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in path.stem)


def load_ai_segment_video_stems(csv_path: Path) -> set[str]:
    """logs/ai_segments.csv に区間提案がある video_stem の集合を返します。

    おまかせモードで「区間提案が0件だった」を判定するために使います。
    ファイルが無い・壊れている場合は空集合を返します。
    """
    if not csv_path.exists():
        return set()
    stems: set[str] = set()
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                stem = row.get("video_stem") or ""
                if stem:
                    stems.add(stem)
    except Exception:
        return set()
    return stems


def load_gui_settings() -> dict:
    """`logs/gui_settings.json` を読み込みます。存在しない・壊れている場合は空dictを返します
    （GUIの起動を絶対に止めないため、推測フォールバックはこの関数の外＝呼び出し側で行います）。
    """
    if not GUI_SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(GUI_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_gui_settings(settings: dict) -> None:
    GUI_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        GUI_SETTINGS_FILE.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # 選択状態の永続化に失敗しても、GUIの動作自体は継続させます。


class KiteClipperGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Media Clip Organizer")
        # 画面からはみ出さないよう、起動サイズを画面の作業領域に合わせて調整します。
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1040, screen_w - 80)
        height = min(700, screen_h - 120)  # タスクバー分の余裕を見込む
        self.geometry(f"{width}x{height}")
        self.minsize(880, 560)
        # ttk/tk 標準ウィジェットが参照する名前付きフォントを Meiryo UI に統一します。
        for font_name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        ):
            tkfont.nametofont(font_name).configure(family=APP_FONT_FAMILY)
        self.clip_paths: list[Path] = []
        self.clip_durations: dict[Path, float] = {}
        self.clip_tags: dict[str, list[str]] = load_clip_tags()
        self.tag_entry_var = tk.StringVar()
        self.person_totals_var = tk.StringVar(value="人物別合計: なし")
        # サムネイルはtkの参照が切れると消えるため、表示中の画像を保持します。
        self.thumbnail_image: tk.PhotoImage | None = None
        self.thumbnail_target: Path | None = None
        self.thumbnail_failed: set[Path] = set()

        self.frame_interval = tk.StringVar(value="1")
        self.pre_roll = tk.StringVar(value="1")
        self.post_roll = tk.StringVar(value="1")
        self.split_gap = tk.StringVar(value="2")
        self.fast_copy = tk.BooleanVar(value=False)
        self.cleanup_review_frames = tk.BooleanVar(value=False)
        self.cleanup_review_photos = tk.BooleanVar(value=False)
        self.cleanup_work = tk.BooleanVar(value=False)
        self.cleanup_selected_clips = tk.BooleanVar(value=False)
        self.last_action: str | None = None
        self.normalize_mode = tk.StringVar(value="vertical")
        self.fit_mode = tk.StringVar(value="contain")
        self.concat_info_var = tk.StringVar()

        self.frame_selector: FrameSelectorWindow | None = None
        self.status_var = tk.StringVar(value="待機中")
        self.total_duration_var = tk.StringVar(value="合計: 00:00.00")
        # CLI実行中に無効化するボタン（多重実行の防止）。tk.Button（アクセント色ボタン）も含むため tk.Widget で受けます。
        self.action_buttons: list[tk.Widget] = []

        # 左ステッパー＋詳細パネル方式の状態
        self.current_step = 1
        self.next_step = 1
        self.counts: dict[str, int] = {}
        self.next_step_var = tk.StringVar(value="▶ 次: ① 素材")
        self.materials_var = tk.StringVar(value="動画 0本 / 写真 0枚")
        self.frames_status_var = tk.StringVar(value="")
        self.select_status_var = tk.StringVar(value="")
        self.cut_status_var = tk.StringVar(value="")
        self.detail_panels: dict[int, ttk.Frame] = {}
        self.panel_footers: dict[int, ttk.Frame] = {}
        self.panel_canvases: dict[int, tk.Canvas] = {}
        self.step_rows: dict[int, dict[str, tk.Widget]] = {}
        self.cleanup_dialog: tk.Toplevel | None = None

        # 工程5「選択クリップ」のタグ編集トグル用の状態
        self.clip_tags_summary_var = tk.StringVar(value="登場人物: （未設定）")
        self.tag_editor_visible = False

        # ログは下部バーに最終行のみ表示し、クリックで全文を展開します。
        self.log_visible = False
        self.last_log_var = tk.StringVar(value="最終行: （まだログはありません）")

        # おまかせモード（自動切り出し）の状態
        self.busy = False  # run_cli/run_frame_scorer_predict実行中かどうか（set_busyで更新）
        self.omakase_mode = tk.BooleanVar(value=False)
        self.omakase_status_var = tk.StringVar(value="")
        self.omakase_poll_after_id: str | None = None
        self.omakase_running = False  # 自動パイプライン(抽出→採点→切り出し)実行中かどうか
        # ファイル名 -> (直近ポーリングでのサイズ, そのサイズが何回連続で観測されたか)
        self.omakase_pending_sizes: dict[str, tuple[int, int]] = {}
        self.omakase_processed: dict[str, dict[str, float]] = self._load_omakase_processed()
        # ファイル名 -> 連続失敗回数。恒久的なエラーで同じ動画を延々と再処理しないための上限管理。
        # 起動しなおせばリセットされる（原因を直してから再試行できるように、あえて保存しない）。
        self.omakase_failures: dict[str, int] = {}

        # ---- 競技（スポーツプロファイル）選択の状態（マルチスポーツ対応 Phase 3）
        # 起動不能を避けるため list_profiles_lenient() を使い、読めなかったものは
        # ログへの警告に留める（3-0）。ログ欄はこの時点でまだ存在しないため、
        # 警告文はいったん保留し、create_widgets() の後にまとめて出力します。
        self._pending_startup_warnings: list[str] = []
        profiles, profile_errors = sport_profile.list_profiles_lenient(ROOT)
        for filename, reason in profile_errors:
            self._pending_startup_warnings.append(
                f"警告: スポーツプロファイル '{filename}' を読み込めませんでした（{reason}）"
            )
        self.sport_profiles: dict[str, sport_profile.SportProfile] = {p.id: p for p in profiles}
        self.sport_ids_ordered: list[str] = [p.id for p in profiles]

        saved_settings = load_gui_settings()
        saved_sport_id = saved_settings.get("sport_id") if isinstance(saved_settings, dict) else None
        if saved_sport_id in self.sport_profiles:
            initial_sport_id = saved_sport_id
        else:
            if sport_profile.DEFAULT_SPORT_ID in self.sport_profiles:
                initial_sport_id = sport_profile.DEFAULT_SPORT_ID
            elif self.sport_ids_ordered:
                initial_sport_id = self.sport_ids_ordered[0]
            else:
                initial_sport_id = None  # プロファイルが1件も読めなかった場合（極端なケース）
            if saved_sport_id and saved_sport_id != initial_sport_id:
                self._pending_startup_warnings.append(
                    f"警告: 保存されていた競技 '{saved_sport_id}' のプロファイルが見つからないため、"
                    f"既定（{initial_sport_id or '（利用可能な競技なし）'}）に戻しました。"
                )
        self.sport_id_var = tk.StringVar(value=initial_sport_id or "")
        # 混在ガードに引っかかった際、「そのまま続行する」を選んだ場合に True になります。
        # True の間は CLI 呼び出しに --allow-profile-switch を自動付与します。
        self.allow_profile_switch = False

        self.create_widgets()
        self.refresh_all()
        self.show_step(self.next_step)
        for message in self._pending_startup_warnings:
            self.append_log(message)
        self._pending_startup_warnings.clear()

    # ------------------------------------------------------------------ UI構築
    def create_widgets(self) -> None:
        self._row_bg = self.cget("background")

        # ---- ヘッダー: タイトル / 「次にやること」バッジ / 状態 / 再読み込み
        header = ttk.Frame(self, padding=(12, 10, 12, 8))
        header.pack(fill=tk.X)
        ttk.Label(header, text="Media Clip Organizer", font=(APP_FONT_FAMILY, 15, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="再読み込み", command=self.refresh_all).pack(side=tk.RIGHT)
        ttk.Label(header, textvariable=self.status_var).pack(side=tk.RIGHT, padx=(0, 10))
        self.next_badge = tk.Label(
            header,
            textvariable=self.next_step_var,
            bg=STEP_ACTIVE_BG,
            fg=ACCENT_BG,
            font=(APP_FONT_FAMILY, 9, "bold"),
            padx=12,
            pady=3,
            cursor="hand2",
        )
        self.next_badge.pack(side=tk.RIGHT, padx=(0, 10))
        self.next_badge.bind("<Button-1>", lambda _e: self.show_step(self.next_step))

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # ---- 競技（スポーツプロファイル）選択バー。ステッパーより上に配置します（仕様書 2.5）。
        sport_bar = ttk.Frame(self, padding=(12, 6, 12, 6))
        sport_bar.pack(fill=tk.X)
        ttk.Label(sport_bar, text="競技:", font=(APP_FONT_FAMILY, 9, "bold")).pack(side=tk.LEFT, padx=(0, 6))
        self.sport_display_var = tk.StringVar()
        self.sport_combo = ttk.Combobox(
            sport_bar, textvariable=self.sport_display_var, state="readonly", width=20
        )
        self.sport_combo.pack(side=tk.LEFT)
        self.sport_combo.bind("<<ComboboxSelected>>", self.on_sport_selected)
        self._refresh_sport_combo_values()
        ttk.Label(
            sport_bar,
            text="（処理実行中は切り替えできません）",
            foreground="#888",
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # ---- 下部: ログバー（最終行表示＋クリックで展開）
        bottom = ttk.Frame(self, padding=(12, 4, 12, 8))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bar = ttk.Frame(bottom)
        bar.pack(fill=tk.X)
        self.log_toggle_button = ttk.Button(bar, text="▸ ログ", width=9, command=self.toggle_log)
        self.log_toggle_button.pack(side=tk.LEFT)
        ttk.Label(bar, textvariable=self.last_log_var, foreground="#888").pack(
            side=tk.LEFT, padx=8, fill=tk.X
        )
        self.log_frame = ttk.Frame(bottom)
        log_scroll = ttk.Scrollbar(self.log_frame)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = tk.Text(self.log_frame, height=9, wrap=tk.WORD, yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.configure(command=self.log_text.yview)
        # log_frame は toggle_log() が呼ばれるまで pack しません（折りたたみ状態）。

        # ---- 中央: 左ステッパー ＋ 右詳細パネル
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        self._build_stepper(body)
        self.detail_container = ttk.Frame(body)
        self.detail_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
        self._build_panel_materials()
        self._build_panel_generate()
        self._build_panel_select()
        self._build_panel_cut()
        self._build_panel_concat()

        # パネル本文のホイールスクロール。add="+" で既存のバインドを潰さないようにします。
        self.bind_all("<MouseWheel>", self._on_panel_mousewheel, add="+")

    def _build_stepper(self, parent: ttk.Frame) -> None:
        outer = ttk.LabelFrame(parent, text="工程")
        outer.pack(side=tk.LEFT, fill=tk.Y)
        inner = ttk.Frame(outer, padding=(4, 4))
        inner.pack(fill=tk.BOTH, expand=True)

        for number in (1, 2, 3, 4, 5):
            row = tk.Frame(
                inner,
                bg=self._row_bg,
                highlightthickness=1,
                highlightbackground=self._row_bg,
                cursor="hand2",
            )
            mark = tk.Label(row, text="", width=2, bg=self._row_bg, font=(APP_FONT_FAMILY, 10, "bold"))
            name = tk.Label(row, text=STEP_LABELS[number], bg=self._row_bg, fg=STEP_IDLE_FG, anchor=tk.W)
            summary = tk.Label(row, text="", bg=self._row_bg, fg=STEP_SUMMARY_FG, font=(APP_FONT_FAMILY, 8))
            mark.pack(side=tk.LEFT, padx=(6, 2), pady=7)
            name.pack(side=tk.LEFT, pady=7)
            summary.pack(side=tk.RIGHT, padx=(8, 6))
            row.pack(fill=tk.X, pady=2)
            for widget in (row, mark, name, summary):
                widget.bind("<Button-1>", lambda _e, n=number: self.show_step(n))
            self.step_rows[number] = {"row": row, "mark": mark, "name": name, "summary": summary}

        # フッター: 削除系操作の集約入口（実行内容は確認ダイアログで選択）
        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(10, 4))
        tk.Button(
            inner,
            text="🗑 整理・削除…",
            fg=DANGER_FG,
            relief=tk.FLAT,
            anchor=tk.W,
            cursor="hand2",
            command=self.open_cleanup_dialog,
        ).pack(fill=tk.X, padx=2, pady=(0, 2))

    # ---------------------------------------------------------- 各工程のパネル
    def _panel_inner(self, number: int, title: str) -> tuple[ttk.Frame, ttk.Frame]:
        """各工程のパネルを「スクロール可能な本文＋下部固定フッター」の
        2段構成で作ります。戻り値は (本文用の内側Frame, フッターFrame) です。
        """
        panel = ttk.LabelFrame(self.detail_container, text=title)
        self.detail_panels[number] = panel

        # ---- 下部固定フッター（本文より先にpackすることで下端に固定されます）
        footer = ttk.Frame(panel, padding=(12, 8))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Separator(panel, orient=tk.HORIZONTAL).pack(side=tk.BOTTOM, fill=tk.X)
        self.panel_footers[number] = footer

        # ---- 本文: Canvas + 縦Scrollbar + 内側Frame（スクロール可能）
        body_area = ttk.Frame(panel)
        body_area.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(body_area, highlightthickness=0, background=self._row_bg)
        scrollbar = ttk.Scrollbar(body_area, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = ttk.Frame(canvas, padding=12)
        inner_window = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def _on_inner_configure(_event: object, _canvas: tk.Canvas = canvas) -> None:
            _canvas.configure(scrollregion=_canvas.bbox("all"))

        def _on_canvas_configure(
            event: tk.Event, _canvas: tk.Canvas = canvas, _window: int = inner_window
        ) -> None:
            _canvas.itemconfigure(_window, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # マウスホイールはアプリ共通の _on_panel_mousewheel で処理するため、
        # ここではパネル番号→Canvasの対応だけ登録します。
        self.panel_canvases[number] = canvas

        return inner, footer

    def _on_panel_mousewheel(self, event: tk.Event) -> None:
        """パネル本文のCanvasをマウスホイールでスクロールします。

        Windowsではホイールイベントがフォーカス先へ届くことがあるため、
        カーソル直下のウィジェットを起点に、所属するパネルCanvasを親方向へ
        たどって特定します。リストやログ（Listbox/Text）の上では、それ自身の
        スクロールに任せるため何もしません。
        """
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        if widget is None or widget.winfo_toplevel() is not self:
            # 選別ビューアなど別ウィンドウ上のホイールには干渉しません。
            return
        canvases = set(self.panel_canvases.values())
        while widget is not None:
            if isinstance(widget, (tk.Listbox, tk.Text)):
                return
            if widget in canvases:
                widget.yview_scroll(int(-event.delta / 120) * 3, "units")
                return
            widget = widget.master

    def _build_panel_materials(self) -> None:
        inner, footer = self._panel_inner(1, "① 素材を用意")
        ttk.Label(inner, text="input/videos に元動画、input/photos に元写真を入れてください。").pack(anchor=tk.W)
        ttk.Label(inner, textvariable=self.materials_var, font=(APP_FONT_FAMILY, 11, "bold")).pack(
            anchor=tk.W, pady=(10, 14)
        )
        row = ttk.Frame(inner)
        row.pack(anchor=tk.W)
        ttk.Button(row, text="📁 input/videos を開く", command=lambda: self.open_folder(ROOT / "input" / "videos")).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(row, text="📁 input/photos を開く", command=lambda: self.open_folder(ROOT / "input" / "photos")).pack(
            side=tk.LEFT
        )
        ttk.Label(
            inner,
            text="素材を入れたら下の「再読み込みして次へ ▶」で本数を更新し、「② フレーム生成」へ進んでください。",
            foreground="#888",
        ).pack(anchor=tk.W, pady=(16, 0))

        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(16, 10))
        ttk.Checkbutton(
            inner,
            text="おまかせモード（自動切り出し）",
            variable=self.omakase_mode,
            command=self.on_omakase_toggle,
        ).pack(anchor=tk.W)
        ttk.Label(
            inner,
            text=(
                "ONの間、input/videos に置かれた新しい動画を自動検知し、"
                "フレーム抽出→AI採点→AI提案区間からの切り出しまでを自動実行します"
                "（selected_clips に出力、結合は行いません）。"
                "AI採点モデル（models/frame_scorer.pkl）が未学習の場合は使えません。"
            ),
            foreground="#888",
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(inner, textvariable=self.omakase_status_var, foreground=ACCENT_BG).pack(
            anchor=tk.W, pady=(6, 0)
        )

        def _refresh_and_advance() -> None:
            self.refresh_all()
            self.show_step(2)

        _accent_button(footer, "再読み込みして次へ ▶", _refresh_and_advance).pack(side=tk.RIGHT)

    def _build_panel_generate(self) -> None:
        inner, footer = self._panel_inner(2, "② フレーム生成")
        ttk.Label(
            inner,
            text="元動画から一定間隔でフレーム画像を抽出します（review/frames に出力）。\n写真は候補チェックのうえ review/photos にコピーされます。",
        ).pack(anchor=tk.W)
        row = ttk.Frame(inner)
        row.pack(anchor=tk.W, pady=(12, 4))
        ttk.Label(row, text="フレーム間隔(秒)").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Combobox(
            row,
            textvariable=self.frame_interval,
            values=("1", "0.5", "0.25"),
            width=8,
        ).pack(side=tk.LEFT)
        ttk.Label(inner, textvariable=self.frames_status_var, font=(APP_FONT_FAMILY, 11, "bold")).pack(
            anchor=tk.W, pady=(10, 14)
        )
        row2 = ttk.Frame(inner)
        row2.pack(anchor=tk.W)
        ttk.Button(row2, text="📁 review/frames を開く", command=lambda: self.open_folder(ROOT / "review" / "frames")).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(row2, text="📁 review/photos を開く", command=lambda: self.open_folder(ROOT / "review" / "photos")).pack(
            side=tk.LEFT
        )

        generate_button = _accent_button(footer, "フレームを生成", self.generate_candidates)
        generate_button.pack(side=tk.RIGHT)
        self.action_buttons.append(generate_button)

    def _build_panel_select(self) -> None:
        inner, footer = self._panel_inner(3, "③ フレームを選別")
        ttk.Label(
            inner,
            text="ビューアで残したいフレームを選ぶと selected_frames にコピーされます。\nAIスコア・提案区間・音量ピークを参考に選べます。",
        ).pack(anchor=tk.W)
        ttk.Label(inner, textvariable=self.select_status_var, font=(APP_FONT_FAMILY, 11, "bold")).pack(
            anchor=tk.W, pady=(10, 14)
        )
        ttk.Button(inner, text="📁 selected_frames を開く", command=lambda: self.open_folder(ROOT / "selected_frames")).pack(
            anchor=tk.W
        )
        ttk.Label(
            inner,
            text="AI採点（スコア付与と区間提案）はビューア内の「AI採点を実行」から行えます。",
            foreground="#888",
        ).pack(anchor=tk.W, pady=(16, 0))
        ttk.Label(
            inner,
            text="ビューアを閉じると採用数が更新されます。選び終えたら「④ 切り出し」へ進んでください。",
            foreground="#888",
        ).pack(anchor=tk.W, pady=(4, 0))

        _accent_button(footer, "選別ビューアを開く", self.open_frame_selector).pack(side=tk.RIGHT)

    def _build_panel_cut(self) -> None:
        inner, footer = self._panel_inner(4, "④ 選択フレームから動画を切り出し")
        ttk.Label(
            inner,
            text="selected_frames の連続した時間帯ごとに、元動画から selected_clips へ動画を切り出します。",
        ).pack(anchor=tk.W)
        row = ttk.Frame(inner)
        row.pack(anchor=tk.W, pady=(12, 4))
        for label, var in [
            ("前余白", self.pre_roll),
            ("後余白", self.post_roll),
            ("分割間隔", self.split_gap),
        ]:
            ttk.Label(row, text=label).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Entry(row, textvariable=var, width=7).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(row, text="高速コピー", variable=self.fast_copy).pack(side=tk.LEFT)
        ttk.Label(
            inner,
            text=(
                "高速コピー: 再エンコードせずコピーするため処理が速く、おすすめです。"
                "カット位置がキーフレーム単位になるため、クリップの前後が少しずれることがあります。"
            ),
            foreground="#888",
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 4))
        ttk.Label(inner, textvariable=self.cut_status_var, font=(APP_FONT_FAMILY, 11, "bold")).pack(
            anchor=tk.W, pady=(10, 14)
        )
        ttk.Button(
            inner, text="📁 selected_clips を開く", command=lambda: self.open_folder(ROOT / "selected_clips")
        ).pack(anchor=tk.W)
        ttk.Label(
            inner,
            text="切り出し後の不要ファイル整理は、左下の「🗑 整理・削除…」から行えます。",
            foreground="#888",
        ).pack(anchor=tk.W, pady=(16, 0))

        cut_button = _accent_button(footer, "切り出しを実行", self.cut_from_selected_frames)
        cut_button.pack(side=tk.RIGHT)
        self.action_buttons.append(cut_button)

    def _build_panel_concat(self) -> None:
        concat_frame, footer = self._panel_inner(5, "⑤ selected_clips を結合")

        normalize_frame = ttk.Frame(concat_frame)
        normalize_frame.pack(fill=tk.X)
        ttk.Label(normalize_frame, text="最終サイズ").pack(side=tk.LEFT, padx=(0, 4))
        self.normalize_display_var = tk.StringVar()
        normalize_combo = ttk.Combobox(
            normalize_frame,
            textvariable=self.normalize_display_var,
            values=[label for _value, label in NORMALIZE_CHOICES],
            width=14,
            state="readonly",
        )
        normalize_combo.pack(side=tk.LEFT, padx=4)
        ttk.Label(normalize_frame, text="表示方法").pack(side=tk.LEFT, padx=(12, 4))
        self.fit_display_var = tk.StringVar()
        fit_combo = ttk.Combobox(
            normalize_frame,
            textvariable=self.fit_display_var,
            values=[label for _value, label in FIT_CHOICES],
            width=12,
            state="readonly",
        )
        fit_combo.pack(side=tk.LEFT, padx=4)
        ttk.Label(
            normalize_frame,
            textvariable=self.concat_info_var,
            foreground="#888",
            wraplength=420,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, padx=12)

        # 表示ラベルの初期値は内部値（normalize_mode/fit_mode）から逆引きします。
        self.normalize_display_var.set(_label_for_value(NORMALIZE_CHOICES, self.normalize_mode.get()))
        self.fit_display_var.set(_label_for_value(FIT_CHOICES, self.fit_mode.get()))

        def _on_normalize_selected(_event: object = None) -> None:
            self.normalize_mode.set(_value_for_label(NORMALIZE_CHOICES, self.normalize_display_var.get()))
            self.update_concat_info()

        def _on_fit_selected(_event: object = None) -> None:
            self.fit_mode.set(_value_for_label(FIT_CHOICES, self.fit_display_var.get()))
            self.update_concat_info()

        normalize_combo.bind("<<ComboboxSelected>>", _on_normalize_selected)
        fit_combo.bind("<<ComboboxSelected>>", _on_fit_selected)
        self.update_concat_info()

        list_area = ttk.Frame(concat_frame)
        list_area.pack(fill=tk.BOTH, expand=True, pady=8)

        # 選択クリップの詳細パネル（サムネイル・タグ表示・タグ編集を1か所に集約）
        detail_frame = ttk.LabelFrame(list_area, text="選択クリップ")
        detail_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        thumb_holder = ttk.Frame(detail_frame, width=THUMBNAIL_MAX_WIDTH + 8, height=THUMBNAIL_MAX_HEIGHT + 8)
        thumb_holder.pack(padx=6, pady=(6, 2))
        thumb_holder.pack_propagate(False)
        self.thumbnail_label = ttk.Label(thumb_holder, text="クリップを選択すると\nサムネイルを表示", anchor=tk.CENTER)
        self.thumbnail_label.pack(fill=tk.BOTH, expand=True)
        self.selected_clip_var = tk.StringVar(value="（未選択）")
        ttk.Label(
            detail_frame,
            textvariable=self.selected_clip_var,
            wraplength=THUMBNAIL_MAX_WIDTH,
        ).pack(fill=tk.X, padx=6, pady=(0, 4))

        # 通常時に表示する読み取り専用のタグ要約ラベル。
        ttk.Label(
            detail_frame,
            textvariable=self.clip_tags_summary_var,
            wraplength=THUMBNAIL_MAX_WIDTH,
        ).pack(anchor=tk.W, padx=6, pady=(0, 2))
        self.tag_editor_toggle_button = ttk.Button(
            detail_frame, text="タグを編集…", command=self.toggle_tag_editor
        )
        self.tag_editor_toggle_button.pack(anchor=tk.W, padx=6, pady=(0, 6))

        # タグ編集UI（Entry・名前ボタン等）は「タグを編集…」を押すまで隠しておきます。
        self.tag_editor_frame = ttk.Frame(detail_frame)

        tag_row = ttk.Frame(self.tag_editor_frame)
        tag_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(tag_row, text="登場人物").pack(side=tk.LEFT, padx=(0, 4))
        tag_entry = ttk.Entry(tag_row, textvariable=self.tag_entry_var)
        tag_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tag_entry.bind("<Return>", lambda _event: self.apply_tags_from_entry())
        ttk.Button(tag_row, text="タグ設定", command=self.apply_tags_from_entry).pack(side=tk.LEFT)
        ttk.Label(self.tag_editor_frame, text="カンマ区切り / 空欄で設定すると解除").pack(anchor=tk.W)

        ttk.Label(self.tag_editor_frame, text="名前ボタン（クリックで付け外し）").pack(anchor=tk.W, pady=(6, 0))
        self.name_buttons_frame = ttk.Frame(self.tag_editor_frame)
        self.name_buttons_frame.pack(fill=tk.X, pady=(2, 6))

        scrollbar = ttk.Scrollbar(list_area)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.clip_list = tk.Listbox(list_area, height=10, selectmode=tk.SINGLE)
        self.clip_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=self.clip_list.yview)
        self.clip_list.configure(yscrollcommand=scrollbar.set)
        self.clip_list.bind("<<ListboxSelect>>", self.on_clip_select)

        total_frame = ttk.Frame(concat_frame)
        total_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(total_frame, textvariable=self.total_duration_var, font=(APP_FONT_FAMILY, 11, "bold")).pack(side=tk.LEFT)
        ttk.Label(total_frame, textvariable=self.person_totals_var).pack(side=tk.LEFT, padx=(16, 0))

        order_buttons = ttk.Frame(concat_frame)
        order_buttons.pack(fill=tk.X)
        ttk.Button(order_buttons, text="上へ", command=lambda: self.move_clip(-1)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(order_buttons, text="下へ", command=lambda: self.move_clip(1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(order_buttons, text="クリップ再読み込み", command=self.refresh_clips).pack(side=tk.LEFT, padx=4)

        ttk.Button(footer, text="📁 final を開く", command=lambda: self.open_folder(ROOT / "final")).pack(
            side=tk.LEFT
        )
        concat_button = _accent_button(footer, "結合する ▶", self.concat_selected_clips)
        concat_button.pack(side=tk.RIGHT)
        self.action_buttons.append(concat_button)

    def toggle_tag_editor(self) -> None:
        """工程5「選択クリップ」のタグ編集UI（Entry・名前ボタン等）の表示/非表示を切り替えます。"""
        self.tag_editor_visible = not self.tag_editor_visible
        if self.tag_editor_visible:
            self.tag_editor_frame.pack(fill=tk.X, padx=6, pady=(0, 6))
            self.tag_editor_toggle_button.configure(text="タグ編集を閉じる")
        else:
            self.tag_editor_frame.pack_forget()
            self.tag_editor_toggle_button.configure(text="タグを編集…")

    # ------------------------------------------------------ ステッパーの状態管理
    def show_step(self, step: int) -> None:
        if step not in self.detail_panels:
            return
        self.current_step = step
        for panel in self.detail_panels.values():
            panel.pack_forget()
        self.detail_panels[step].pack(fill=tk.BOTH, expand=True)
        self.update_stepper()

    def step_done_flags(self) -> dict[int, bool]:
        counts = self.counts
        return {
            1: counts.get("input_videos", 0) + counts.get("input_photos", 0) > 0,
            2: counts.get("review_frames", 0) + counts.get("review_photos", 0) > 0,
            3: counts.get("selected_frames", 0) > 0,
            4: counts.get("selected_clips", 0) > 0,
            5: counts.get("final", 0) > 0,
        }

    def update_stepper(self) -> None:
        if not self.step_rows:
            return
        done = self.step_done_flags()
        self.next_step = next((n for n in (1, 2, 3, 4, 5) if not done[n]), 5)
        self.next_step_var.set(f"▶ 次: {STEP_LABELS[self.next_step]}")

        counts = self.counts
        total = sum(self.clip_durations.get(path, 0.0) for path in self.clip_paths)
        summaries = {
            1: f"{counts.get('input_videos', 0)}本/{counts.get('input_photos', 0)}枚",
            2: f"{counts.get('review_frames', 0)}枚",
            3: f"{counts.get('selected_frames', 0)}採用",
            4: f"{counts.get('selected_clips', 0)}本",
            5: format_duration(total) if self.clip_paths else "",
        }

        for number, widgets in self.step_rows.items():
            is_current = number == self.current_step
            bg = STEP_ACTIVE_BG if is_current else self._row_bg
            border = ACCENT_BG if is_current else self._row_bg
            widgets["row"].configure(bg=bg, highlightbackground=border, highlightcolor=border)
            if done[number]:
                mark_text, mark_fg = "✔", STEP_DONE_FG
            elif is_current:
                mark_text, mark_fg = "▶", ACCENT_BG
            else:
                mark_text, mark_fg = "", STEP_SUMMARY_FG
            widgets["mark"].configure(text=mark_text, fg=mark_fg, bg=bg)
            widgets["name"].configure(
                bg=bg,
                fg=ACCENT_BG if is_current else STEP_IDLE_FG,
                font=(APP_FONT_FAMILY, 9, "bold") if is_current else (APP_FONT_FAMILY, 9),
            )
            widgets["summary"].configure(
                text=summaries[number],
                bg=bg,
                fg=ACCENT_BG if is_current else STEP_SUMMARY_FG,
            )

    # ---------------------------------------------------------------- ログ表示
    def toggle_log(self) -> None:
        if self.log_visible:
            self.log_frame.pack_forget()
            self.log_toggle_button.configure(text="▸ ログ")
        else:
            self.log_frame.pack(fill=tk.BOTH, pady=(4, 0))
            self.log_toggle_button.configure(text="▾ ログ")
        self.log_visible = not self.log_visible

    # ------------------------------------------------------ 競技（スポーツプロファイル）
    def current_profile(self) -> sport_profile.SportProfile | None:
        """現在GUIで選択中の競技のプロファイルを返します。

        プロファイルが1件も読み込めなかった極端なケースでは None を返します
        （呼び出し側は `--sport` を付けずに従来どおり CLI 側の既定(kite)に解決させます）。
        """
        return self.sport_profiles.get(self.sport_id_var.get())

    def current_model_path(self) -> Path:
        """選択中プロファイルのAI採点モデルパスを返します（3-2。旧 FRAME_SCORER_MODEL_PATH の代替）。"""
        profile = self.current_profile()
        if profile is not None:
            return profile.model_path
        # プロファイルが1件も読めなかった場合のみ、後方互換のため旧来のkite固定パスにフォールバックします。
        return ROOT / "models" / "frame_scorer.pkl"

    def current_sport_display_name(self) -> str:
        profile = self.current_profile()
        return profile.display_name if profile is not None else self.sport_id_var.get() or "（未選択）"

    def current_predict_extra_args(self) -> list[str]:
        """frame_scorer.py predict に渡す `--sport` / `--allow-profile-switch` 追加引数。

        FrameSelectorWindow（ビューア内「AI採点を実行」）から都度呼ばれるコールバックです。
        """
        args: list[str] = []
        profile = self.current_profile()
        if profile is not None:
            args = ["--sport", profile.id]
        if self.allow_profile_switch:
            args.append("--allow-profile-switch")
        return args

    def _refresh_sport_combo_values(self) -> None:
        labels = [self.sport_profiles[sid].display_name for sid in self.sport_ids_ordered]
        self.sport_combo.configure(values=labels)
        profile = self.current_profile()
        self.sport_display_var.set(profile.display_name if profile is not None else "")

    def on_sport_selected(self, _event: object = None) -> None:
        label = self.sport_display_var.get()
        new_id = next(
            (sid for sid in self.sport_ids_ordered if self.sport_profiles[sid].display_name == label),
            None,
        )
        if new_id is None or new_id == self.sport_id_var.get():
            self._refresh_sport_combo_values()
            return
        if self.busy:
            # コンボボックスは busy 時 disabled にしているため通常は到達しませんが、保険で戻します。
            self._refresh_sport_combo_values()
            return
        self._guarded(new_id, lambda: self._finish_sport_switch(new_id))

    def _finish_sport_switch(self, new_sport_id: str) -> None:
        self.sport_id_var.set(new_sport_id)
        self._refresh_sport_combo_values()
        save_gui_settings({"sport_id": new_sport_id})
        name = self.current_sport_display_name()
        self.append_log(f"競技を切り替えました: {name}")
        self.status_var.set(f"競技: {name}")
        if self.omakase_mode.get():
            # AI採点モデルが競技ごとに変わるため、おまかせモードは一旦解除して再確認を促します。
            self.omakase_mode.set(False)
            self.omakase_status_var.set("")
            self.append_log("おまかせモード: 競技切り替えのため監視を停止しました。必要なら再度ONにしてください。")
            if self.omakase_poll_after_id is not None:
                self.after_cancel(self.omakase_poll_after_id)
                self.omakase_poll_after_id = None
        self.refresh_all()

    def _guarded(self, sport_id: str, run_after: Callable[[], None]) -> None:
        """混在ガード（sport_profile.check_profile_switch）を事前チェックしてから run_after を実行します。

        引っかかった場合は run_cli を呼ばずに確認ダイアログを出し、ユーザーの選択後に run_after を
        再実行します（中心課題：GUIが謎の終了コード2で停止する経路を塞ぐため。TASKS.md 3-1）。
        """
        try:
            sport_profile.check_profile_switch(ROOT, sport_id, self.allow_profile_switch)
        except sport_profile.ProfileSwitchGuardError:
            findings = sport_profile.find_residual_artifacts(ROOT)
            self._show_guard_dialog(sport_id, findings, run_after)
            return
        run_after()

    def _show_guard_dialog(
        self, sport_id: str, findings: list[str], run_after: Callable[[], None]
    ) -> None:
        display_name = self.sport_profiles[sport_id].display_name if sport_id in self.sport_profiles else sport_id
        dialog = tk.Toplevel(self)
        dialog.title("競技の切り替え")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)
        message = (
            f"「{display_name}」に切り替えようとしていますが、前回の競技の中間生成物が残っています。\n\n"
            + "\n".join(findings)
            + "\n\nこのまま処理を続けると、別競技のフレームを誤ったモデルで採点する事故につながります。"
            "\nいずれかを選んでください。"
        )
        ttk.Label(frame, text=message, wraplength=440, justify=tk.LEFT).pack(anchor=tk.W)

        def _on_cleanup() -> None:
            dialog.destroy()
            self._cleanup_then(sport_id, run_after)

        def _on_continue() -> None:
            dialog.destroy()
            self.allow_profile_switch = True
            self.append_log(
                "競技切り替え: 中間生成物の混在を承知のうえで続行します（--allow-profile-switch）。"
            )
            run_after()

        def _on_cancel() -> None:
            dialog.destroy()
            self._refresh_sport_combo_values()  # コンボボックスの表示を選択前の状態に戻します。

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(14, 0))
        _accent_button(buttons, "整理してから切り替える", _on_cleanup).pack(side=tk.LEFT)
        ttk.Button(buttons, text="そのまま続行する", command=_on_continue).pack(side=tk.LEFT, padx=8)
        ttk.Button(buttons, text="キャンセル", command=_on_cancel).pack(side=tk.RIGHT)
        dialog.protocol("WM_DELETE_WINDOW", _on_cancel)

    def _cleanup_then(self, sport_id: str, run_after: Callable[[], None]) -> None:
        """「整理してから切り替える」を選んだ際の整理パイプライン。

        --cleanup-review と --cleanup-after-cut（selected_frames等）をCLI経由で実行し、
        最後に logs/ai_scores.csv・logs/ai_segments.csv をGUI側で直接削除します
        （どちらもCLIの既存コマンドでは削除対象になっていないため。cleanup-after-cut は
        混在ガード自体の対象コマンドのため、ここでは整理目的と明示して --allow-profile-switch を付けます）。
        """
        self.append_log("競技切り替え: 中間生成物を整理しています…")
        self._run_cli_raw(
            ["--sport", sport_id, "--cleanup-review", "--confirm-delete"],
            "整理中(1/2)",
            on_done=lambda code: self._cleanup_then_step2(code, sport_id, run_after),
        )

    def _cleanup_then_step2(self, return_code: int, sport_id: str, run_after: Callable[[], None]) -> None:
        if return_code != 0:
            self.on_command_done(return_code)
            messagebox.showerror(
                "整理に失敗しました",
                f"review の整理に失敗しました（終了コード {return_code}）。競技の切り替えを中止しました。",
            )
            self._refresh_sport_combo_values()
            return
        self._run_cli_raw(
            [
                "--sport", sport_id, "--allow-profile-switch",
                "--cleanup-after-cut", "--confirm-delete",
                "--include-review-frames", "--include-review-photos",
            ],
            "整理中(2/2)",
            on_done=lambda code: self._cleanup_then_step3(code, sport_id, run_after),
        )

    def _cleanup_then_step3(self, return_code: int, sport_id: str, run_after: Callable[[], None]) -> None:
        if return_code != 0:
            self.on_command_done(return_code)
            messagebox.showerror(
                "整理に失敗しました",
                f"selected_frames 等の整理に失敗しました（終了コード {return_code}）。競技の切り替えを中止しました。",
            )
            self._refresh_sport_combo_values()
            return
        for csv_name in ("ai_scores.csv", "ai_segments.csv"):
            csv_path = ROOT / "logs" / csv_name
            try:
                if csv_path.exists():
                    csv_path.unlink()
            except OSError as exc:
                self.append_log(f"警告: {csv_path} を削除できませんでした（{exc}）")
        self.append_log("競技切り替え: 整理が完了しました。")
        self.on_command_done(0)
        run_after()

    # ------------------------------------------------------------ 整理・削除
    def open_cleanup_dialog(self) -> None:
        if self.cleanup_dialog is not None and self.cleanup_dialog.winfo_exists():
            self.cleanup_dialog.lift()
            self.cleanup_dialog.focus_set()
            return
        dialog = tk.Toplevel(self)
        dialog.title("整理・削除")
        dialog.transient(self)
        dialog.resizable(False, False)
        self.cleanup_dialog = dialog

        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text=(
                "切り出し後に不要になったファイルを整理します。\n"
                "selected_frames は常に削除対象です。\n"
                "元動画と input フォルダには触りません。"
            ),
        ).pack(anchor=tk.W)
        checks = ttk.Frame(frame)
        checks.pack(anchor=tk.W, pady=(10, 12))
        for text, var in [
            ("review/frames も削除", self.cleanup_review_frames),
            ("review/photos も削除", self.cleanup_review_photos),
            ("work も削除", self.cleanup_work),
            ("selected_clips も削除", self.cleanup_selected_clips),
        ]:
            ttk.Checkbutton(checks, text=text, variable=var).pack(anchor=tk.W, pady=1)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X)
        dry_run_button = ttk.Button(buttons, text="削除予定を確認", command=self.cleanup_after_cut_dry_run)
        dry_run_button.pack(side=tk.LEFT)
        delete_button = tk.Button(
            buttons,
            text="削除を実行…",
            fg=DANGER_FG,
            command=self.cleanup_after_cut_delete,
        )
        delete_button.pack(side=tk.LEFT, padx=8)
        ttk.Button(buttons, text="閉じる", command=dialog.destroy).pack(side=tk.RIGHT)
        self.action_buttons.extend([dry_run_button, delete_button])

    # ---------------------------------------------------------------- 各種処理
    def open_frame_selector(self) -> None:
        if self.frame_selector is not None and self.frame_selector.winfo_exists():
            self.frame_selector.lift()
            self.frame_selector.focus_set()
            return
        self.frame_selector = FrameSelectorWindow(
            self,
            ROOT,
            get_cut_params=self.current_cut_params,
            on_selection_change=self.refresh_counts,
            get_predict_extra_args=self.current_predict_extra_args,
        )

    def current_cut_params(self) -> tuple[float, float, float]:
        def to_float(value: str, default: float) -> float:
            try:
                return max(0.0, float(value))
            except ValueError:
                return default

        return (
            to_float(self.split_gap.get(), 2.0),
            to_float(self.pre_roll.get(), 1.0),
            to_float(self.post_roll.get(), 1.0),
        )

    def update_concat_info(self, _event: object = None) -> None:
        # 選択中の「最終サイズ／表示方法」でどう結合されるかを説明するラベルを更新します。
        fit_note = "（全体を収める=余白付きで全体表示 / 画面いっぱい=はみ出す部分を切り取り）"
        mode = self.normalize_mode.get()
        if mode == "original":
            text = "元動画のサイズ: 無劣化で結合します（全クリップの形式が同一である必要あり）"
        elif mode == "vertical":
            text = f"縦 9:16: 1080×1920 / 30fps に再エンコードして結合します{fit_note}"
        elif mode == "horizontal":
            text = f"横 16:9: 1920×1080 / 30fps に再エンコードして結合します{fit_note}"
        elif mode == "square":
            text = f"正方形 1:1: 1080×1080 / 30fps に再エンコードして結合します{fit_note}"
        else:
            text = ""
        self.concat_info_var.set(text)

    def open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def refresh_all(self) -> None:
        self.ensure_dirs()
        self.refresh_counts()
        self.refresh_clips()

    def ensure_dirs(self) -> None:
        for folder in [
            ROOT / "input" / "photos",
            ROOT / "input" / "videos",
            ROOT / "review" / "photos",
            ROOT / "review" / "frames",
            ROOT / "selected_frames",
            ROOT / "selected_clips",
            ROOT / "work",
            ROOT / "final",
            ROOT / "logs",
        ]:
            folder.mkdir(parents=True, exist_ok=True)

    def ai_scoring_done(self) -> bool:
        """AI採点が「現在の review/frames の内容」に対して実施済みかどうか。

        logs/ai_scores.csv には過去の実行結果が残ることがあるため、ファイルの
        有無だけでなく、採点されたフレームが現在も review/frames に存在するかで
        判定します（1件でも一致すれば採点済みとみなします）。
        """
        csv_path = ROOT / "logs" / "ai_scores.csv"
        if not csv_path.exists() or self.counts.get("review_frames", 0) == 0:
            return False
        frames_dir = ROOT / "review" / "frames"
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
                for row in csv.DictReader(file):
                    name = row.get("frame_name") or ""
                    if name and (frames_dir / name).exists():
                        return True
        except Exception:
            return False
        return False

    def refresh_counts(self) -> None:
        self.counts = {
            "input_photos": count_files(ROOT / "input" / "photos"),
            "input_videos": count_files(ROOT / "input" / "videos"),
            "review_photos": count_files(ROOT / "review" / "photos"),
            "review_frames": count_files(ROOT / "review" / "frames"),
            "selected_frames": count_files(ROOT / "selected_frames"),
            "selected_clips": count_files(ROOT / "selected_clips"),
            "final": count_files(ROOT / "final"),
        }
        counts = self.counts
        self.materials_var.set(f"動画 {counts['input_videos']}本 / 写真 {counts['input_photos']}枚")
        ai_state = "済" if self.ai_scoring_done() else "未"
        self.frames_status_var.set(
            f"抽出フレーム: {counts['review_frames']}枚 ／ 候補写真: {counts['review_photos']}枚"
        )
        self.select_status_var.set(f"採用 {counts['selected_frames']} / 全 {counts['review_frames']} 枚 ／ AI採点: {ai_state}")
        self.cut_status_var.set(f"切り出し済みクリップ: {counts['selected_clips']}本")
        self.update_stepper()

    def refresh_clips(self) -> None:
        self.clip_paths = sorted(
            path for path in (ROOT / "selected_clips").iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
        self.clip_durations = {path: get_duration_seconds(path) for path in self.clip_paths}
        self.redraw_clip_list()
        self.update_total_duration()
        self.update_person_totals()
        self.rebuild_name_buttons()
        self.refresh_counts()

    def redraw_clip_list(self, selected_index: int | None = None) -> None:
        self.clip_list.delete(0, tk.END)
        for index, path in enumerate(self.clip_paths):
            self.clip_list.insert(tk.END, self.clip_display_text(index, path))
        if selected_index is not None:
            self.clip_list.selection_set(selected_index)

    def clip_display_text(self, index: int, path: Path) -> str:
        duration = format_duration(self.clip_durations.get(path, 0.0))
        names = self.clip_tags.get(path.name, [])
        tag_text = f"  [{', '.join(names)}]" if names else ""
        return f"{index + 1:2d}. {path.name}    {duration}{tag_text}"

    def selected_clip_index(self) -> int | None:
        selected = self.clip_list.curselection()
        if not selected:
            return None
        index = selected[0]
        if index >= len(self.clip_paths):
            return None
        return index

    def on_clip_select(self, _event: object = None) -> None:
        index = self.selected_clip_index()
        if index is None:
            return
        clip = self.clip_paths[index]
        self.selected_clip_var.set(clip.name)
        names = self.clip_tags.get(clip.name, [])
        self.tag_entry_var.set(", ".join(names))
        self.update_clip_tags_summary(names)
        self.request_thumbnail(clip)

    def update_clip_tags_summary(self, names: list[str]) -> None:
        """工程5の読み取り専用タグ要約ラベルを更新します。"""
        if names:
            self.clip_tags_summary_var.set("登場人物: " + ", ".join(names))
        else:
            self.clip_tags_summary_var.set("登場人物: （未設定）")

    def apply_tags_from_entry(self) -> None:
        index = self.selected_clip_index()
        if index is None:
            messagebox.showwarning("タグ設定", "先にリストからクリップを選択してください。")
            return
        names = parse_person_names(self.tag_entry_var.get())
        self.set_clip_tags(index, names)

    def toggle_name_tag(self, name: str) -> None:
        index = self.selected_clip_index()
        if index is None:
            messagebox.showwarning("名前ボタン", "先にリストからクリップを選択してください。")
            return
        clip = self.clip_paths[index]
        names = list(self.clip_tags.get(clip.name, []))
        if name in names:
            names.remove(name)
        else:
            names.append(name)
        self.set_clip_tags(index, names)

    def set_clip_tags(self, index: int, names: list[str]) -> None:
        clip = self.clip_paths[index]
        if names:
            self.clip_tags[clip.name] = names
        else:
            self.clip_tags.pop(clip.name, None)
        save_clip_tags(self.clip_tags)
        self.tag_entry_var.set(", ".join(names))
        self.update_clip_tags_summary(names)
        self.redraw_clip_list(selected_index=index)
        self.update_person_totals()
        self.rebuild_name_buttons()

    def rebuild_name_buttons(self) -> None:
        for child in self.name_buttons_frame.winfo_children():
            child.destroy()
        names = sorted({name for tag_names in self.clip_tags.values() for name in tag_names})
        if not names:
            ttk.Label(self.name_buttons_frame, text="（タグ設定した名前がここに並びます）").grid(row=0, column=0, sticky=tk.W)
            return
        columns = 3
        for column in range(columns):
            self.name_buttons_frame.columnconfigure(column, weight=1)
        for position, name in enumerate(names):
            ttk.Button(
                self.name_buttons_frame,
                text=name,
                command=lambda n=name: self.toggle_name_tag(n),
            ).grid(row=position // columns, column=position % columns, padx=2, pady=2, sticky=tk.EW)

    def update_person_totals(self) -> None:
        totals: dict[str, float] = {}
        untagged = 0.0
        for path in self.clip_paths:
            duration = self.clip_durations.get(path, 0.0)
            names = self.clip_tags.get(path.name, [])
            if not names:
                untagged += duration
            for name in names:
                totals[name] = totals.get(name, 0.0) + duration
        parts = [f"{name} {format_duration(total)}" for name, total in sorted(totals.items())]
        if untagged > 0 and parts:
            parts.append(f"未タグ {format_duration(untagged)}")
        if parts:
            self.person_totals_var.set("人物別合計: " + " / ".join(parts))
        else:
            self.person_totals_var.set("人物別合計: なし")

    def request_thumbnail(self, clip: Path) -> None:
        self.thumbnail_target = clip
        cached = thumbnail_cache_path(clip)
        if cached.exists():
            self.show_thumbnail(clip, cached)
            return
        self.thumbnail_label.configure(text="サムネイル生成中...", image="")
        self.thumbnail_failed.discard(clip)
        threading.Thread(target=self._thumbnail_worker, args=(clip,), daemon=True).start()
        self._poll_thumbnail(clip, cached, attempts=150)

    def _thumbnail_worker(self, clip: Path) -> None:
        # tkinterはメインスレッド以外から触れないため、ここではファイル生成だけを行います。
        try:
            generate_thumbnail(clip, thumbnail_cache_path(clip), self.clip_durations.get(clip, 0.0))
        except Exception:
            self.thumbnail_failed.add(clip)

    def _poll_thumbnail(self, clip: Path, cached: Path, attempts: int) -> None:
        if self.thumbnail_target != clip:
            return
        if clip in self.thumbnail_failed or attempts <= 0:
            self._show_thumbnail_error(clip)
            return
        if cached.exists():
            self.show_thumbnail(clip, cached)
            return
        self.after(200, self._poll_thumbnail, clip, cached, attempts - 1)

    def _show_thumbnail_error(self, clip: Path) -> None:
        if self.thumbnail_target != clip:
            return
        self.thumbnail_label.configure(text="サムネイルを生成できません", image="")

    def show_thumbnail(self, clip: Path, png_path: Path) -> None:
        if self.thumbnail_target != clip:
            return
        try:
            image = tk.PhotoImage(file=str(png_path))
        except Exception:
            self._show_thumbnail_error(clip)
            return
        self.thumbnail_image = image
        self.thumbnail_label.configure(image=image, text="")

    def move_clip(self, direction: int) -> None:
        selected = self.clip_list.curselection()
        if not selected:
            return
        index = selected[0]
        new_index = index + direction
        if new_index < 0 or new_index >= len(self.clip_paths):
            return
        self.clip_paths[index], self.clip_paths[new_index] = self.clip_paths[new_index], self.clip_paths[index]
        self.redraw_clip_list(selected_index=new_index)
        self.update_total_duration()

    def update_total_duration(self) -> None:
        total = sum(self.clip_durations.get(path, 0.0) for path in self.clip_paths)
        if not self.clip_paths:
            self.total_duration_var.set("合計: 00:00.00")
        else:
            short_label = "ショート目安: 60秒以内" if total <= 60.0 else "ショート目安: 60秒超"
            self.total_duration_var.set(f"合計: {format_duration(total)}  /  {short_label}")
        self.update_stepper()

    def validate_number(self, label: str, value: str, allow_zero: bool) -> bool:
        try:
            number = float(value)
        except ValueError:
            messagebox.showerror("入力エラー", f"{label} は数値で入力してください: {value}")
            return False
        if allow_zero and number < 0:
            messagebox.showerror("入力エラー", f"{label} は 0 以上で入力してください: {value}")
            return False
        if not allow_zero and number <= 0:
            messagebox.showerror("入力エラー", f"{label} は 0 より大きい値で入力してください: {value}")
            return False
        return True

    def generate_candidates(self) -> None:
        if not self.validate_number("フレーム間隔", self.frame_interval.get(), allow_zero=False):
            return
        self.run_cli(["--frame-interval", self.frame_interval.get()], "候補生成中")

    def cut_from_selected_frames(self) -> None:
        for label, value in [
            ("前余白", self.pre_roll.get()),
            ("後余白", self.post_roll.get()),
            ("分割間隔", self.split_gap.get()),
        ]:
            if not self.validate_number(label, value, allow_zero=True):
                return
        args = [
            "--cut-from-selected-frames",
            "--pre-roll",
            self.pre_roll.get(),
            "--post-roll",
            self.post_roll.get(),
            "--split-gap",
            self.split_gap.get(),
        ]
        if self.fast_copy.get():
            args.append("--fast-copy")
        self.run_cli(args, "動画切り出し中")

    def cleanup_after_cut_dry_run(self) -> None:
        args = ["--cleanup-after-cut", "--dry-run"]
        if self.cleanup_review_frames.get():
            args.append("--include-review-frames")
        if self.cleanup_review_photos.get():
            args.append("--include-review-photos")
        if self.cleanup_work.get():
            args.append("--include-work")
        if self.cleanup_selected_clips.get():
            args.append("--include-selected-clips")
        self.last_action = "cleanup_dry_run"
        self.run_cli(args, "削除予定確認中")

    def cleanup_after_cut_delete(self) -> None:
        targets = ["selected_frames"]
        if self.cleanup_review_frames.get():
            targets.append("review/frames")
        if self.cleanup_review_photos.get():
            targets.append("review/photos")
        if self.cleanup_work.get():
            targets.append("work")
        if self.cleanup_selected_clips.get():
            targets.append("selected_clips")
        message = (
            f"{' と '.join(targets)} のファイルを完全削除します。\n"
            "Windowsのゴミ箱には入りません。\n"
            "元動画と input フォルダは触りません。\n\n"
            "実行しますか？"
        )
        if not messagebox.askyesno("削除確認", message):
            return
        args = ["--cleanup-after-cut", "--confirm-delete"]
        if self.cleanup_review_frames.get():
            args.append("--include-review-frames")
        if self.cleanup_review_photos.get():
            args.append("--include-review-photos")
        if self.cleanup_work.get():
            args.append("--include-work")
        if self.cleanup_selected_clips.get():
            args.append("--include-selected-clips")
        self.last_action = "cleanup_delete"
        self.run_cli(args, "削除中")

    def concat_selected_clips(self) -> None:
        if not self.clip_paths:
            messagebox.showwarning("結合", "selected_clips に動画がありません。")
            return
        order_file = ROOT / "logs" / "concat_order.txt"
        order_file.parent.mkdir(parents=True, exist_ok=True)
        with order_file.open("w", encoding="utf-8") as file:
            for clip in self.clip_paths:
                file.write(str(clip) + "\n")
        args = ["--concat-selected-clips", "--concat-order-file", str(order_file)]
        args.extend(["--normalize", self.normalize_mode.get(), "--fit", self.fit_mode.get()])
        # original選択時はチェック状態に関係なく必ず高速コピーにする。
        # 「最終サイズ=original」なのに高速コピーOFFのまま結合すると、
        # 無劣化のはずが8M再エンコードされて画質が大きく劣化する事故になるため。
        # original以外はCLI側で--fast-copyが無視されるため、従来通りチェック状態に従う。
        if self.normalize_mode.get() == "original" or self.fast_copy.get():
            args.append("--fast-copy")
        self.run_cli(args, "動画結合中")

    def run_cli(
        self,
        args: list[str],
        label: str,
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        """kite_clipper.py（exe化時はkite_clipper.exe）を args 付きで実行します。

        実行前に混在ガードを事前チェックします（_guarded）。問題なければ現在選択中の競技の
        `--sport` を自動付与して実行します。`--allow-profile-switch` を承知済みの場合はそれも
        自動付与します（3-3）。on_done を指定すると、完了時に既定の on_command_done の代わりに
        そちらを呼びます。おまかせモードの自動パイプラインで複数コマンドを連結する際に使い、
        完了処理（set_busy(False)や再読み込み）を最後の1回だけにします。
        """
        profile = self.current_profile()
        sport_id = profile.id if profile is not None else sport_profile.DEFAULT_SPORT_ID
        self._guarded(sport_id, lambda: self._run_cli_with_sport(args, label, on_done))

    def _run_cli_with_sport(
        self,
        args: list[str],
        label: str,
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        full_args = list(args)
        profile = self.current_profile()
        if profile is not None:
            full_args = ["--sport", profile.id, *full_args]
        if self.allow_profile_switch:
            full_args.append("--allow-profile-switch")
        self._run_cli_raw(full_args, label, on_done)

    def _run_cli_raw(
        self,
        args: list[str],
        label: str,
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        """混在ガードの事前チェックや `--sport` の自動付与を行わず、与えられた args を
        そのまま kite_clipper.py（exe化時は kite_clipper.exe）に渡して実行します。
        競技切り替えの整理パイプライン（_cleanup_then 系）など、既にガード解決フローの
        中にいて `--sport` / `--allow-profile-switch` を呼び出し側で組み立て済みの場合に使います。
        """
        if getattr(sys, "frozen", False):
            if not CLI_EXE.exists():
                messagebox.showerror(
                    "実行ファイルが見つかりません",
                    f"{CLI_EXE} が見つかりません。\n\n"
                    "kite_clipper_distribution フォルダごと配置して、"
                    "その中の kite_clipper_gui.exe を起動してください。",
                )
                return
            command = [str(CLI_EXE), *args]
        else:
            command = [str(PYTHON), str(ROOT / "kite_clipper.py"), *args]
        self._run_command(command, label, on_done)

    def run_frame_scorer_predict(
        self,
        label: str,
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        """frame_scorer.py predict を実行します（おまかせモードのAI採点ステップ用）。

        run_cli と同様に混在ガードを事前チェックしたうえで `--sport` を自動付与します。
        """
        profile = self.current_profile()
        sport_id = profile.id if profile is not None else sport_profile.DEFAULT_SPORT_ID
        self._guarded(sport_id, lambda: self._run_frame_scorer_predict_now(label, on_done))

    def _run_frame_scorer_predict_now(
        self,
        label: str,
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        profile = self.current_profile()
        extra = ["--allow-profile-switch"] if self.allow_profile_switch else []
        command = build_predict_command(ROOT, sport=(profile.id if profile is not None else None), extra=extra)
        self._run_command(command, label, on_done)

    def _run_command(
        self,
        command: list[str],
        label: str,
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        self.set_busy(True)
        self.status_var.set(label)
        self.append_log("> " + " ".join(command))
        callback = on_done or self.on_command_done
        thread = threading.Thread(target=self._run_cli_worker, args=(command, callback), daemon=True)
        thread.start()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        # 整理・削除ダイアログが閉じられた後のボタンなど、破棄済みウィジェットを除外します。
        self.action_buttons = [button for button in self.action_buttons if button.winfo_exists()]
        for button in self.action_buttons:
            button.configure(state=state)
        # 処理実行中は競技の切り替えを禁止します（仕様書 2.5）。
        self.sport_combo.configure(state=(tk.DISABLED if busy else "readonly"))

    def _run_cli_worker(self, command: list[str], callback: Callable[[int], None]) -> None:
        if not command:
            self.after(0, self.append_log, "ERROR: 実行コマンドが空です")
            self.after(0, callback, 1)
            return

        executable = Path(command[0])
        if not executable.is_file():
            self.after(0, self.append_log, f"ERROR: 実行ファイルが見つかりません: {executable}")
            self.after(0, callback, 1)
            return

        if (
            getattr(sys, "frozen", False)
            and executable.resolve() == Path(sys.executable).resolve()
            and (len(command) < 2 or command[1] != FRAME_SCORER_ARGV_FLAG)
        ):
            self.after(
                0,
                self.append_log,
                "ERROR: 内部サブコマンドなしでGUI自身を誤って起動しようとしました",
            )
            self.after(0, callback, 1)
            return

        try:
            # CLI(Python)の標準出力をUTF-8に固定し、ログ欄の文字化けを防ぎます。
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            assert process.stdout is not None
            output_queue: Queue[str | None] = Queue()

            def read_output() -> None:
                try:
                    for line in process.stdout:
                        output_queue.put(line)
                finally:
                    output_queue.put(None)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            last_output_at = time.monotonic()
            watchdog_triggered = False

            while True:
                try:
                    line = output_queue.get(timeout=CLI_WATCHDOG_POLL_INTERVAL_SECONDS)
                except Empty:
                    if process.poll() is not None:
                        break
                    if time.monotonic() - last_output_at < CLI_WATCHDOG_TIMEOUT_SECONDS:
                        continue

                    watchdog_triggered = True
                    self.after(
                        0,
                        self.append_log,
                        (
                            "ERROR: 一定時間、出力もプロセス終了も確認できなかったため"
                            f"処理を中止しました（{CLI_WATCHDOG_TIMEOUT_SECONDS}秒）"
                        ),
                    )
                    process.terminate()
                    try:
                        process.wait(timeout=CLI_TERMINATE_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break
                else:
                    if line is None:
                        if process.poll() is not None:
                            break
                        continue
                    last_output_at = time.monotonic()
                    self.after(0, self.append_log, line.rstrip())

            return_code = 1 if watchdog_triggered else process.wait()
            self.after(0, callback, return_code)
        except Exception as exc:
            self.after(0, self.append_log, f"ERROR: {exc}")
            self.after(0, callback, 1)

    def on_command_done(self, return_code: int) -> None:
        self.set_busy(False)
        self.status_var.set("完了" if return_code == 0 else f"エラー: {return_code}")
        self.refresh_all()
        if self.last_action == "cleanup_dry_run":
            self.show_cleanup_feedback("削除予定を確認しました")
        elif self.last_action == "cleanup_delete":
            self.show_cleanup_feedback("削除が完了しました")
        self.last_action = None

    # ------------------------------------------------------------ おまかせモード
    def on_omakase_toggle(self) -> None:
        """「おまかせモード（自動切り出し）」チェックボックスの切り替え処理です。"""
        if self.omakase_mode.get():
            if not self.current_model_path().exists():
                messagebox.showwarning(
                    "おまかせモード",
                    f"{self.current_sport_display_name()}のAI採点モデルが未学習のため"
                    "おまかせモードは使えません（WORKFLOW.md 手順4.6参照）。",
                )
                self.omakase_mode.set(False)
                return
            self.omakase_status_var.set("おまかせモード: 監視中...")
            self.append_log("おまかせモード: input/videos の監視を開始しました。")
            self._schedule_omakase_poll()
        else:
            self.omakase_status_var.set("")
            self.append_log("おまかせモード: 監視を停止しました。")
            if self.omakase_poll_after_id is not None:
                self.after_cancel(self.omakase_poll_after_id)
                self.omakase_poll_after_id = None

    def _schedule_omakase_poll(self) -> None:
        if not self.omakase_mode.get():
            return
        self.omakase_poll_after_id = self.after(OMAKASE_POLL_INTERVAL_MS, self._omakase_poll)

    def _omakase_poll(self) -> None:
        self.omakase_poll_after_id = None
        if not self.omakase_mode.get():
            return
        try:
            self._omakase_check_and_start()
        finally:
            # トグルOFFなら次のポーリングは仕掛けない（監視停止）。
            self._schedule_omakase_poll()

    def _omakase_check_and_start(self) -> None:
        """input/videos を確認し、未処理かつサイズが安定した新規動画があれば
        自動パイプラインを開始します。他のコマンド実行中(busy)なら何もせず、
        次のポーリングで再試行します。
        """
        if self.busy or self.omakase_running:
            return
        videos_dir = ROOT / "input" / "videos"
        if not videos_dir.exists():
            return

        current: dict[str, tuple[int, float]] = {}
        try:
            for path in sorted(videos_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                    stat = path.stat()
                    current[path.name] = (stat.st_size, stat.st_mtime)
        except OSError:
            return

        # 監視対象から消えたファイル（削除やリネーム）の追跡情報を掃除します。
        for name in list(self.omakase_pending_sizes.keys()):
            if name not in current:
                del self.omakase_pending_sizes[name]
        for name in list(self.omakase_failures.keys()):
            if name not in current:
                del self.omakase_failures[name]

        target_name: str | None = None
        for name, (size, _mtime) in current.items():
            if name in self.omakase_processed:
                continue
            previous = self.omakase_pending_sizes.get(name)
            if previous is not None and previous[0] == size:
                stable_count = previous[1] + 1
            else:
                stable_count = 1
            self.omakase_pending_sizes[name] = (size, stable_count)
            # 2回連続のポーリングで同じサイズ = コピー中ではないとみなします。
            if stable_count >= 2 and target_name is None:
                target_name = name

        if target_name is None:
            return

        self.omakase_pending_sizes.pop(target_name, None)
        self._omakase_start_pipeline(videos_dir / target_name)

    def _omakase_start_pipeline(self, video_path: Path) -> None:
        self.omakase_running = True
        only_video = video_path.name
        pre_roll = self.pre_roll.get() or "1"
        post_roll = self.post_roll.get() or "1"
        frame_interval = self.frame_interval.get() or "1"

        self.omakase_status_var.set(f"おまかせ: {video_path.name} を処理中...")
        self.append_log(f"おまかせモード: 新しい動画を検出しました: {video_path.name}")
        self.append_log("おまかせ: フレーム抽出中...")
        self.run_cli(
            ["--frame-interval", frame_interval, "--only-video", only_video],
            "おまかせ: フレーム抽出中...",
            on_done=lambda code: self._omakase_after_extract(video_path, code, pre_roll, post_roll),
        )

    def _omakase_after_extract(
        self, video_path: Path, return_code: int, pre_roll: str, post_roll: str
    ) -> None:
        if return_code != 0:
            self._omakase_abort(video_path, f"フレーム抽出に失敗しました（終了コード {return_code}）")
            return
        self.append_log("おまかせ: AI採点中...")
        self.run_frame_scorer_predict(
            "おまかせ: AI採点中...",
            on_done=lambda code: self._omakase_after_predict(video_path, code, pre_roll, post_roll),
        )

    def _omakase_after_predict(
        self, video_path: Path, return_code: int, pre_roll: str, post_roll: str
    ) -> None:
        if return_code != 0:
            self._omakase_abort(video_path, f"AI採点に失敗しました（終了コード {return_code}）")
            return
        if safe_stem(video_path) not in load_ai_segment_video_stems(AI_SEGMENTS_CSV):
            self.append_log(f"おまかせ: {video_path.name} は区間提案なし（切り出しスキップ）。")
            self._omakase_finish(video_path)
            return
        self.append_log("おまかせ: AI提案区間から切り出し中...")
        args = [
            "--cut-from-ai-segments",
            "--only-video",
            video_path.name,
            "--pre-roll",
            pre_roll,
            "--post-roll",
            post_roll,
        ]
        if self.fast_copy.get():
            args.append("--fast-copy")
        self.run_cli(
            args,
            "おまかせ: 切り出し中...",
            on_done=lambda code: self._omakase_after_cut(video_path, code),
        )

    def _omakase_after_cut(self, video_path: Path, return_code: int) -> None:
        if return_code != 0:
            self._omakase_abort(video_path, f"切り出しに失敗しました（終了コード {return_code}）")
            return
        self.append_log(f"おまかせ: {video_path.name} の自動切り出しが完了しました。")
        self._omakase_finish(video_path)

    def _omakase_abort(self, video_path: Path, reason: str) -> None:
        # 一時的な失敗（他コマンドとの競合など）は再試行したいので、既定では処理済みに
        # 記録せず次のポーリングに任せます。ただし同じ動画が続けて失敗する場合は恒久的な
        # エラーとみなし、上限回数で打ち切ります。
        attempts = self.omakase_failures.get(video_path.name, 0) + 1
        self.omakase_failures[video_path.name] = attempts
        self.append_log(
            f"おまかせ: {reason}（{video_path.name}）。"
            f"この動画の自動処理を中断しました。[{attempts}/{OMAKASE_MAX_ATTEMPTS}回目]"
        )
        if attempts >= OMAKASE_MAX_ATTEMPTS:
            self._record_omakase_processed(video_path)
            self.append_log(
                f"おまかせ: {video_path.name} は{OMAKASE_MAX_ATTEMPTS}回失敗したため自動処理の対象から外しました。"
                "原因を解消してからGUIを再起動すると、もう一度処理されます。"
            )
        self.omakase_running = False
        self.set_busy(False)
        self.status_var.set(f"おまかせ: エラー（{video_path.name}）")
        self.omakase_status_var.set("おまかせモード: 監視中..." if self.omakase_mode.get() else "")
        self.refresh_all()

    def _omakase_finish(self, video_path: Path) -> None:
        self.omakase_failures.pop(video_path.name, None)
        self._record_omakase_processed(video_path)
        self.omakase_running = False
        self.set_busy(False)
        self.status_var.set("待機中")
        self.omakase_status_var.set("おまかせモード: 監視中..." if self.omakase_mode.get() else "")
        self.refresh_all()

    def _record_omakase_processed(self, video_path: Path) -> None:
        try:
            stat = video_path.stat()
        except OSError:
            return
        self.omakase_processed[video_path.name] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "processed_at": time.time(),
        }
        self._save_omakase_processed()

    def _load_omakase_processed(self) -> dict[str, dict[str, float]]:
        if not OMAKASE_PROCESSED_FILE.exists():
            return {}
        try:
            with OMAKASE_PROCESSED_FILE.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        return {}

    def _save_omakase_processed(self) -> None:
        OMAKASE_PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with OMAKASE_PROCESSED_FILE.open("w", encoding="utf-8") as file:
                json.dump(self.omakase_processed, file, ensure_ascii=False, indent=2)
        except OSError as exc:
            self.append_log(f"おまかせモード: 処理済み記録の保存に失敗しました: {exc}")

    def append_log(self, message: str) -> None:
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        text = message.strip()
        if text:
            if len(text) > 90:
                text = text[:90] + "…"
            self.last_log_var.set("最終行: " + text)

    def show_cleanup_feedback(self, title: str) -> None:
        csv_path = ROOT / "logs" / "cleanup_after_cut.csv"
        count = count_cleanup_actions(csv_path)
        message = f"対象ファイル: {count} 件\n\n結果CSV:\n{csv_path}"
        messagebox.showinfo(title, message)


def parse_person_names(text: str) -> list[str]:
    names: list[str] = []
    for part in re.split(r"[,、]", text):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return names


def load_clip_tags() -> dict[str, list[str]]:
    if not CLIP_TAGS_FILE.exists():
        return {}
    try:
        data = json.loads(CLIP_TAGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    tags: dict[str, list[str]] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, list):
            names = [name for name in value if isinstance(name, str) and name.strip()]
            if names:
                tags[key] = names
    return tags


def save_clip_tags(tags: dict[str, list[str]]) -> None:
    CLIP_TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLIP_TAGS_FILE.write_text(
        json.dumps(tags, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def thumbnail_cache_path(clip: Path) -> Path:
    return THUMBNAIL_DIR / f"{clip.stem}.png"


def generate_thumbnail(clip: Path, output: Path, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    seek = max(0.0, duration * 0.5)
    command = [
        bundled_tool_path("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{seek:.3f}",
        "-i",
        ffmpeg_path(clip),
        "-frames:v",
        "1",
        "-vf",
        f"scale=w={THUMBNAIL_MAX_WIDTH}:h={THUMBNAIL_MAX_HEIGHT}:force_original_aspect_ratio=decrease",
        ffmpeg_path(output),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0 or not output.exists():
        message = result.stderr.strip() or "ffmpeg thumbnail failed"
        raise RuntimeError(message)


def count_files(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for path in folder.rglob("*") if path.is_file() and path.name != ".gitkeep")


def count_cleanup_actions(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except Exception:
        return 0
    return len(re.findall(r",(?:would_delete|deleted),", text))


def get_duration_seconds(path: Path) -> float:
    command = [
        bundled_tool_path("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        ffmpeg_path(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return 0.0
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "--:--"
    minutes = int(seconds // 60)
    remain = seconds - minutes * 60
    return f"{minutes:02d}:{remain:05.2f}"


def _run_frame_scorer(args: list[str]) -> int:
    original_argv = sys.argv
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    fallback_streams = []
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
            fallback_streams.append(sys.stdout)
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
            fallback_streams.append(sys.stderr)

        import frame_scorer

        sys.argv = ["frame_scorer", *args]
        return frame_scorer.main()
    except Exception as exc:
        print(f"frame_scorer の実行に失敗しました: {exc}", file=sys.stderr)
        return 1
    finally:
        sys.argv = original_argv
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        for stream in fallback_streams:
            stream.close()


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == FRAME_SCORER_ARGV_FLAG:
        return _run_frame_scorer(argv[1:])
    if argv:
        print(f"不明な引数です: {argv}", file=sys.stderr)
        return 2
    from video_ui import VideoOnlyApp
    app = VideoOnlyApp(ROOT, ROOT / "out")
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
