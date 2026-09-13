from __future__ import annotations

import gc
import multiprocessing as mp
import sys
import time
import unittest
from pathlib import Path

from video_preview_backend import MciVideoPreview


SYNTHETIC = Path(__file__).parent / "out" / "ui-redesign-implementation" / "synthetic_h264_aac_01.mp4"


class FakeParent:
    def __init__(self) -> None:
        self.after_calls = 0
        self.errors: list[str] = []

    def after(self, _delay: int, _callback, *_args) -> None:
        self.after_calls += 1

    def on_error(self, message: str) -> None:
        self.errors.append(message)


def _probe_child(path: str, result: mp.Queue) -> None:
    class Parent:
        def after(self, *_args):
            pass
    try:
        preview = MciVideoPreview(Parent())
        preview.open(path)
        preview.play()
        time.sleep(0.15)
        preview.close()
        result.put(True)
    except Exception as exc:
        result.put(exc)


def _tk_preview_child(path: str, result: mp.Queue) -> None:
    import gc
    import tkinter as tk
    try:
        root = tk.Tk()
        root.withdraw()
        host = tk.Frame(root, width=640, height=360)
        host.pack()
        preview = MciVideoPreview(host)
        preview.resize(640, 360)
        preview.open(path)
        preview.play()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not getattr(preview.video_label, "image", None):
            root.update()
            time.sleep(0.01)
        if getattr(preview.video_label, "image", None) is None:
            raise AssertionError("フレームが表示されません")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and preview.position_ms() <= 0:
            root.update()
            time.sleep(0.01)
        if preview.position_ms() <= 0:
            raise AssertionError("再生位置が進みません")
        preview.pause()
        paused = preview.position_ms()
        for _ in range(5):
            root.update()
            time.sleep(0.02)
        if preview.position_ms() != paused:
            raise AssertionError("pause後に位置が進みました")
        old_image = preview.video_label.image
        if preview.seek(500) != 500:
            raise AssertionError("seekに失敗しました")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            root.update()
            if preview.video_label.image is not old_image:
                break
            time.sleep(0.01)
        if preview.video_label.image is old_image:
            raise AssertionError("seek後フレームが更新されません")
        preview.open(path)
        preview.play()
        root.update()
        if preview.mode() != "playing":
            raise AssertionError("再open後に再生されません")
        preview.close()
        root.update_idletasks()
        label = preview.video_label
        if label is not None:
            label.configure(image="")
            label.image = None
            label.destroy()
        preview._label = None
        preview._photo = None
        old_image = None
        root.update_idletasks()
        gc.collect()
        del label, preview
        gc.collect()
        root.update_idletasks()
        root.destroy()
        result.put(True)
    except Exception as exc:
        try:
            root.destroy()
        except Exception:
            pass
        result.put(repr(exc))
        raise

class VideoPreviewTests(unittest.TestCase):
    def test_synthetic_controls(self) -> None:
        parent = FakeParent()
        preview = MciVideoPreview(parent)
        preview.open(SYNTHETIC)
        self.assertGreater(preview.duration_ms(), 0)
        self.assertEqual(preview.mode(), "stopped")
        self.assertEqual(preview._frames.maxsize, 3)

        preview.play()
        time.sleep(0.25)
        self.assertGreater(preview.position_ms(), 0)
        preview.pause()
        paused = preview.position_ms()
        self.assertEqual(preview.mode(), "paused")
        time.sleep(0.1)
        self.assertEqual(preview.position_ms(), paused)

        self.assertEqual(preview.seek(500), 500)
        self.assertEqual(preview.position_ms(), 500)
        self.assertEqual(preview.mode(), "paused")
        preview.resume()
        time.sleep(0.05)
        self.assertGreaterEqual(preview.position_ms(), 500)
        preview.close()
        self.assertEqual(preview.mode(), "closed")

    def test_play_restarts_after_eof(self) -> None:
        parent = FakeParent()
        preview = MciVideoPreview(parent)
        preview.open(SYNTHETIC)
        preview.play(end_ms=250)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            preview._poll()
            if preview.mode() == "stopped":
                break
            time.sleep(0.02)
        self.assertEqual(preview.mode(), "stopped")
        self.assertGreaterEqual(preview.position_ms(), 200)
        preview.play()
        time.sleep(0.05)
        self.assertLess(preview.position_ms(), preview.duration_ms())
        preview.close()

    def test_parent_process_timeout(self) -> None:
        if not getattr(sys.modules["__main__"], "__file__", ""):
            self.skipTest("親スクリプトなし")
        result: mp.Queue = mp.Queue()
        child = mp.Process(target=_probe_child, args=(str(SYNTHETIC), result))
        child.start()
        child.join(timeout=12)
        if child.is_alive():
            child.terminate()
            child.join(timeout=2)
            self.fail("動画プレビューの子プロセスがタイムアウトしました")
        self.assertEqual(child.exitcode, 0)
        self.assertTrue(result.get(timeout=2))

    def test_tk_frame_pause_seek_switch_and_close(self) -> None:
        result: mp.Queue = mp.Queue()
        child = mp.Process(target=_tk_preview_child, args=(str(SYNTHETIC), result))
        child.start()
        child.join(timeout=20)
        if child.is_alive():
            child.terminate()
            child.join(timeout=2)
            self.fail("Tkプレビュー子プロセスがタイムアウトしました")
        self.assertEqual(child.exitcode, 0)
        self.assertTrue(result.get(timeout=3))


if __name__ == "__main__":
    unittest.main()
