from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from frame_selector import FrameSelectorWindow


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_base_dir()
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)
CLI_EXE = ROOT / "kite_clipper.exe"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}

# exe化したGUIから子プロセスを起動した際にコンソール窓が点滅しないようにします。
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CLIP_TAGS_FILE = ROOT / "logs" / "clip_tags.json"
THUMBNAIL_DIR = ROOT / "work" / "thumbnails"
THUMBNAIL_MAX_WIDTH = 320
THUMBNAIL_MAX_HEIGHT = 240


def bundled_tool_path(tool_name: str) -> str:
    bundled = app_base_dir() / "vendor" / "ffmpeg" / "windows-x64" / f"{tool_name}.exe"
    if bundled.exists():
        return str(bundled)
    return tool_name


def ffmpeg_path(path: Path) -> str:
    return path.resolve().as_posix()


class KiteClipperGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Media Clip Organizer")
        self.geometry("1100x820")
        self.minsize(960, 700)
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

        self.frame_selector: FrameSelectorWindow | None = None
        self.status_var = tk.StringVar(value="待機中")
        self.counts_var = tk.StringVar(value="")
        self.total_duration_var = tk.StringVar(value="合計: 00:00.00")
        # CLI実行中に無効化するボタン（多重実行の防止）
        self.action_buttons: list[ttk.Button] = []

        self.create_widgets()
        self.refresh_all()

    def create_widgets(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(main)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Media Clip Organizer", font=("", 15, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.RIGHT)

        folder_frame = ttk.LabelFrame(main, text="1. フォルダ")
        folder_frame.pack(fill=tk.X, pady=(12, 8))
        for label, folder in [
            ("input/photos", ROOT / "input" / "photos"),
            ("input/videos", ROOT / "input" / "videos"),
            ("review/photos", ROOT / "review" / "photos"),
            ("review/frames", ROOT / "review" / "frames"),
            ("selected_frames", ROOT / "selected_frames"),
            ("selected_clips", ROOT / "selected_clips"),
            ("work", ROOT / "work"),
            ("final", ROOT / "final"),
        ]:
            ttk.Button(folder_frame, text=label, command=lambda p=folder: self.open_folder(p)).pack(
                side=tk.LEFT, padx=4, pady=8
            )
        ttk.Button(folder_frame, text="再読み込み", command=self.refresh_all).pack(side=tk.RIGHT, padx=4)

        counts = ttk.Label(main, textvariable=self.counts_var)
        counts.pack(fill=tk.X, pady=(0, 8))

        generate_frame = ttk.LabelFrame(main, text="2. 候補写真と切り出しフレーム生成")
        generate_frame.pack(fill=tk.X, pady=8)
        ttk.Label(generate_frame, text="フレーム間隔(秒)").pack(side=tk.LEFT, padx=(8, 4))
        ttk.Combobox(
            generate_frame,
            textvariable=self.frame_interval,
            values=("1", "0.5", "0.25"),
            width=8,
        ).pack(side=tk.LEFT, padx=4)
        generate_button = ttk.Button(generate_frame, text="生成", command=self.generate_candidates)
        generate_button.pack(side=tk.LEFT, padx=8)
        self.action_buttons.append(generate_button)
        ttk.Button(generate_frame, text="フレーム選別", command=self.open_frame_selector).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(generate_frame, text="review/frames を開く", command=lambda: self.open_folder(ROOT / "review" / "frames")).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            generate_frame,
            text="selected_frames を開く",
            command=lambda: self.open_folder(ROOT / "selected_frames"),
        ).pack(side=tk.LEFT, padx=4)

        cut_frame = ttk.LabelFrame(main, text="3. selected_frames から動画切り出し")
        cut_frame.pack(fill=tk.X, pady=8)
        for label, var in [
            ("前余白", self.pre_roll),
            ("後余白", self.post_roll),
            ("分割間隔", self.split_gap),
        ]:
            ttk.Label(cut_frame, text=label).pack(side=tk.LEFT, padx=(8, 4))
            ttk.Entry(cut_frame, textvariable=var, width=7).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(cut_frame, text="高速コピー", variable=self.fast_copy).pack(side=tk.LEFT, padx=8)
        cut_button = ttk.Button(cut_frame, text="切り出し", command=self.cut_from_selected_frames)
        cut_button.pack(side=tk.LEFT, padx=8)
        dry_run_button = ttk.Button(cut_frame, text="削除予定を確認", command=self.cleanup_after_cut_dry_run)
        dry_run_button.pack(side=tk.LEFT, padx=4)
        delete_button = ttk.Button(cut_frame, text="selected_frames を削除", command=self.cleanup_after_cut_delete)
        delete_button.pack(side=tk.LEFT, padx=4)
        self.action_buttons.extend([cut_button, dry_run_button, delete_button])
        ttk.Checkbutton(cut_frame, text="review/framesも削除", variable=self.cleanup_review_frames).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Checkbutton(cut_frame, text="review/photosも削除", variable=self.cleanup_review_photos).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Checkbutton(cut_frame, text="workも削除", variable=self.cleanup_work).pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(cut_frame, text="selected_clipsも削除", variable=self.cleanup_selected_clips).pack(
            side=tk.LEFT, padx=8
        )

        concat_frame = ttk.LabelFrame(main, text="4. selected_clips を結合")
        concat_frame.pack(fill=tk.BOTH, expand=True, pady=8)

        normalize_frame = ttk.Frame(concat_frame)
        normalize_frame.pack(fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(normalize_frame, text="最終サイズ").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Combobox(
            normalize_frame,
            textvariable=self.normalize_mode,
            values=("vertical", "horizontal", "square", "original"),
            width=12,
            state="readonly",
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(normalize_frame, text="表示方法").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Combobox(
            normalize_frame,
            textvariable=self.fit_mode,
            values=("contain", "cover"),
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(
            normalize_frame,
            text="vertical=ショート縦 / contain=全体表示 / cover=画面いっぱい",
        ).pack(side=tk.LEFT, padx=12)

        list_area = ttk.Frame(concat_frame)
        list_area.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 選択クリップの詳細パネル（サムネイル・タグ入力・名前ボタンを1か所に集約）
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

        tag_row = ttk.Frame(detail_frame)
        tag_row.pack(fill=tk.X, padx=6, pady=(0, 2))
        ttk.Label(tag_row, text="登場人物").pack(side=tk.LEFT, padx=(0, 4))
        tag_entry = ttk.Entry(tag_row, textvariable=self.tag_entry_var)
        tag_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tag_entry.bind("<Return>", lambda _event: self.apply_tags_from_entry())
        ttk.Button(tag_row, text="タグ設定", command=self.apply_tags_from_entry).pack(side=tk.LEFT)
        ttk.Label(detail_frame, text="カンマ区切り / 空欄で設定すると解除").pack(anchor=tk.W, padx=6)

        ttk.Label(detail_frame, text="名前ボタン（クリックで付け外し）").pack(anchor=tk.W, padx=6, pady=(6, 0))
        self.name_buttons_frame = ttk.Frame(detail_frame)
        self.name_buttons_frame.pack(fill=tk.X, padx=6, pady=(2, 6))

        scrollbar = ttk.Scrollbar(list_area)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.clip_list = tk.Listbox(list_area, height=10, selectmode=tk.SINGLE)
        self.clip_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=self.clip_list.yview)
        self.clip_list.configure(yscrollcommand=scrollbar.set)
        self.clip_list.bind("<<ListboxSelect>>", self.on_clip_select)

        total_frame = ttk.Frame(concat_frame)
        total_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(total_frame, textvariable=self.total_duration_var, font=("", 11, "bold")).pack(side=tk.LEFT)
        ttk.Label(total_frame, textvariable=self.person_totals_var).pack(side=tk.LEFT, padx=(16, 0))

        order_buttons = ttk.Frame(concat_frame)
        order_buttons.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(order_buttons, text="上へ", command=lambda: self.move_clip(-1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(order_buttons, text="下へ", command=lambda: self.move_clip(1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(order_buttons, text="クリップ再読み込み", command=self.refresh_clips).pack(side=tk.LEFT, padx=4)
        concat_button = ttk.Button(order_buttons, text="結合", command=self.concat_selected_clips)
        concat_button.pack(side=tk.LEFT, padx=12)
        self.action_buttons.append(concat_button)
        ttk.Button(order_buttons, text="final を開く", command=lambda: self.open_folder(ROOT / "final")).pack(side=tk.LEFT, padx=4)

        log_frame = ttk.LabelFrame(main, text="ログ")
        log_frame.pack(fill=tk.BOTH, expand=False, pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=8, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

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

    def refresh_counts(self) -> None:
        counts = {
            "元写真": count_files(ROOT / "input" / "photos"),
            "元動画": count_files(ROOT / "input" / "videos"),
            "候補写真": count_files(ROOT / "review" / "photos"),
            "抽出フレーム": count_files(ROOT / "review" / "frames"),
            "選択フレーム": count_files(ROOT / "selected_frames"),
            "切り出し動画": count_files(ROOT / "selected_clips"),
        }
        self.counts_var.set(" / ".join(f"{name}: {count}" for name, count in counts.items()))

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
        for path in self.clip_paths:
            self.clip_list.insert(tk.END, self.clip_display_text(path))
        if selected_index is not None:
            self.clip_list.selection_set(selected_index)

    def clip_display_text(self, path: Path) -> str:
        duration = format_duration(self.clip_durations.get(path, 0.0))
        names = self.clip_tags.get(path.name, [])
        tag_text = f"  [{', '.join(names)}]" if names else ""
        return f"{path.name}    {duration}{tag_text}"

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
        self.tag_entry_var.set(", ".join(self.clip_tags.get(clip.name, [])))
        self.request_thumbnail(clip)

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
            return
        short_label = "ショート目安: 60秒以内" if total <= 60.0 else "ショート目安: 60秒超"
        self.total_duration_var.set(f"合計: {format_duration(total)}  /  {short_label}")

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
        if self.fast_copy.get():
            args.append("--fast-copy")
        self.run_cli(args, "動画結合中")

    def run_cli(self, args: list[str], label: str) -> None:
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
        self.set_busy(True)
        self.status_var.set(label)
        self.append_log("> " + " ".join(command))
        thread = threading.Thread(target=self._run_cli_worker, args=(command,), daemon=True)
        thread.start()

    def set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self.action_buttons:
            button.configure(state=state)

    def _run_cli_worker(self, command: list[str]) -> None:
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
            for line in process.stdout:
                self.after(0, self.append_log, line.rstrip())
            return_code = process.wait()
            self.after(0, self.on_command_done, return_code)
        except Exception as exc:
            self.after(0, self.append_log, f"ERROR: {exc}")
            self.after(0, self.on_command_done, 1)

    def on_command_done(self, return_code: int) -> None:
        self.set_busy(False)
        self.status_var.set("完了" if return_code == 0 else f"エラー: {return_code}")
        self.refresh_all()
        if self.last_action == "cleanup_dry_run":
            self.show_cleanup_feedback("削除予定を確認しました")
        elif self.last_action == "cleanup_delete":
            self.show_cleanup_feedback("削除が完了しました")
        self.last_action = None

    def append_log(self, message: str) -> None:
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

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


if __name__ == "__main__":
    app = KiteClipperGui()
    app.mainloop()
