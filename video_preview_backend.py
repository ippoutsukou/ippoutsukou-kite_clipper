"""Tk に埋め込む FFmpeg 動画プレビュー。"""
from __future__ import annotations
import json, math, os, queue, subprocess, sys, threading, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from wave_audio import WaveOut

class PreviewBackendError(RuntimeError):
    """プレビューの入力または再生エラー。"""

@dataclass
class _Frame:
    generation: int
    data: bytes
    width: int
    height: int

class _Playback:
    def __init__(self, generation: int, source: Path, width: int, height: int,
                 start_ms: int, end_ms: int | None, static: bool, has_audio: bool):
        self.generation, self.source = generation, source
        self.width, self.height = width, height
        self.start_ms, self.end_ms = start_ms, end_ms
        self.static, self.has_audio = static, has_audio
        self.stop_event = threading.Event()
        self.video: subprocess.Popen[bytes] | None = None
        self.audio: subprocess.Popen[bytes] | None = None
        self.wave_out: WaveOut | None = None
        self.started_at: float | None = None
        self.audio_thread: threading.Thread | None = None
        self.stderr_video = self.stderr_audio = b""
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()

class MciVideoPreview:
    """open/play/pause/resume/seek/position_ms/duration_ms/mode/resize/close API."""
    _FPS, _POLL_MS, _FRAME_QUEUE_SIZE = 30.0, 15, 3

    def __init__(self, parent: Any, *, alias: str = "kite_clip_preview",
                 app_root: str | Path | None = None,
                 on_frame: Callable[[Any], None] | None = None,
                 on_state: Callable[[str], None] | None = None,
                 on_error: Callable[[str], None] | None = None):
        self.parent, self.alias = parent, alias
        self.app_root = Path(app_root).resolve() if app_root else None
        self.on_frame, self.on_state, self.on_error = on_frame, on_state, on_error
        self._path: Path | None = None
        self._opened, self._state = False, "closed"
        self._duration = self._position = self._width = self._height = 0
        self._display_size: tuple[int, int] | None = None
        self._has_audio = False
        self._end_position: int | None = None
        self._generation, self._active = 0, None
        self._lock = threading.RLock()
        self._frames: queue.Queue[_Frame] = queue.Queue(self._FRAME_QUEUE_SIZE)
        self._events: queue.Queue[tuple[Any, ...]] = queue.Queue(32)
        self._ui_thread, self._polling = threading.get_ident(), False
        self._poll_id, self._poll_enabled = None, True
        self._photo, self._last_error, self._label = None, None, None
        self._video = self._audio = None
        self._make_label()
        self._schedule_poll()

    def _make_label(self):
        try:
            import tkinter as tk
            self._label = tk.Label(self.parent, background="black", anchor="center")
            self._label.pack(fill=tk.BOTH, expand=True)
        except (AttributeError, ImportError, RuntimeError, TypeError):
            self._label = None

    def _schedule_poll(self):
        if self._polling or not self._poll_enabled or not hasattr(self.parent, "after"):
            return
        self._polling = True
        try:
            self._poll_id = self.parent.after(self._POLL_MS, self._poll)
        except TypeError:
            self._poll_id = self.parent.after(self._POLL_MS, self._poll, None)
        except Exception:
            self._poll_enabled, self._polling = False, False

    def _poll(self, _unused=None):
        self._poll_id, self._polling = None, False
        if not self._poll_enabled:
            return
        if threading.get_ident() != self._ui_thread:
            self._queue_event(("error", self._generation, "Tk更新がメインスレッドから呼ばれました"))
            return
        latest = None
        try:
            while True:
                latest = self._frames.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self._show_frame(latest)
        try:
            while True:
                self._handle_event(self._events.get_nowait())
        except queue.Empty:
            pass
        self._schedule_poll()

    def _handle_event(self, event):
        kind, generation = event[0], event[1]
        with self._lock:
            if generation != self._generation or not self._opened:
                return
            if kind == "ended":
                self._position = min(self._duration, int(event[2]))
                if self._state == "playing":
                    self._state = "stopped"
                if self._active and self._active.generation == generation:
                    self._active = None
                state = self._state
            elif kind == "error":
                self._last_error, self._state = str(event[2]), "error"
                state = "error"
            else:
                return
        self._emit_state(state)
        if state == "error":
            self._emit_error(self._last_error or "動画再生に失敗しました")

    def _show_frame(self, frame: _Frame):
        with self._lock:
            if not self._opened or frame.generation != self._generation:
                return
        if self._label is None and self.on_frame is None:
            return
        try:
            import tkinter as tk
            ppm = f"P6\n{frame.width} {frame.height}\n255\n".encode() + frame.data
            self._photo = tk.PhotoImage(data=ppm, format="PPM")
            if self._label is not None:
                self._label.configure(image=self._photo)
                self._label.image = self._photo
            if self.on_frame:
                self.on_frame(self._photo)
        except Exception as exc:
            self._queue_event(("error", frame.generation, f"映像フレーム表示失敗: {exc}"))

    def _queue_frame(self, frame):
        try:
            self._frames.put_nowait(frame)
        except queue.Full:
            try: self._frames.get_nowait()
            except queue.Empty: pass
            try: self._frames.put_nowait(frame)
            except queue.Full: pass

    def _queue_event(self, event):
        try:
            self._events.put_nowait(event)
        except queue.Full:
            try: self._events.get_nowait()
            except queue.Empty: pass
            try: self._events.put_nowait(event)
            except queue.Full: pass

    def _emit_state(self, state):
        if self.on_state: self.on_state(state)

    def _emit_error(self, message):
        callback = self.on_error or getattr(self.parent, "on_error", None)
        if callable(callback): callback(message)

    def _tool(self, name):
        exe = name if name.lower().endswith(".exe") else name + ".exe"
        roots = ([self.app_root] if self.app_root else [])
        frozen = getattr(sys, "_MEIPASS", None)
        if frozen: roots.append(Path(frozen))
        roots += [Path(__file__).resolve().parent, Path(sys.executable).resolve().parent]
        for root in roots:
            candidate = root / "vendor" / "ffmpeg" / "windows-x64" / exe
            if candidate.is_file(): return str(candidate)
        return name

    def _probe(self, path):
        try:
            result = subprocess.run(
                [self._tool("ffprobe"), "-v", "error",
                 "-show_entries", "format=duration:stream=codec_type,width,height:stream_tags=rotate:stream_side_data=rotation",
                 "-of", "json", str(path)], capture_output=True, text=True,
                timeout=15, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            data, streams = json.loads(result.stdout), None
            streams = data.get("streams") or []
            video = next(s for s in streams if s.get("codec_type") == "video")
            w, h = int(video["width"]), int(video["height"])
            duration = float((data.get("format") or {}).get("duration") or 0)
            if w <= 0 or h <= 0 or not math.isfinite(duration) or duration < 0:
                raise ValueError("動画情報が不正です")
            rotation = int((video.get("tags") or {}).get("rotate") or 0)
            for side in video.get("side_data_list") or []:
                if side.get("rotation") is not None:
                    rotation = int(side["rotation"]); break
            if abs(rotation) % 180 == 90: w, h = h, w
            return w, h, round(duration * 1000), any(s.get("codec_type") == "audio" for s in streams)
        except (OSError, subprocess.SubprocessError, StopIteration, KeyError,
                TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PreviewBackendError(f"動画情報を取得できません: {exc}") from exc

    def _ensure(self):
        if not self._opened or self._path is None:
            raise PreviewBackendError("動画プレビューが開かれていません")

    def open(self, path):
        source = Path(path).resolve()
        if not source.is_file(): raise PreviewBackendError(f"動画が見つかりません: {source}")
        w, h, duration, audio = self._probe(source)
        self.close()
        self._poll_enabled = True
        self._schedule_poll()
        with self._lock:
            self._path, self._opened, self._state = source, True, "stopped"
            self._width, self._height, self._duration, self._has_audio = w, h, duration, audio
            self._position, self._end_position, self._last_error = 0, None, None
        self._emit_state("ready")

    def _fit_size(self, width=None, height=None):
        width, height = width or self._width, height or self._height
        if not self._display_size: return width, height
        scale = min(self._display_size[0] / width, self._display_size[1] / height)
        return max(1, round(width * scale)), max(1, round(height * scale))

    def resize(self, width, height):
        if isinstance(width, bool) or isinstance(height, bool) or int(width) <= 0 or int(height) <= 0:
            raise PreviewBackendError("表示サイズは正の整数で指定してください")
        self._display_size = int(width), int(height)
        if self._label is not None:
            try:
                w, h = self._fit_size(); self._label.configure(width=w, height=h)
            except Exception as exc: self._emit_error(f"映像領域の変更に失敗しました: {exc}")

    def _clear_frames(self):
        try:
            while True: self._frames.get_nowait()
        except queue.Empty: pass

    def _next_generation(self):
        with self._lock: self._generation += 1; generation = self._generation
        self._clear_frames(); return generation

    def _request_stop(self, playback):
        if playback is None: return
        playback.stop_event.set()
        with playback.lock: processes = (playback.video, playback.audio)
        for process in processes:
            if process is not None and process.poll() is None:
                try: process.terminate()
                except OSError: pass
        for process in processes:
            if process is not None and process.poll() is None:
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try: process.kill()
                    except OSError: pass
        for thread in (playback.thread, playback.audio_thread):
            if thread and thread is not threading.current_thread(): thread.join(timeout=2)

    def _start(self, start, end, static):
        self._ensure()
        old = self._active; self._active = None; self._request_stop(old)
        generation = self._next_generation()
        playback = _Playback(generation, self._path, self._width, self._height, start, end, static,
                             self._has_audio and not static)
        playback.started_at = time.monotonic()
        with self._lock:
            self._active, self._position = playback, start
            self._state = "paused" if static else "playing"
        playback.thread = threading.Thread(target=self._decode, args=(playback,),
                                           name=f"{self.alias}-{generation}", daemon=True)
        playback.thread.start()

    def play(self, *, start_ms=None, end_ms=None):
        self._ensure()
        with self._lock:
            requested_start = self._position if start_ms is None else int(start_ms)
            if start_ms is None and (requested_start >= self._duration or
                                       (self._end_position is not None and requested_start >= self._end_position)):
                requested_start = 0
            start = max(0, min(requested_start, self._duration))
            end = self._end_position if end_ms is None else int(end_ms)
            if end is not None:
                end = max(0, min(end, self._duration))
                if end <= start: raise PreviewBackendError("終了位置は開始位置より後にしてください")
            self._end_position = end
        self._start(start, end, False); self._emit_state("playing")

    def pause(self):
        self._ensure()
        with self._lock:
            if self._state != "playing": return
            self._position = self.position_ms()
            old, self._active, self._state = self._active, None, "paused"
        self._next_generation(); self._request_stop(old); self._emit_state("paused")

    def resume(self):
        self._ensure()
        with self._lock:
            start = 0 if self._position >= self._duration else self._position
            end = self._end_position
        self._start(start, end, False); self._emit_state("playing")

    def stop(self):
        with self._lock:
            old, self._active = self._active, None
            if self._opened and self._state == "playing": self._state = "stopped"
        self._next_generation(); self._request_stop(old)

    def seek(self, position_ms):
        self._ensure()
        if isinstance(position_ms, bool): raise PreviewBackendError("シーク位置が不正です")
        try: position = max(0, min(int(position_ms), self._duration))
        except (TypeError, ValueError) as exc: raise PreviewBackendError("シーク位置が不正です") from exc
        with self._lock:
            old, self._active, self._position, self._end_position = self._active, None, position, None
        self._next_generation(); self._request_stop(old); self._start(position, None, True)
        return position

    def position_ms(self):
        with self._lock:
            playback = self._active
            if self._state != "playing" or playback is None or playback.started_at is None: return self._position
            elapsed = max(0, int((time.monotonic() - playback.started_at) * 1000))
            if playback.wave_out is not None:
                try:
                    device_ms = playback.wave_out.position_ms()
                    if device_ms > 0: elapsed = device_ms
                except (OSError, RuntimeError): pass
            limit = playback.end_ms if playback.end_ms is not None else self._duration
            return max(self._position, min(limit, playback.start_ms + elapsed))

    def duration_ms(self): return self._duration if self._opened else 0
    def mode(self): return self._state if self._opened else "closed"
    @property
    def path(self): return self._path
    @property
    def video_label(self): return self._label

    @staticmethod
    def _read_exact(stream, size):
        data = bytearray()
        while len(data) < size:
            chunk = stream.read(size - len(data))
            if not chunk: return None
            data.extend(chunk)
        return bytes(data)

    def _common(self, playback):
        args = [self._tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-ss", f"{playback.start_ms / 1000:.6f}", "-i", str(playback.source)]
        if playback.end_ms is not None: args += ["-t", f"{max(0, playback.end_ms - playback.start_ms) / 1000:.6f}"]
        return args

    @staticmethod
    def _drain_stderr(process, playback, attr):
        if process.stderr is None: return
        chunks, stored = [], 0
        try:
            while True:
                chunk = process.stderr.read(8192)
                if not chunk: break
                if stored < 65536:
                    keep = chunk[:65536 - stored]
                    chunks.append(keep); stored += len(keep)
        except (OSError, ValueError): return
        setattr(playback, attr, b"".join(chunks))

    def _decode(self, playback):
        w, h = self._fit_size(playback.width, playback.height)
        video = audio = None
        try:
            args = self._common(playback) + ["-an", "-vf", f"fps={self._FPS:g},scale={w}:{h}:flags=lanczos",
                                               "-f", "rawvideo", "-pix_fmt", "rgb24"]
            if playback.static: args += ["-frames:v", "1"]
            args += ["pipe:1"]; flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            video = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
            with playback.lock: playback.video = video
            threading.Thread(target=self._drain_stderr, args=(video, playback, "stderr_video"), daemon=True).start()
            if playback.has_audio:
                aargs = self._common(playback) + ["-vn", "-ac", "2", "-ar", "44100", "-f", "s16le", "pipe:1"]
                audio = subprocess.Popen(aargs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
                with playback.lock: playback.audio = audio
                threading.Thread(target=self._drain_stderr, args=(audio, playback, "stderr_audio"), daemon=True).start()
                playback.audio_thread = threading.Thread(target=self._audio_loop, args=(playback, audio),
                                                         name=f"{self.alias}-audio-{playback.generation}", daemon=True)
                playback.audio_thread.start()
            count, size = 0, w * h * 3
            while not playback.stop_event.is_set() and video.stdout is not None:
                frame = self._read_exact(video.stdout, size)
                if frame is None: break
                elapsed = time.monotonic() - playback.started_at
                if playback.wave_out is not None:
                    try:
                        device_ms = playback.wave_out.position_ms()
                        if device_ms > 0: elapsed = device_ms / 1000
                    except (OSError, RuntimeError): pass
                delay = count / self._FPS - elapsed
                if delay > 0 and playback.stop_event.wait(delay): break
                self._queue_frame(_Frame(playback.generation, frame, w, h)); count += 1
            code = video.wait(timeout=2)
            if playback.audio_thread and not playback.stop_event.is_set():
                playback.audio_thread.join(timeout=max(3, (playback.end_ms or self._duration) / 1000 + 2))
                if playback.audio_thread.is_alive():
                    self._queue_event(("error", playback.generation, "音声再生が終了しませんでした"))
                    playback.stop_event.set()
                    if audio is not None and audio.poll() is None:
                        try: audio.terminate()
                        except OSError: pass
            stderr = playback.stderr_video.decode("utf-8", "replace").strip()
            if code and not playback.stop_event.is_set():
                raise PreviewBackendError(stderr or f"FFmpeg映像デコード失敗 ({code})")
            if not playback.stop_event.is_set() and not playback.static:
                end = playback.end_ms if playback.end_ms is not None else self._duration
                self._queue_event(("ended", playback.generation, min(self._duration, end)))
        except (OSError, subprocess.SubprocessError, PreviewBackendError, ValueError) as exc:
            if not playback.stop_event.is_set(): self._queue_event(("error", playback.generation, str(exc)))
        finally:
            if playback.static: playback.stop_event.set()
            self._close_process(video)
            if playback.audio_thread is None: self._close_process(audio)
            with self._lock:
                if self._active is playback:
                    self._active = None if playback.static else self._active
                    self._video = self._audio = None

    def _audio_loop(self, playback, process):
        output = None
        try:
            if os.name == "nt":
                output = WaveOut(sample_rate=44100, channels=2)
                with playback.lock: playback.wave_out = output
            while not playback.stop_event.is_set() and process.stdout is not None:
                pcm = process.stdout.read(64 * 1024)
                if not pcm: break
                if output is not None: output.write(pcm, cancel_event=playback.stop_event)
            if output is not None and not playback.stop_event.is_set(): output.drain(cancel_event=playback.stop_event)
            code = process.wait(timeout=2)
            if code and not playback.stop_event.is_set():
                detail = playback.stderr_audio.decode("utf-8", "replace").strip()
                raise PreviewBackendError(detail or f"FFmpeg音声デコード失敗 ({code})")
        except InterruptedError:
            if not playback.stop_event.is_set(): self._queue_event(("error", playback.generation, "音声再生がキャンセルされました"))
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, PreviewBackendError) as exc:
            if not playback.stop_event.is_set(): self._queue_event(("error", playback.generation, f"音声再生失敗: {exc}"))
        finally:
            if output is not None:
                try: output.close()
                except (OSError, RuntimeError): pass
            self._close_process(process)

    @staticmethod
    def _close_process(process):
        if process is None: return
        if process.poll() is None:
            try:
                process.terminate(); process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try: process.kill(); process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired): pass
            except OSError: pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try: stream.close()
                except (OSError, ValueError): pass

    def close(self):
        self._poll_enabled = False
        after_id, self._poll_id = self._poll_id, None
        self._polling = False
        if hasattr(self.parent, "after_cancel"):
            if after_id is not None:
                try: self.parent.after_cancel(after_id)
                except Exception: pass
            # closeとpollの境界でIDがNoneになったcallbackも掃除する。
            try: self.parent.after_cancel(self._poll)
            except Exception: pass
        with self._lock:
            old, self._active = self._active, None
            self._generation += 1
            self._opened, self._path, self._state = False, None, "closed"
            self._position = self._duration = 0
        self._clear_frames()
        try:
            while True:
                self._events.get_nowait()
        except queue.Empty:
            pass
        self._request_stop(old); self._emit_state("closed")
        if self._label is not None:
            try:
                self._label.configure(image="")
                self._label.image = None
            except Exception:
                pass
        self._photo = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()

__all__ = ["MciVideoPreview", "PreviewBackendError"]
