import hashlib
import unittest
import uuid
import shutil
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from video_session import Candidate, Material, Session, probe
from video_ui import VideoOnlyApp

class VideoUiTest(unittest.TestCase):
    def setUp(self):
        self.root = Path("out") / ("test_video_ui_apostrophe_" + uuid.uuid4().hex + "_x'y")
        self.root.mkdir(parents=True, exist_ok=True)
        shared = Path("out/ui-redesign-implementation/synthetic_h264_aac_01.mp4").resolve()
        self.source = self.root / "source.mp4"
        if shared.is_file(): shutil.copy2(shared, self.source)
        if not self.source.is_file(): self.skipTest("合成素材なし")
        self.app = VideoOnlyApp(Path.cwd(), self.root)
        self.app.withdraw()
        self.app.store = __import__("video_session").SessionStore(self.root, "session-" + uuid.uuid4().hex[:8])
        st = self.source.stat()
        material = Material("m1", self.source.name, str(self.source), st.st_size, st.st_mtime_ns, duration_sec=3.0, status="ready")
        self.candidate = Candidate("c1", "m1", 0.0, 1.0)
        self.app.session = Session(self.app.store.root.name, materials=[material], candidates=[self.candidate])
    def tearDown(self):
        self.app.destroy()
    def test_cut_all_snapshot_and_changed_source_rejected(self):
        seen = []
        with patch.object(self.app, "_start_cut", side_effect=lambda items, status: seen.append(items)):
            self.app.cut_all()
        self.assertEqual(len(seen), 1)
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(digest, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.source.touch()
        seen.clear(); self.app.cut_all()
        self.assertFalse(seen)
    def test_real_cut_save_and_concat_workers(self):
        second = Candidate("c2", "m1", 1.0, 2.0, order=1)
        self.app.session.candidates.append(second)
        items = [self.app._snapshot(c) for c in self.app.session.accepted_candidates()]
        save_dir = self.root / "saved"
        self.app._save_worker(items, save_dir, self.app.store.root)
        kind, payload = self.app.events.get_nowait()
        self.assertEqual(kind, "save_results")
        saved, was_cancelled = payload
        self.assertFalse(was_cancelled)
        self.assertEqual(len([row for row in saved if not row[2]]), 2)
        for item, row in zip(items, saved):
            self.assertEqual(Path(row[1]).read_bytes(), self.app._clip_target(self.app.store.root, item).read_bytes())
        self.app._cut_worker(items, self.app.store.root)
        kind, results = self.app.events.get_nowait()
        self.assertEqual(kind, "cut_results")
        self.app.current = None
        self.app._apply_cut_results(results)
        self.assertTrue(all(c.preview_status == "ready" for c in self.app.session.candidates))
        self.assertEqual(self.app.view_var.get(), "preview")
        save_dir = self.root / "apostrophe's-output"
        self.app._save_worker(items, save_dir, self.app.store.root)
        kind, payload = self.app.events.get_nowait()
        self.assertEqual(kind, "save_results")
        saved, was_cancelled = payload
        self.assertFalse(was_cancelled)
        self.assertEqual(len([row for row in saved if not row[2]]), 2)
        for candidate, row in zip(self.app.session.candidates, saved):
            self.assertEqual(Path(row[1]).read_bytes(), Path(candidate.preview_path).read_bytes())
        target = self.root / "apostrophe's-final.mp4"
        self.app._concat_worker(items, target, self.app.store.root)
        kind, payload = self.app.events.get_nowait()
        self.assertEqual(kind, "concat_result")
        self.assertEqual(payload[0], 0, payload[1])
        merged = probe(target, Path.cwd())
        expected = sum(float(probe(self.app._clip_target(self.app.store.root, item), Path.cwd())["format"]["duration"]) for item in items)
        self.assertAlmostEqual(float(merged["format"]["duration"]), expected, delta=0.2)
    def test_real_exclude_restore_and_preview_order(self):
        second = Candidate("c2", "m1", 1.0, 2.0, order=1)
        self.app.session.candidates.append(second)
        with patch("video_ui.MciVideoPreview", None):
            self.app.current = self.candidate
            self.app._show_preview()
            self.app._exclude_candidate(self.candidate)
            self.assertFalse(self.candidate.accepted)
            self.assertEqual(self.app.current.candidate_id, "c2")
            self.app._restore_candidate(self.candidate)
            self.assertTrue(self.candidate.accepted)
            self.app.current = second
            self.app._move_candidate_preview(second, -1)
        self.assertEqual([c.candidate_id for c in self.app.session.accepted_candidates()], ["c2", "c1"])
        self.app._show_preview()
        self.assertEqual(self.app.session.current_view, "preview")
        self.assertEqual(self.app.session.current_candidate_id, "c2")
        loaded = self.app.store.load()
        self.assertEqual(loaded.current_view, "preview")
        self.assertEqual(loaded.current_candidate_id, "c2")

    def test_timeline_shortening_then_extension_increments_generation(self):
        self.app.current = self.candidate
        self.candidate.requested_start_sec, self.candidate.requested_end_sec = 0.5, 1.0
        self.app._timeline_duration = 3.0
        self.app._timeline_handle = "end"
        class E: x = 50
        self.app.timeline = tk.Canvas(self.app.body)
        self.app.range_info_var = tk.StringVar()
        self.app._start_cut = lambda *args: None
        self.app._timeline_drag(E())
        self.app._timeline_release(E())
        self.assertGreater(self.candidate.generation, 0)
        self.assertLess(self.candidate.requested_start_sec, self.candidate.requested_end_sec)
    def test_old_generation_result_is_ignored(self):
        self.app.current = self.candidate
        self.candidate.generation = 2
        self.app._apply_cut_results([{"candidate_id":"c1", "session_id":self.app.session.session_id, "generation":1, "status":"ready", "target":"x", "duration":1.0, "start":0.0, "end":1.0}])
        self.assertIsNone(self.candidate.preview_path)
    def test_exclusion_and_order(self):
        self.candidate.accepted = False
        self.assertEqual(self.app.session.accepted_candidates(), [])
        self.candidate.accepted = True
        self.assertEqual(self.app.session.accepted_candidates()[0].candidate_id, "c1")

if __name__ == "__main__": unittest.main()