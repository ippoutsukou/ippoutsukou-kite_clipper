"""承認仕様に沿った動画専用Tkinterワークスペース。"""
from __future__ import annotations

import os
import math
import queue
import subprocess
import threading
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import sport_profile

from video_session import (
    Candidate, Material, Session, SessionError, SessionStore, compatible_for_concat,
    copy_file_exclusive, probe, safe_name, snap_range, stream_copy_clip, unique_output,
)

try:
    from video_preview_backend import MciVideoPreview, PreviewBackendError
except ImportError:  # pragma: no cover
    MciVideoPreview = None
    PreviewBackendError = RuntimeError


def _format_sec(value: float) -> str:
    value = max(0.0, float(value))
    return f"{int(value // 60):02d}:{value % 60:05.2f}"


class VideoOnlyApp(tk.Tk):
    """動画選択から保存までを一画面の状態機械として扱う。"""

    def __init__(self, app_root: str | Path, output_root: str | Path) -> None:
        super().__init__()
        self.app_root = Path(app_root).resolve()
        self.output_root = Path(output_root).resolve()
        self.title("Kite Clipper - 動画ワークスペース")
        self.geometry("1180x760")
        self.minsize(900, 620)
        self.store: SessionStore | None = None
        self.session: Session | None = None
        self.current: Candidate | None = None
        self.player: Any = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.cards: dict[str, tk.Widget] = {}
        self.check_vars: dict[str, tk.BooleanVar] = {}
        self.status_var = tk.StringVar(value="動画を選択してください")
        self.profile_var = tk.StringVar(value="")
        self._profiles = []
        self._profile_errors = []
        self.view_var = tk.StringVar(value="start")
        self._busy = False
        self._closed = False
        self._shutdown_done = False
        self.source_panel: Any = None
        self._cancel = threading.Event()
        self._analysis_warning = False
        self._generation_by_id: dict[str, int] = {}
        self._analysis_run_id: str | None = None
        self._analysis_material_id: str | None = None
        self._range_job: str | None = None
        self._resize_job: str | None = None
        self._drain_job: str | None = None
        self._timeline_duration = 1.0
        self._timeline_handle = "start"
        self._build()
        self._drain_job = self.after(80, self._drain_events)

    def _build(self) -> None:
        self.configure(background="#eef2f5")
        style = ttk.Style(self)
        try: style.theme_use("clam")
        except tk.TclError: pass
        style.configure("Header.TFrame", background="#ffffff")
        style.configure("Header.TLabel", background="#ffffff", foreground="#172b3a")
        style.configure("Background.TFrame", background="#eef2f5")
        style.configure("Card.TFrame", background="white", relief="groove")
        style.configure("Primary.TButton", background="#1677ee", foreground="white")
        style.map("Primary.TButton", background=[("disabled", "#b9c7d8"), ("active", "#0f62c9")], foreground=[("disabled", "#eef3f8"), ("!disabled", "white")])
        style.configure("Success.TButton", foreground="#147d55")
        header = ttk.Frame(self, padding=(18, 10), style="Header.TFrame")
        header.pack(fill=tk.X, side=tk.TOP)
        ttk.Label(header, text="Kite Clipper", font=("Meiryo UI", 16, "bold"), style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.profile_var, style="Header.TLabel").pack(side=tk.LEFT, padx=18)
        ttk.Label(header, textvariable=self.status_var, style="Header.TLabel").pack(side=tk.RIGHT)
        self.body = ttk.Frame(self, padding=(18, 14, 18, 14), style="Background.TFrame")
        self.body.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_start()

    def _close_player(self) -> None:
        player, self.player = self.player, None
        if not player:
            return
        label = getattr(player, "video_label", None)
        try:
            player.close()
        except Exception:
            pass
        if label is not None:
            try: label.destroy()
            except Exception: pass
    def _clear(self) -> None:
        panel, self.source_panel = self.source_panel, None
        if panel is not None:
            try:
                panel.close()
            except Exception:
                pass
        for attr in ("_range_job", "_resize_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        self._close_player()
        for child in self.body.winfo_children():
            child.destroy()

    def _show_start(self) -> None:
        self._clear()
        self.view_var.set("start")
        panel = ttk.LabelFrame(self.body, text="動画を選ぶ", padding=24)
        panel.pack(fill=tk.BOTH, expand=True)
        self._profiles, self._profile_errors = sport_profile.list_profiles_lenient(self.app_root)
        if self._profiles:
            self.profile_var.set("競技: " + self._profiles[0].display_name)
            self.profile_combo = ttk.Combobox(panel, values=[p.display_name + " (" + p.id + ")" for p in self._profiles], state="readonly", width=34)
            self.profile_combo.current(0)
            self.profile_combo.pack(pady=(15, 10))
            self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        else:
            detail = self._profile_errors[0][1] if self._profile_errors else "profiles/*.json がありません"
            ttk.Label(panel, text="競技プロファイルを読み込めません: " + detail[:120], foreground="#a33", wraplength=620).pack(pady=(20, 12))
            self.profile_var.set("競技プロファイルなし")
        ttk.Label(panel, text="動画を選ぶとサムネイル生成とAI候補解析を自動で開始します。", font=("Meiryo UI", 12)).pack(pady=(100, 18))
        select_button = ttk.Button(panel, text="動画を選択…", command=self.select_videos, style="Primary.TButton", state=(tk.NORMAL if self._profiles else tk.DISABLED))
        select_button.pack()
        latest = self._latest_store()
        if latest:
            ttk.Button(panel, text="前回の作業を再開",
                       command=lambda item=latest: self._resume(item)).pack(pady=12)

    def _on_profile_selected(self, _event=None) -> None:
        if not self._profiles or not hasattr(self, "profile_combo"): return
        index = self.profile_combo.current()
        if 0 <= index < len(self._profiles): self.profile_var.set("競技: " + self._profiles[index].display_name)

    def _latest_store(self) -> SessionStore | None:
        root = self.output_root / "sessions"
        if not root.is_dir():
            return None
        paths = [p for p in root.iterdir() if p.is_dir() and (p / "session.json").is_file()]
        if not paths:
            return None
        return SessionStore(self.output_root, max(paths, key=lambda p: p.stat().st_mtime).name)

    def _resume(self, store: SessionStore) -> None:
        try:
            session = store.load()
            self.store, self.session = store, session
            profile = next((p for p in self._profiles if p.id == session.sport_id), None)
            self.profile_var.set("競技: " + (profile.display_name if profile else session.sport_id))
            for material in session.materials:
                if material.changed():
                    material.status = "changed"
            accepted = session.accepted_candidates()
            self.current = next((c for c in accepted if c.candidate_id == session.current_candidate_id),
                                accepted[0] if accepted else None)
            if session.current_view == "preview" and self.current:
                self._show_preview()
            else:
                self._show_candidates()
            self.status_var.set("前回の作業を再開しました。変更素材は再指定・再解析が必要です")
        except SessionError as exc:
            self.status_var.set(f"作業を再開できません: {exc}")

    def select_videos(self) -> None:
        paths = filedialog.askopenfilenames(
            title="動画を選択",
            filetypes=[("動画", "*.mp4 *.mov *.avi *.mkv *.m4v *.wmv"), ("すべて", "*.*")]
        )
        if not paths:
            return
        self.store = SessionStore(self.output_root)
        if not getattr(self, "_profiles", None) or not hasattr(self, "profile_combo"):
            self.status_var.set("競技プロファイルがないため開始できません")
            return
        self.session = Session(self.store.root.name, sport_id=self._profiles[max(0, self.profile_combo.current())].id, current_view="analyzing")
        self.store.save(self.session)
        self._busy = True
        self._cancel.clear()
        self._analysis_warning = False
        self._analysis_run_id = uuid.uuid4().hex
        self._analysis_material_id = None
        self.status_var.set("解析中…候補を準備しています")
        self._show_analyzing()
        threading.Thread(
            target=self._analyze,
            args=(tuple(paths), self.store.root, self.session.session_id, self.session.sport_id,
                  self._analysis_run_id, None),
            daemon=True,
        ).start()

    def _show_analyzing(self) -> None:
        self._clear()
        self.view_var.set("analyzing")
        panel = ttk.LabelFrame(self.body, text="自動解析中", padding=24)
        panel.pack(fill=tk.BOTH, expand=True)
        ttk.Label(panel, text="候補を準備しています。").pack(pady=(110, 12))
        ttk.Button(panel, text="中断", command=self._cancel_processing).pack()

    def _cancel_processing(self) -> None:
        self._cancel.set()
        self.status_var.set("中断要求を受け付けました")

    def _analyze(self, paths: tuple[str, ...], session_root: Path,
                 session_id: str, sport_id: str, run_id: str | None = None,
                 material_id: str | None = None) -> None:
        run_id = run_id or uuid.uuid4().hex
        try:
            from video_analysis import analyze_material
        except Exception:
            analyze_material = None
        for path in paths:
            if self._cancel.is_set():
                break
            current_material_id = material_id
            try:
                material = Material.from_path(path, material_id=current_material_id)
                current_material_id = material.material_id
                data = probe(path, self.app_root)
                material.duration_sec = float((data.get("format") or {}).get("duration") or 0.0)
                material.streams = {"streams": data.get("streams", [])}
                material.status = "ready"
                self.events.put(("material", (session_id, run_id, current_material_id, material)))
                if analyze_material is None:
                    self.events.put(("warning", (session_id, run_id, current_material_id,
                                                 "AIモデルが未準備です。手動候補追加を利用してください")))
                    continue
                try:
                    result = analyze_material(path, self.app_root, session_root, sport_id,
                                              cancel_event=self._cancel)
                except TypeError:
                    result = analyze_material(path, self.app_root, session_root, sport_id)
                raw = result.get("candidates", []) if isinstance(result, dict) else getattr(result, "candidates", result)
                for index, item in enumerate(raw or []):
                    if self._cancel.is_set():
                        break
                    if isinstance(item, dict):
                        start = float(item.get("start_sec", item.get("start", 0)))
                        end = float(item.get("end_sec", item.get("end", 0)))
                        thumb = item.get("representative_thumbnail_path")
                    else:
                        start = float(getattr(item, "start_sec", 0))
                        end = float(getattr(item, "end_sec", 0))
                        thumb = None
                    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                        continue
                    candidate = Candidate(
                        f"c-{material.material_id}-{index + 1}-{uuid.uuid4().hex[:6]}",
                        material.material_id, start, end, order=index,
                    )
                    if thumb and Path(thumb).is_file():
                        candidate.thumbnail_path = str(Path(thumb).resolve())
                    self.events.put(("candidate", (session_id, run_id, current_material_id, candidate)))
                if isinstance(result, dict) and result.get("status") in {"no_model", "no_candidates", "error"}:
                    self.events.put(("warning", (session_id, run_id, current_material_id,
                                                 result.get("error") or "AI候補がありません。手動候補追加を利用してください")))
            except Exception as exc:
                self.events.put(("error", (session_id, run_id, current_material_id, str(exc))))
        self.events.put(("analysis_done", (session_id, run_id, material_id)))
    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind in {"material", "candidate", "warning", "error"}:
                    if not isinstance(value, tuple) or len(value) != 4:
                        continue
                    event_session, event_run, event_material, payload = value
                    if not self.session or event_session != self.session.session_id:
                        continue
                    if self._analysis_run_id is not None and event_run != self._analysis_run_id:
                        continue
                    if (self._analysis_material_id is not None
                            and event_material != self._analysis_material_id):
                        continue
                    value = payload
                elif kind == "analysis_done":
                    if not isinstance(value, tuple) or len(value) != 3:
                        continue
                    event_session, event_run, event_material = value
                    if not self.session or event_session != self.session.session_id:
                        continue
                    if self._analysis_run_id is not None and event_run != self._analysis_run_id:
                        continue
                    if (self._analysis_material_id is not None
                            and event_material != self._analysis_material_id):
                        continue
                if kind == "material" and self.session and self.store:
                    for index, existing in enumerate(self.session.materials):
                        if existing.material_id == value.material_id:
                            self.session.materials[index] = value
                            break
                    else:
                        self.session.materials.append(value)
                    self.store.save(self.session)
                elif kind == "candidate" and self.session and self.store:
                    if all(c.candidate_id != value.candidate_id for c in self.session.candidates):
                        value.order = max((c.order for c in self.session.candidates), default=-1) + 1
                        self.session.candidates.append(value)
                        self._generation_by_id[value.candidate_id] = value.generation
                    self.store.save(self.session)
                elif kind == "analysis_done":
                    self._busy = False
                    if self.session and self.session.session_id == event_session:
                        self.session.current_view = "candidates"
                        if self.store:
                            self.store.save(self.session)
                        self._show_candidates()
                    has_candidates = bool(self.session and self.session.candidates)
                    self.status_var.set("候補を確認し、切り出し実行へ進んでください"
                                        if has_candidates else "AI候補なし。手動候補追加を利用してください")
                elif kind == "warning":
                    self._analysis_warning = True
                    self.status_var.set(str(value))
                elif kind == "error":
                    # 複数素材解析中はworker全体が完了するまでbusyを維持する。
                    self.status_var.set(f"処理エラー: {value}")
                elif kind == "cut_results":
                    self._apply_cut_results(value)
                elif kind == "save_results":
                    self._busy = False
                    results, cancelled = value if isinstance(value, tuple) else (value, False)
                    failed = [item for item in results if item[2]]
                    suffix = "／中断しました" if cancelled else (f"／失敗{len(failed)}件" if failed else "")
                    self.status_var.set(f"{len(results)-len(failed)}件保存完了" + suffix)
                elif kind == "concat_result":
                    self._busy = False
                    code, detail = value
                    self.status_var.set("結合動画を保存しました" if code == 0 else f"結合できません: {detail}")
                    if hasattr(self, "merge_button") and code != 0:
                        self.merge_button.configure(state=tk.NORMAL)
        except queue.Empty:
            pass
        if not self._closed:
            self._drain_job = self.after(80, self._drain_events)
        else:
            self._drain_job = None

    def _show_candidates(self) -> None:
        self._clear()
        self.view_var.set("candidates")
        if self.session:
            self.session.current_view = "candidates"
            self.session.current_candidate_id = self.current.candidate_id if self.current else None
            if self.store: self.store.save(self.session)
        ttk.Label(self.body, text="AI候補（仮採用）", font=("Meiryo UI", 14, "bold")).pack(anchor=tk.W)
        area = ttk.Frame(self.body); area.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(area, highlightthickness=0)
        bar = ttk.Scrollbar(area, orient=tk.VERTICAL, command=canvas.yview)
        bar.pack(side=tk.RIGHT, fill=tk.Y); canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        canvas.configure(yscrollcommand=bar.set)
        holder = ttk.Frame(canvas); self.candidate_holder = holder
        window = canvas.create_window((0, 0), window=holder, anchor="nw")
        holder.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(window, width=e.width), self._reflow_cards(e.width)))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))
        holder.columnconfigure(0, weight=1); holder.columnconfigure(1, weight=1)
        self.cards.clear(); self.check_vars.clear()
        if self.session:
            for idx, candidate in enumerate(sorted(self.session.candidates, key=lambda c: c.order)):
                self._add_card(candidate, idx // 2, idx % 2)
        footer = ttk.Frame(self.body); footer.pack(fill=tk.X, pady=8)
        ttk.Button(footer, text="すべての動画から手動追加…", command=self.manual_add).pack(side=tk.LEFT)
        count = len(self.session.accepted_candidates()) if self.session else 0
        ttk.Label(footer, text=f"採用中の{count}候補を保存").pack(side=tk.LEFT)
        ttk.Button(footer, text=f"採用中の{count}候補を切り出し実行",
                   command=self.cut_all, style="Primary.TButton").pack(side=tk.RIGHT)

    def _reflow_cards(self, width: int) -> None:
        holder = getattr(self, "candidate_holder", None)
        if holder is None: return
        columns = 2 if width < 1050 else (3 if width < 1350 else 4)
        for index, candidate in enumerate(sorted((self.session.candidates if self.session else []), key=lambda c: c.order)):
            card = self.cards.get(candidate.candidate_id)
            if card is not None: card.grid_configure(row=index // columns, column=index % columns)

    def _add_card(self, candidate: Candidate, row: int = 0, column: int = 0) -> None:
        holder = getattr(self, "candidate_holder", None)
        if holder is None or not self.session or candidate.candidate_id in self.cards:
            return
        material = self.session.material(candidate.material_id)
        card = ttk.Frame(holder, padding=8, style="Card.TFrame", relief=tk.GROOVE, borderwidth=1)
        card.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        var = tk.BooleanVar(value=candidate.accepted)
        self.check_vars[candidate.candidate_id] = var
        var.trace_add("write", lambda *_: self._toggle(candidate, var.get()))
        thumb: tk.Widget
        if candidate.thumbnail_path and Path(candidate.thumbnail_path).is_file():
            try:
                image = tk.PhotoImage(file=candidate.thumbnail_path)
                scale = max(1, (image.width() + 239) // 240)
                card.thumb_image = image.subsample(scale, scale)
                thumb = ttk.Label(card, image=card.thumb_image, cursor="hand2")
            except tk.TclError:
                thumb = ttk.Label(card, text="動画プレビュー", anchor=tk.CENTER, cursor="hand2")
        else:
            thumb = ttk.Label(card, text="動画プレビュー", anchor=tk.CENTER, cursor="hand2")
        thumb.pack(fill=tk.X, ipady=8)
        thumb.bind("<Button-1>", lambda _e, c=candidate: self._open_preview(c))
        ttk.Label(card, text=material.display_name, anchor=tk.W).pack(fill=tk.X)
        ttk.Label(card, text=f"{_format_sec(candidate.requested_start_sec)} - "
                  f"{_format_sec(candidate.requested_end_sec)}  "
                  f"({_format_sec(candidate.requested_end_sec-candidate.requested_start_sec)})",
                  anchor=tk.W).pack(fill=tk.X)
        actions = ttk.Frame(card); actions.pack(fill=tk.X, pady=(4, 0))
        ttk.Checkbutton(actions, text="採用", variable=var).pack(side=tk.LEFT)
        ttk.Button(actions, text="↑", width=3,
                   command=lambda c=candidate: self._move_candidate(c, -1)).pack(side=tk.RIGHT)
        ttk.Button(actions, text="↓", width=3,
                   command=lambda c=candidate: self._move_candidate(c, 1)).pack(side=tk.RIGHT)
        self.cards[candidate.candidate_id] = card

    def _toggle(self, candidate: Candidate, accepted: bool) -> None:
        if self._busy:
            return
        candidate.accepted = bool(accepted)
        if self.store and self.session:
            self.store.save(self.session)

    def _move_candidate(self, candidate: Candidate, delta: int) -> None:
        if self._busy or not self.session or not self.store:
            return
        ordered = sorted(self.session.candidates, key=lambda item: item.order)
        try:
            index = ordered.index(candidate)
        except ValueError:
            return
        target = index + delta
        if 0 <= target < len(ordered):
            ordered[index], ordered[target] = ordered[target], ordered[index]
            for number, item in enumerate(ordered):
                item.order = number
            self.store.save(self.session)
            self._show_candidates()

    def _snapshot(self, candidate: Candidate) -> dict[str, Any]:
        if not self.session:
            raise SessionError("作業セッションがありません")
        material = self.session.material(candidate.material_id)
        if material.changed():
            raise SessionError(f"元動画が変更されています。再指定・再解析してください: {material.display_name}")
        try:
            stat = Path(material.source_path).stat()
        except OSError as exc:
            raise SessionError(f"元動画が見つかりません: {material.display_name}") from exc
        return {
            "session_id": self.session.session_id, "candidate_id": candidate.candidate_id,
            "material_id": candidate.material_id, "source_path": material.source_path,
            "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns,
            "display_name": material.display_name, "start": candidate.requested_start_sec,
            "end": candidate.requested_end_sec, "generation": candidate.generation,
            "persons": list(candidate.persons), "tricks": list(candidate.tricks),
        }

    @staticmethod
    def _source_current(item: dict[str, Any]) -> bool:
        try:
            stat = Path(item["source_path"]).stat()
        except OSError:
            return False
        return stat.st_size == item["source_size"] and stat.st_mtime_ns == item["source_mtime_ns"]

    def _clip_target(self, root: Path, item: dict[str, Any]) -> Path:
        return root / "clips" / (
            f"{safe_name(Path(item['display_name']).stem)}_{item['candidate_id']}_g{item['generation']}.mp4"
        )

    def cut_all(self) -> None:
        if self._busy or not self.session or not self.store:
            return
        candidates = self.session.accepted_candidates()
        if not candidates:
            self.status_var.set("採用中の候補がありません。手動追加または復帰を利用してください")
            return
        try:
            items = [self._snapshot(c) for c in candidates]
        except SessionError as exc:
            self.status_var.set(str(exc)); return
        self._start_cut(items, "採用候補を切り出し中…")

    def _open_preview(self, candidate: Candidate) -> None:
        self.current = candidate
        self._show_preview()
        if candidate.preview_status != "ready" or not candidate.preview_path or not Path(candidate.preview_path).is_file():
            try:
                self._start_cut([self._snapshot(candidate)], "プレビュー準備中…")
            except SessionError as exc:
                self.status_var.set(str(exc))

    def _start_cut(self, items: list[dict[str, Any]], status: str) -> None:
        if self._busy or not self.store:
            return
        self._busy = True; self._cancel.clear(); self.status_var.set(status)
        threading.Thread(target=self._cut_worker, args=(items, self.store.root), daemon=True).start()

    def _cut_worker(self, items: list[dict[str, Any]], session_root: Path) -> None:
        results = []
        for item in items:
            if self._cancel.is_set():
                break
            result = {"session_id": item["session_id"], "candidate_id": item["candidate_id"], "generation": item["generation"]}
            try:
                if not self._source_current(item):
                    raise SessionError("元動画が変更されています。再指定・再解析してください")
                start, end = snap_range(item["source_path"], item["start"], item["end"], self.app_root)
                target = self._clip_target(session_root, item)
                if target.is_file():
                    data = probe(target, self.app_root)
                else:
                    stream_copy_clip(item["source_path"], target, start, end, self.app_root)
                    data = probe(target, self.app_root)
                duration = float((data.get("format") or {}).get("duration") or 0.0)
                if duration <= 0:
                    raise SessionError("切り出し結果の再生時間を検証できません")
                result.update(status="ready", target=str(target), start=start, end=end, duration=duration)
            except Exception as exc:
                result.update(status="error", error=str(exc))
            results.append(result)
        self.events.put(("cut_results", results))

    def _apply_cut_results(self, results: list[dict[str, Any]]) -> None:
        if not self.session or not self.store:
            return
        by_id = {c.candidate_id: c for c in self.session.candidates}
        for item in results:
            candidate = by_id.get(item.get("candidate_id"))
            if not candidate or item.get("session_id") != self.session.session_id or candidate.generation != item.get("generation"):
                continue
            candidate.preview_status = item["status"]; candidate.error = item.get("error")
            if item["status"] == "ready":
                candidate.preview_path = item["target"]
                candidate.preview_duration_sec = item["duration"]
                candidate.effective_start_sec = item["start"]
                candidate.effective_end_sec = item["end"]
                candidate.preview_generation = item["generation"]
        self._busy = False
        self.store.save(self.session)
        self._refresh_merge_state()
        if self.current is None:
            ready = [c for c in self.session.accepted_candidates() if c.preview_status == "ready"]
            self.current = ready[0] if ready else (self.session.accepted_candidates()[0] if self.session.accepted_candidates() else None)
        if self.current:
            self._show_preview()
        else:
            self._show_candidates()

    def _refresh_merge_state(self) -> None:
        if not self.session:
            return
        candidates = self.session.accepted_candidates()
        if not candidates or any(c.preview_status != "ready" or not c.preview_path for c in candidates):
            self._set_merge_reason("")
            return
        ok, reason = compatible_for_concat([Path(c.preview_path) for c in candidates], self.app_root)
        self._set_merge_reason("" if ok else reason)

    def _show_preview(self) -> None:
        if not self.session or not self.current:
            return
        self._clear(); self.view_var.set("preview")
        self.session.current_view = "preview"
        self.session.current_candidate_id = self.current.candidate_id
        if self.store: self.store.save(self.session)
        outer = ttk.Frame(self.body); outer.pack(fill=tk.BOTH, expand=True); outer.columnconfigure(1, weight=1); outer.rowconfigure(0, weight=1)
        left = ttk.Frame(outer, width=230); left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        ttk.Label(left, text="候補（採用順）").pack(anchor=tk.W)
        list_area = ttk.Frame(left); list_area.pack(fill=tk.BOTH, expand=True)
        list_canvas = tk.Canvas(list_area, width=230, highlightthickness=0)
        list_bar = ttk.Scrollbar(list_area, orient=tk.VERTICAL, command=list_canvas.yview)
        list_bar.pack(side=tk.RIGHT, fill=tk.Y); list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_canvas.configure(yscrollcommand=list_bar.set)
        list_holder = ttk.Frame(list_canvas)
        list_window = list_canvas.create_window((0, 0), window=list_holder, anchor="nw")
        list_holder.bind("<Configure>", lambda _e: list_canvas.configure(scrollregion=list_canvas.bbox("all")))
        list_canvas.bind("<Configure>", lambda e: list_canvas.itemconfigure(list_window, width=e.width))
        list_canvas.bind("<MouseWheel>", lambda e: list_canvas.yview_scroll(-int(e.delta / 120), "units"))
        def label_for(candidate: Candidate) -> str:
            material = self.session.material(candidate.material_id)
            return (f"{material.display_name}  "
                    f"{_format_sec(candidate.requested_start_sec)}-{_format_sec(candidate.requested_end_sec)}")
        for candidate in self.session.accepted_candidates():
            row = ttk.Frame(list_holder); row.pack(fill=tk.X, pady=2); row.columnconfigure(0, weight=1)
            ttk.Button(row, text=label_for(candidate),
                       command=lambda c=candidate: self._select_preview(c)).grid(row=0, column=0, sticky="ew")
            ttk.Button(row, text="除外", width=4,
                       command=lambda c=candidate: self._exclude_candidate(c)).grid(row=0, column=1, sticky="e")
            ttk.Button(row, text="↑", width=3,
                       command=lambda c=candidate: self._move_candidate_preview(c, -1)).grid(row=0, column=2, sticky="e")
            ttk.Button(row, text="↓", width=3,
                       command=lambda c=candidate: self._move_candidate_preview(c, 1)).grid(row=0, column=3, sticky="e")
        excluded = [c for c in self.session.candidates if not c.accepted]
        if excluded:
            ttk.Label(list_holder, text="除外した候補").pack(anchor=tk.W, pady=(14, 3))
            for candidate in excluded:
                ttk.Button(list_holder, text=f"復帰 {label_for(candidate)}",
                           command=lambda c=candidate: self._restore_candidate(c)).pack(fill=tk.X, pady=2)
        right = ttk.Frame(outer); right.grid(row=0, column=1, sticky="nsew"); right.rowconfigure(0, weight=1); right.columnconfigure(0, weight=1)
        self.video_host = tk.Frame(right, background="black", height=240)
        self.video_host.grid(row=0, column=0, sticky="nsew")
        self.video_host.bind("<Configure>", self._on_video_resize)
        info = ttk.Frame(right); info.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self.range_info_var = tk.StringVar()
        ttk.Label(info, textvariable=self.range_info_var).pack(side=tk.LEFT)
        ttk.Button(info, text="タグ…", command=self._tags).pack(side=tk.RIGHT)
        self.timeline = tk.Canvas(right, height=62, background="#20242a", highlightthickness=0)
        self.timeline.grid(row=2, column=0, sticky="ew", pady=5)
        self.timeline.bind("<Configure>", lambda _e: self._draw_handles())
        self.timeline.bind("<Button-1>", self._timeline_press)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)
        controls = ttk.Frame(right); controls.grid(row=3, column=0, sticky="ew")
        ttk.Button(controls, text="再生", command=self._play).pack(side=tk.LEFT)
        ttk.Button(controls, text="一時停止", command=self._pause).pack(side=tk.LEFT, padx=4)
        ttk.Button(controls, text="候補一覧へ戻る", command=self._show_candidates).pack(side=tk.LEFT, padx=10)
        self.merge_reason_var = tk.StringVar()
        ttk.Label(right, textvariable=self.merge_reason_var, foreground="#a33").grid(row=4, column=0, sticky="ew")
        footer = ttk.Frame(right); footer.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(footer, text="個別ファイルで保存", command=self.save_individual, style="Primary.TButton").pack(side=tk.RIGHT, padx=4)
        self.merge_button = ttk.Button(footer, text="1本にまとめて保存", command=self.save_concat, style="Primary.TButton")
        self.merge_button.pack(side=tk.RIGHT)
        if self._busy:
            for child in controls.winfo_children(): child.configure(state=tk.DISABLED)
            self.merge_button.configure(state=tk.DISABLED)
        self._select_preview(self.current)
        self._refresh_merge_state()

    def _select_preview(self, candidate: Candidate) -> None:
        if not self.session:
            return
        self.current = candidate
        material = self.session.material(candidate.material_id)
        if material.changed():
            self.status_var.set("元動画が変更されています。再指定・再解析してください")
            return
        duration = max(0.1, material.duration_sec or candidate.requested_end_sec)
        self._timeline_duration = duration
        if hasattr(self, "timeline"):
            self._draw_timeline()
            start = candidate.effective_start_sec if candidate.effective_start_sec is not None else candidate.requested_start_sec
            end = candidate.effective_end_sec if candidate.effective_end_sec is not None else candidate.requested_end_sec
            self.range_info_var.set(f"実効範囲 {_format_sec(start)} - {_format_sec(end)}（元動画 {_format_sec(duration)}）")
        self._close_player()
        path = candidate.preview_path if candidate.preview_status == "ready" and candidate.preview_path else material.source_path
        try:
            if not MciVideoPreview:
                raise PreviewBackendError("動画再生基盤がありません")
            self.player = MciVideoPreview(self.video_host, app_root=self.app_root,
                                          on_error=lambda msg: self.status_var.set(f"再生エラー: {msg}"))
            self.player.open(path)
            if path == material.source_path and candidate.requested_start_sec > 0 and hasattr(self.player, "seek"):
                self.player.seek(int(candidate.requested_start_sec * 1000))
            self.player.play()
            self._resize_job = self.after(0, self._resize_player)
        except Exception as exc:
            self.status_var.set(f"この動画を再生できません: {exc}")

    def _resize_player(self) -> None:
        if not getattr(self, "player", None): return
        width = max(1, self.video_host.winfo_width())
        height = max(1, self.video_host.winfo_height())
        if hasattr(self.player, "resize"):
            try: self.player.resize(width, height)
            except Exception: pass

    def _on_video_resize(self, _event: Any) -> None:
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(80, self._resize_player)

    def _play(self) -> None:
        if self.player: self.player.play()

    def _pause(self) -> None:
        if self.player: self.player.pause()

    def _draw_timeline(self) -> None:
        if not hasattr(self, "timeline"): return
        self.timeline.delete("all")
        width = max(240, self.timeline.winfo_width() or 720)
        duration = max(0.1, self._timeline_duration)
        self.timeline.create_rectangle(14, 25, width - 14, 35, fill="#66717d", outline="")
        self.timeline.create_text(14, 9, text="元動画全範囲（短縮後も拡張できます）",
                                  anchor=tk.W, fill="white")
        self.timeline.create_text(width - 14, 51, text=_format_sec(duration),
                                  anchor=tk.E, fill="white")
        self._draw_handles()

    def _draw_handles(self) -> None:
        if not hasattr(self, "timeline") or not self.current: return
        width = max(240, self.timeline.winfo_width() or 720)
        start = max(0.0, min(self._timeline_duration, self.current.requested_start_sec))
        end = max(start + 0.01, min(self._timeline_duration, self.current.requested_end_sec))
        x1 = 14 + (width - 28) * start / self._timeline_duration
        x2 = 14 + (width - 28) * end / self._timeline_duration
        self.timeline.delete("range")
        self.timeline.create_rectangle(x1, 20, x2, 40, fill="#37a6d9", outline="", tags="range")
        self.timeline.create_oval(x1-7, 17, x1+7, 43, fill="#f0c44f", outline="", tags="range")
        self.timeline.create_oval(x2-7, 17, x2+7, 43, fill="#f0c44f", outline="", tags="range")
        self._timeline_x = (x1, x2)

    def _timeline_value(self, x: float) -> float:
        width = max(240, self.timeline.winfo_width() or 720)
        return max(0.0, min(self._timeline_duration,
                            (x - 14) / max(1, width - 28) * self._timeline_duration))

    def _timeline_press(self, event: Any) -> None:
        if self._busy or not self.current: return
        x1, x2 = getattr(self, "_timeline_x", (14, 14))
        self._timeline_handle = "start" if abs(event.x-x1) <= abs(event.x-x2) else "end"
        self._timeline_drag(event)

    def _timeline_drag(self, event: Any) -> None:
        if self._busy or not self.current: return
        value = self._timeline_value(event.x)
        start, end = self.current.requested_start_sec, self.current.requested_end_sec
        if self._timeline_handle == "start":
            start = min(value, end - 0.01)
        else:
            end = max(value, start + 0.01)
        self.current.requested_start_sec, self.current.requested_end_sec = start, end
        self._draw_handles()
        self.range_info_var.set(f"要望範囲 {_format_sec(start)} - {_format_sec(end)}（離すと更新）")

    def _timeline_release(self, _event: Any) -> None:
        if self._busy or not self.current: return
        try:
            self.current.set_requested_range(self.current.requested_start_sec, self.current.requested_end_sec)
            if self.store and self.session: self.store.save(self.session)
            self._start_cut([self._snapshot(self.current)], "プレビュー更新中…")
        except SessionError as exc:
            self.status_var.set(str(exc))

    def _exclude_candidate(self, candidate: Candidate) -> None:
        if self._busy or not self.session or not self.store:
            return
        candidate.accepted = False
        self.store.save(self.session)
        remaining = self.session.accepted_candidates()
        self.current = remaining[0] if remaining else None
        self._show_preview() if self.current else self._show_candidates()

    def _move_candidate_preview(self, candidate: Candidate, delta: int) -> None:
        if self._busy or not self.session or not self.store:
            return
        ordered = sorted(self.session.candidates, key=lambda c: c.order)
        try:
            index = ordered.index(candidate)
        except ValueError:
            return
        target = index + delta
        if 0 <= target < len(ordered):
            ordered[index], ordered[target] = ordered[target], ordered[index]
            for number, item in enumerate(ordered):
                item.order = number
            self.store.save(self.session)
            self._show_preview()
    def _restore_candidate(self, candidate: Candidate) -> None:
        if self._busy: return
        candidate.accepted = True
        if self.store and self.session: self.store.save(self.session)
        self._show_preview()

    def _tags(self) -> None:
        if not self.current or not self.session: return
        self._clear(); self.view_var.set("tags")
        ttk.Label(self.body, text="タグ（任意）", font=("Meiryo UI", 14, "bold")).pack(anchor=tk.W)
        ttk.Label(self.body, text="人物・トリックは入力中も自動保存されます").pack(anchor=tk.W)
        people = tk.StringVar(value=", ".join(self.current.persons))
        tricks = tk.StringVar(value=", ".join(self.current.tricks))
        for label, variable in (("人物（カンマ区切り）", people), ("トリック（カンマ区切り）", tricks)):
            ttk.Label(self.body, text=label).pack(anchor=tk.W, pady=(12, 2))
            entry = ttk.Entry(self.body, textvariable=variable, width=60); entry.pack(anchor=tk.W)
            entry.bind("<KeyRelease>", lambda _e: self._save_tags(people, tricks))
        ttk.Button(self.body, text="保存してプレビューへ戻る",
                   command=lambda: self._save_tags(people, tricks, True)).pack(anchor=tk.W, pady=18)

    def _save_tags(self, people: tk.StringVar, tricks: tk.StringVar, back: bool = False) -> None:
        if not self.current: return
        self.current.persons = [x.strip() for x in people.get().split(",") if x.strip()]
        self.current.tricks = [x.strip() for x in tricks.get().split(",") if x.strip()]
        if self.store and self.session: self.store.save(self.session)
        if back: self._show_preview()

    def _on_manual_panel_add(self, material_id: str, start: float, end: float) -> None:
        if not self.session or not self.store:
            return
        material = self.session.material(material_id)
        try:
            start, end = float(start), float(end)
            if (not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start
                    or (material.duration_sec and end > material.duration_sec)):
                raise SessionError("元動画の長さ内で開始<終了を指定してください")
            candidate = Candidate("manual-" + uuid.uuid4().hex, material_id, start, end,
                                  order=len(self.session.candidates))
            self.session.candidates.append(candidate)
            self.current = candidate
            self.session.current_view = "preview"
            self.session.current_candidate_id = candidate.candidate_id
            self.store.save(self.session)
            self._show_preview()
            self._start_cut([self._snapshot(candidate)], "手動候補を準備中…")
        except (ValueError, SessionError) as exc:
            self.status_var.set(str(exc))
    def _relocate_material(self, material_id: str, path: str) -> None:
        if not self.session or not self.store:
            return
        try:
            replacement = Material.from_path(path, material_id=material_id)
            data = probe(path, self.app_root)
            replacement.duration_sec = float((data.get("format") or {}).get("duration") or 0.0)
            replacement.streams = {"streams": data.get("streams", [])}
            replacement.status = "ready"
            for index, material in enumerate(self.session.materials):
                if material.material_id == material_id:
                    self.session.materials[index] = replacement
                    break
            self.session.candidates = [c for c in self.session.candidates if c.material_id != material_id]
            if self.current and self.current.material_id == material_id:
                self.current = None
            self.store.save(self.session)
            self.status_var.set("素材を再指定しました。再解析中…")
            self._reanalyze_material(material_id)
        except Exception as exc:
            self.status_var.set(f"素材を再指定できません: {exc}")

    def _reanalyze_material(self, material_id: str) -> None:
        if self._busy or not self.session or not self.store:
            return
        try:
            material = self.session.material(material_id)
            self.session.candidates = [c for c in self.session.candidates if c.material_id != material_id]
            self.current = None if self.current and self.current.material_id == material_id else self.current
            self.store.save(self.session)
            self._busy = True
            self._cancel.clear()
            self._analysis_warning = False
            self._analysis_run_id = uuid.uuid4().hex
            self._analysis_material_id = material_id
            run_id = self._analysis_run_id
            threading.Thread(target=self._analyze,
                             args=((material.source_path,), self.store.root,
                                   self.session.session_id, self.session.sport_id,
                                   run_id, material_id),
                             daemon=True).start()
            self._show_analyzing()
        except SessionError as exc:
            self.status_var.set(str(exc))
    def manual_add(self) -> None:
        if not self.session or not self.store or not self.session.materials:
            return
        try:
            from video_source_panel import SourcePanel
        except ImportError:
            SourcePanel = None
        if SourcePanel is not None:
            self._clear(); self.view_var.set("manual")
            panel = SourcePanel(self.body, app_root=self.app_root, materials=self.session.materials,
                                on_add=self._on_manual_panel_add, on_back=self._show_candidates,
                                on_relocate=self._relocate_material,
                                on_reanalyze=self._reanalyze_material)
            self.source_panel = panel
            panel.pack(fill=tk.BOTH, expand=True)
            return
        dialog = tk.Toplevel(self); dialog.title("候補を手動追加"); dialog.transient(self)
        material_ids = [m.material_id for m in self.session.materials]
        material_var = tk.StringVar(value=material_ids[0])
        start_var, end_var = tk.StringVar(value="0"), tk.StringVar(value="1")
        ttk.Label(dialog, text="元動画").pack(anchor=tk.W, padx=12, pady=(12, 2))
        ttk.Combobox(dialog, textvariable=material_var, values=material_ids,
                     state="readonly", width=50).pack(padx=12)
        ttk.Label(dialog, text="開始秒").pack(anchor=tk.W, padx=12, pady=(8, 2))
        ttk.Entry(dialog, textvariable=start_var).pack(padx=12)
        ttk.Label(dialog, text="終了秒").pack(anchor=tk.W, padx=12, pady=(8, 2))
        ttk.Entry(dialog, textvariable=end_var).pack(padx=12)
        def add() -> None:
            try:
                start, end = float(start_var.get()), float(end_var.get())
                if (not math.isfinite(start) or not math.isfinite(end)
                        or start < 0 or end <= start):
                    raise ValueError("開始<終了で指定してください")
                material = self.session.material(material_var.get())
                if material.duration_sec and end > material.duration_sec:
                    raise ValueError("元動画の長さを超えています")
                candidate = Candidate("manual-" + uuid.uuid4().hex, material_var.get(), start, end,
                                      order=len(self.session.candidates))
            except (ValueError, SessionError) as exc:
                messagebox.showerror("範囲エラー", str(exc), parent=dialog); return
            self.session.candidates.append(candidate); self.store.save(self.session)
            dialog.destroy(); self._show_candidates()
        ttk.Button(dialog, text="候補へ追加", command=add).pack(pady=12)

    def _save_snapshot_clip(self, item: dict[str, Any], session_root: Path) -> tuple[Path | None, str | None]:
        if not self._source_current(item):
            return None, "元動画が変更されています。再指定・再解析してください"
        target = self._clip_target(session_root, item)
        if target.is_file():
            return target, None
        try:
            start, end = snap_range(item["source_path"], item["start"], item["end"], self.app_root)
            stream_copy_clip(item["source_path"], target, start, end, self.app_root)
            probe(target, self.app_root)
            return target, None
        except Exception as exc:
            return None, str(exc)

    def save_individual(self) -> None:
        if self._busy or not self.session or not self.store:
            return
        directory = filedialog.askdirectory(title="保存先フォルダーを選択")
        if not directory:
            return
        candidates = self.session.accepted_candidates()
        try:
            items = [self._snapshot(c) for c in candidates]
        except SessionError as exc:
            self.status_var.set(str(exc)); return
        if not items:
            self.status_var.set("採用中の候補がありません"); return
        self._busy = True; self._cancel.clear()
        self.status_var.set(f"採用中の{len(items)}候補を保存中…")
        threading.Thread(target=self._save_worker,
                         args=(items, Path(directory), self.store.root), daemon=True).start()

    def _save_worker(self, items: list[dict[str, Any]], directory: Path, session_root: Path) -> None:
        results = []
        for item in items:
            if self._cancel.is_set(): break
            source, error = self._save_snapshot_clip(item, session_root)
            if error or source is None:
                results.append((item["candidate_id"], "", error or "準備に失敗しました")); continue
            tags = "_".join(safe_name(x) for x in item["persons"] + item["tricks"])
            target = unique_output(directory, source.stem + (f"_{tags}" if tags else ""), source.suffix)
            try:
                copy_file_exclusive(source, target)
                results.append((item["candidate_id"], str(target), None))
            except Exception as exc:
                results.append((item["candidate_id"], "", str(exc)))
        self.events.put(("save_results", (results, self._cancel.is_set())))

    def _set_merge_reason(self, reason: str) -> None:
        if hasattr(self, "merge_reason_var"):
            self.merge_reason_var.set(reason)
        if hasattr(self, "merge_button"):
            self.merge_button.configure(state=tk.DISABLED if reason else tk.NORMAL)

    def save_concat(self) -> None:
        if self._busy or not self.session or not self.store:
            return
        candidates = self.session.accepted_candidates()
        if not candidates:
            self.status_var.set("採用中の候補がありません"); return
        try:
            items = [self._snapshot(c) for c in candidates]
        except SessionError as exc:
            self.status_var.set(str(exc)); return
        target_name = filedialog.asksaveasfilename(
            title="結合動画を保存", defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
        if not target_name:
            return
        target = Path(target_name)
        if target.exists():
            self.status_var.set("既存ファイルを上書きしません"); return
        self._busy = True; self._cancel.clear()
        self.status_var.set(f"採用中の{len(items)}候補を準備・結合中…")
        threading.Thread(target=self._concat_worker,
                         args=(items, target, self.store.root), daemon=True).start()

    def _concat_worker(self, items: list[dict[str, Any]], target: Path, session_root: Path) -> None:
        try:
            paths: list[Path] = []
            for item in items:
                if self._cancel.is_set():
                    self.events.put(("concat_result", (1, "中断しました")))
                    return
                path, error = self._save_snapshot_clip(item, session_root)
                if error or path is None: raise SessionError(error or "準備に失敗しました")
                paths.append(path)
            ok, reason = compatible_for_concat(paths, self.app_root)
            if not ok:
                self.events.put(("concat_result", (1, reason))); return
            list_file = session_root / ("concat-" + uuid.uuid4().hex + ".txt")
            def quote(path: Path) -> str:
                return path.as_posix().replace("\\", "/").replace("'", "'\\''")
            list_file.write_text("".join(f"file '{quote(path)}'{chr(10)}" for path in paths),
                                  encoding="utf-8")
            ffmpeg = self.app_root / "vendor" / "ffmpeg" / "windows-x64" / "ffmpeg.exe"
            ffmpeg = ffmpeg if ffmpeg.is_file() else Path("ffmpeg")
            partial = target.with_name(target.stem + ".partial-" + uuid.uuid4().hex + target.suffix)
            result = subprocess.run(
                [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-n", "-f", "concat",
                 "-safe", "0", "-i", str(list_file), "-map", "0", "-c", "copy", str(partial)],
                capture_output=True, text=True, timeout=300, check=False)
            if result.returncode or not partial.is_file() or partial.stat().st_size == 0:
                self.events.put(("concat_result", (1, result.stderr.strip() or "結合に失敗しました"))); return
            probe(partial, self.app_root)
            try:
                os.rename(partial, target)
            except FileExistsError:
                self.events.put(("concat_result", (1, "既存ファイルを上書きしません"))); return
            self.events.put(("concat_result", (0, "")))
        except Exception as exc:
            self.events.put(("concat_result", (1, str(exc))))

    def _shutdown_resources(self) -> None:
        if self._shutdown_done:
            return
        self._cancel.set()
        self._closed = True
        for attr in ("_drain_job", "_range_job", "_resize_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        panel, self.source_panel = self.source_panel, None
        if panel is not None:
            try:
                panel.close()
            except Exception:
                pass
            try:
                panel.destroy()
            except Exception:
                pass
        self._close_player()
        self.cards.clear()
        self.check_vars.clear()
        # Tk変数のデストラクタがroot破棄後にTclへ触れないよう先に参照を解放する。
        for attr in ("status_var", "view_var", "range_info_var", "merge_reason_var"):
            if hasattr(self, attr):
                setattr(self, attr, None)
        self._shutdown_done = True

    def destroy(self) -> None:
        self._shutdown_resources()
        try:
            super().destroy()
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        self.destroy()


def main(app_root: str | Path, output_root: str | Path) -> int:
    app = VideoOnlyApp(app_root, output_root)
    app.mainloop()
    return 0
