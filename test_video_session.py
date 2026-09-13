import hashlib
import uuid
import unittest
from pathlib import Path
from video_session import SessionError, SessionStore, Session, Material, Candidate, copy_file_exclusive, stream_copy_clip, compatible_for_concat, probe

class VideoSessionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path("out") / ("test_video_session_" + uuid.uuid4().hex)
        self.root.mkdir(parents=True, exist_ok=True)
        self.source = self.root / "source.bin"
        self.source.write_bytes(b"sample-data")

    def test_exclusive_copy_and_no_clobber(self):
        target = self.root / "copy.bin"
        copy_file_exclusive(self.source, target)
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        with self.assertRaises(SessionError):
            copy_file_exclusive(self.source, target)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), hashlib.sha256(b"sample-data").hexdigest())

    def test_real_ffmpeg_audio_clips_and_compatibility(self):
        source = Path("out/ui-redesign-implementation/synthetic_h264_aac_01.mp4").resolve()
        if not source.is_file():
            self.skipTest("合成素材なし")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        different = self.root / "different.mp4"
        first = self.root / "first.mp4"
        second = self.root / "second.mp4"
        stream_copy_clip(source, first, 0.0, 1.0, Path.cwd())
        stream_copy_clip(source, second, 1.0, 2.0, Path.cwd())
        import subprocess
        subprocess.run([str(Path.cwd() / "vendor/ffmpeg/windows-x64/ffmpeg.exe"), "-y", "-i", str(source), "-vf", "scale=160:90", "-c:v", "mpeg4", "-c:a", "aac", str(different)], check=True, capture_output=True)
        compatible, message = compatible_for_concat([first, second], Path.cwd())
        self.assertTrue(compatible, message)
        digest_before = digest
        for clip in (first, second):
            info = probe(clip, Path.cwd())
            self.assertTrue(any(s.get("codec_type") == "video" for s in info.get("streams", [])))
            self.assertTrue(any(s.get("codec_type") == "audio" for s in info.get("streams", [])))
            self.assertGreater(float(info["format"]["duration"]), 0.0)
        self.assertFalse(compatible_for_concat([first, different], Path.cwd())[0])
        self.assertEqual(digest_before, hashlib.sha256(source.read_bytes()).hexdigest())
    def test_load_rejects_candidate_outside_duration(self):
        store = SessionStore(self.root, "s-" + uuid.uuid4().hex[:8])
        material = Material("m1", "x.mp4", str(self.source), self.source.stat().st_size, self.source.stat().st_mtime_ns, duration_sec=2.0)
        session = Session(store.root.name, materials=[material], candidates=[Candidate("c1", "m1", 0.0, 3.0)])
        store.save(session)
        with self.assertRaises(SessionError):
            store.load()

if __name__ == "__main__":
    unittest.main()