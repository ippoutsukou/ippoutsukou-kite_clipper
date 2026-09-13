from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import uuid
import cv2
import numpy as np
import video_analysis


class FakeModel:
    def predict_proba(self, vectors):
        self.shape = vectors.shape
        scores = np.array([0.1, 0.9, 0.9])[:len(vectors)]
        return np.column_stack([1.0 - scores, scores])


class VideoAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.root = Path("out/test_video_analysis") / uuid.uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        self.source = self.root / "synthetic.mp4"
        writer = cv2.VideoWriter(str(self.source), cv2.VideoWriter_fourcc(*"mp4v"), 1.0, (320, 180))
        for value in (40, 180, 220):
            writer.write(np.full((180, 320, 3), value, dtype=np.uint8))
        writer.release()
        self.profile = SimpleNamespace(equipment_detector="kite_hsv_upper", equipment_detector_params={}, id="kite",
            cv={"frame_interval": 1.0, "blur_threshold": 0, "dark_threshold": 0, "min_width": 1, "min_height": 1},
            segment={"enter": 0.5, "exit": 0.4, "smooth_window": 1},
            model_path=self.root / "model.pkl",
        )

    def _samples(self, source, out_dir, interval, cancel_event):
        paths = []
        thumbs = []
        for index, value in enumerate((40, 180, 220)):
            frame = np.full((180, 320, 3), value, dtype=np.uint8)
            path = out_dir / f"sample_{index}.jpg"
            cv2.imwrite(str(path), frame)
            paths.append(path)
            thumbs.append(frame)
        return [0.0, 1.0, 2.0], paths, thumbs, 3.0

    def _analysis(self):
        return SimpleNamespace(path=None, width=320, height=180, blur_score=1.0, brightness=100.0,
            megapixels=0.004, is_blurry=False, is_dark=False, is_low_resolution=False,
            has_person_candidate=True, has_kite_candidate=True, is_empty_scene=False,
            is_ng=False, ng_reason="", score=1.0)

    def test_real_predict_and_thumbnails(self):
        self.profile.model_path.write_text("placeholder", encoding="utf-8")
        model = FakeModel()
        with patch("video_analysis.sport_profile.load_profile", return_value=self.profile), \
             patch("video_analysis.kite_clipper.analyze_frame_task", return_value=(Path("frame.jpg"), self._analysis(), "")), \
             patch("video_analysis.joblib.load", return_value={"model": model, "medians": {}, "features": "classic"}):
            result = video_analysis.analyze_material(self.source, self.root, self.root)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(model.shape[0], 3)
        self.assertTrue(Path(result["thumbnail_path"]).is_file())
        for key in ("start_thumbnail_path", "representative_thumbnail_path", "end_thumbnail_path"):
            self.assertTrue(Path(result["candidates"][0][key]).is_file())

    def test_sklearn_bundle_end_to_end_and_rerun(self):
        from sklearn.dummy import DummyClassifier
        import hashlib
        import joblib
        from threading import Event
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        model = DummyClassifier(strategy="constant", constant=1)
        model.fit(np.zeros((2, 14)), np.array([0, 1], dtype=int))
        model_path = self.root / "e2e_model.pkl"
        joblib.dump({"model": model, "medians": {}, "features": "classic"}, model_path)
        self.profile.model_path = model_path
        self.profile.cv.update({"frame_interval": 1.0, "min_width": 1, "min_height": 1})
        with patch("video_analysis.sport_profile.load_profile", return_value=self.profile):
            first = video_analysis.analyze_material(self.source, self.root / "app", self.root / "work")
            second = video_analysis.analyze_material(self.source, self.root / "app", self.root / "work")
        self.assertEqual(first["status"], "ok", first.get("error"))
        self.assertTrue(first["candidates"])
        self.assertNotEqual(first["thumbnail_path"], second["thumbnail_path"])
        self.assertEqual(digest, hashlib.sha256(self.source.read_bytes()).hexdigest())
        image = cv2.imread(first["thumbnail_path"])
        self.assertIsNotNone(image)

    def test_cancelled_before_work_and_structured_error(self):
        from threading import Event
        event = Event()
        event.set()
        with patch("video_analysis.sport_profile.load_profile", return_value=self.profile):
            result = video_analysis.analyze_material(self.source, self.root, self.root, cancel_event=event)
        self.assertEqual(result["status"], "cancelled")
        missing = video_analysis.analyze_material(self.root / "does-not-exist.mp4", self.root, self.root)
        self.assertEqual(missing["status"], "error")
        self.assertIsInstance(missing["error"], str)
    def test_missing_model_is_no_model(self):
        self.profile.model_path = self.root / "missing_model_unique.pkl"
        with patch("video_analysis.sport_profile.load_profile", return_value=self.profile), patch("video_analysis._extract_samples", side_effect=self._samples):
            result = video_analysis.analyze_material(self.source, self.root, self.root)
        self.assertEqual(result["status"], "no_model")
        self.assertEqual(result["candidates"], [])


if __name__ == "__main__":
    unittest.main()
