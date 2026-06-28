from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}


class KiteClipperGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("kite_clipper")
        self.geometry("980x720")
        self.minsize(900, 620)
        self.clip_paths: list[Path] = []
        self.clip_durations: dict[Path, float] = {}

        self.frame_interval = tk.StringVar(value="1")
        self.pre_roll = tk.StringVar(value="1")
        self.post_roll = tk.StringVar(value="1")
        self.split_gap = tk.StringVar(value="2")
        self.fast_copy = tk.BooleanVar(value=False)
        self.cleanup_review_frames = tk.BooleanVar(value=False)
        self.cleanup_work = tk.BooleanVar(value=False)
        self.normalize_mode = tk.StringVar(value="vertical")
        self.fit_mode = tk.StringVar(value="contain")

        self.status_var = tk.StringVar(value="待機中")
        self.counts_var = tk.StringVar(value="")
        self.total_duration_var = tk.StringVar(value="合計: 00:00.00")

        self.create_widgets()
        self.refresh_all()

    def create_widgets(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(main)
        top.pack(fill=tk.X)
        ttk.Label(top, text="kite_clipper 操作パネル", font=("", 15, "bold")).pack(side=tk.LEFT)
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
        ttk.Button(generate_frame, text="生成", command=self.generate_candidates).pack(side=tk.LEFT, padx=8)
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
        ttk.Button(cut_frame, text="切り出し", command=self.cut_from_selected_frames).pack(side=tk.LEFT, padx=8)
        ttk.Button(cut_frame, text="削除予定を確認", command=self.cleanup_after_cut_dry_run).pack(side=tk.LEFT, padx=4)
        ttk.Button(cut_frame, text="selected_frames を削除", command=self.cleanup_after_cut_delete).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(cut_frame, text="review/framesも削除", variable=self.cleanup_review_frames).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Checkbutton(cut_frame, text="workも削除", variable=self.cleanup_work).pack(side=tk.LEFT, padx=8)

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
        self.clip_list = tk.Listbox(list_area, height=10, selectmode=tk.SINGLE)
        self.clip_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_area, command=self.clip_list.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.clip_list.configure(yscrollcommand=scrollbar.set)

        total_frame = ttk.Frame(concat_frame)
        total_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(total_frame, textvariable=self.total_duration_var, font=("", 11, "bold")).pack(side=tk.LEFT)

        order_buttons = ttk.Frame(concat_frame)
        order_buttons.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(order_buttons, text="上へ", command=lambda: self.move_clip(-1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(order_buttons, text="下へ", command=lambda: self.move_clip(1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(order_buttons, text="クリップ再読み込み", command=self.refresh_clips).pack(side=tk.LEFT, padx=4)
        ttk.Button(order_buttons, text="結合", command=self.concat_selected_clips).pack(side=tk.LEFT, padx=12)
        ttk.Button(order_buttons, text="final を開く", command=lambda: self.open_folder(ROOT / "final")).pack(side=tk.LEFT, padx=4)

        log_frame = ttk.LabelFrame(main, text="ログ")
        log_frame.pack(fill=tk.BOTH, expand=False, pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=8, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

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
        self.clip_list.delete(0, tk.END)
        for path in self.clip_paths:
            duration = format_duration(self.clip_durations.get(path, 0.0))
            self.clip_list.insert(tk.END, f"{path.name}    {duration}")
        self.update_total_duration()
        self.refresh_counts()

    def move_clip(self, direction: int) -> None:
        selected = self.clip_list.curselection()
        if not selected:
            return
        index = selected[0]
        new_index = index + direction
        if new_index < 0 or new_index >= len(self.clip_paths):
            return
        self.clip_paths[index], self.clip_paths[new_index] = self.clip_paths[new_index], self.clip_paths[index]
        self.clip_list.delete(0, tk.END)
        for path in self.clip_paths:
            self.clip_list.insert(tk.END, f"{path.name}    {format_duration(self.clip_durations.get(path, 0.0))}")
        self.clip_list.selection_set(new_index)
        self.update_total_duration()

    def update_total_duration(self) -> None:
        total = sum(self.clip_durations.get(path, 0.0) for path in self.clip_paths)
        if not self.clip_paths:
            self.total_duration_var.set("合計: 00:00.00")
            return
        short_label = "ショート目安: 60秒以内" if total <= 60.0 else "ショート目安: 60秒超"
        self.total_duration_var.set(f"合計: {format_duration(total)}  /  {short_label}")

    def generate_candidates(self) -> None:
        self.run_cli(["--frame-interval", self.frame_interval.get()], "候補生成中")

    def cut_from_selected_frames(self) -> None:
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
        if self.cleanup_work.get():
            args.append("--include-work")
        self.run_cli(args, "削除予定確認中")

    def cleanup_after_cut_delete(self) -> None:
        targets = ["selected_frames"]
        if self.cleanup_review_frames.get():
            targets.append("review/frames")
        if self.cleanup_work.get():
            targets.append("work")
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
        if self.cleanup_work.get():
            args.append("--include-work")
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
        command = [str(PYTHON), str(ROOT / "kite_clipper.py"), *args]
        self.status_var.set(label)
        self.append_log("> " + " ".join(command))
        thread = threading.Thread(target=self._run_cli_worker, args=(command,), daemon=True)
        thread.start()

    def _run_cli_worker(self, command: list[str]) -> None:
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
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
        self.status_var.set("完了" if return_code == 0 else f"エラー: {return_code}")
        self.refresh_all()

    def append_log(self, message: str) -> None:
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)


def count_files(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for path in folder.rglob("*") if path.is_file() and path.name != ".gitkeep")


def get_duration_seconds(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
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
