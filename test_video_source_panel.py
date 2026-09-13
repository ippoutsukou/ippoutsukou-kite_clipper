from __future__ import annotations

import gc
import time
import tkinter as tk
import unittest
from pathlib import Path

from video_session import Material
from video_source_panel import SourcePanel

SYNTHETIC = Path(__file__).parent / "out" / "ui-redesign-implementation" / "synthetic_h264_aac_01.mp4"


class SourcePanelTests(unittest.TestCase):
    def test_valid_range_callback_and_close(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk表示環境がありません: {exc}")
        root.withdraw()
        material = Material.from_path(SYNTHETIC, material_id="m-1")
        added: list[tuple[str, float, float]] = []
        panel = SourcePanel(root, Path(__file__).parent, [material],
                            lambda material_id, start, end: added.append((material_id, start, end)),
                            lambda: None)
        panel.pack(fill=tk.BOTH, expand=True)
        panel.material_list.selection_set(0)
        panel._on_material_selected()
        root.update()
        self.assertGreater(panel._duration_ms, 0)
        panel.start_var.set("0.25")
        panel.end_var.set("0.75")
        panel._add_range()
        self.assertEqual(added, [("m-1", 0.25, 0.75)])
        self.assertEqual(material.source_path, str(SYNTHETIC.resolve()))
        panel.start_var.set("NaN")
        panel.end_var.set("1")
        panel._add_range()
        self.assertEqual(len(added), 1)
        panel.start_var.set("0")
        panel.end_var.set(str(panel._duration_ms / 1000 + 1))
        panel._add_range()
        self.assertEqual(len(added), 1)
        panel.close()
        root.update()
        self.assertEqual(panel.player.mode(), "closed")
        panel.destroy()
        del panel, material
        gc.collect()
        root.update_idletasks()
        root.destroy()

    def test_play_pause_and_range_buttons(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk表示環境がありません: {exc}")
        root.withdraw()
        material = Material.from_path(SYNTHETIC, material_id="m-buttons")
        panel = SourcePanel(root, Path(__file__).parent, [material], lambda *_: None, lambda: None)
        panel.pack()
        calls: list[tuple[str, object]] = []

        class FakePlayer:
            def play(self, **kwargs):
                calls.append(("play", kwargs))
            def pause(self):
                calls.append(("pause", None))
            def resize(self, *_args):
                pass
            def close(self):
                pass
            def mode(self):
                return "closed"

        real_player = panel.player
        real_player.close()
        panel.player.close()
        panel.player = FakePlayer()
        panel.current = material
        panel._duration_ms = 2000
        panel.start_var.set("0.25")
        panel.end_var.set("0.75")
        panel.play_button.invoke()
        panel.pause_button.invoke()
        panel.range_play_button.invoke()
        self.assertEqual(calls[0], ("play", {}))
        self.assertEqual(calls[1], ("pause", None))
        self.assertEqual(calls[2], ("play", {"start_ms": 250, "end_ms": 750}))
        panel.destroy()
        del panel, real_player, material
        gc.collect()
        root.update_idletasks()
        root.destroy()


if __name__ == "__main__":
    unittest.main()
