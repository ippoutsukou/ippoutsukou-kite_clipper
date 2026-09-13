"""wave_audio の構造体と実デバイス動作を検証する unittest。"""

from __future__ import annotations

import math
import sys
import time
import unittest
import ctypes
import threading

from wave_audio import MMTIME, WAVEFORMATEX, WAVEHDR, WaveOut


def _sine_pcm(sample_rate: int = 44100, seconds: float = 1.0) -> bytes:
    """実素材を使わず、ステレオ16bitの正弦波PCMを生成する。"""
    frames = int(sample_rate * seconds)
    output = bytearray(frames * 2 * 2)
    for index in range(frames):
        value = int(12000 * math.sin(2.0 * math.pi * 440.0 * index / sample_rate))
        value_bytes = value.to_bytes(2, byteorder="little", signed=True)
        offset = index * 4
        output[offset : offset + 2] = value_bytes
        output[offset + 2 : offset + 4] = value_bytes
    return bytes(output)


class WaveOutLayoutTests(unittest.TestCase):
    """Windows ABIで要求される構造体サイズを確認する。"""

    def test_layout_sizes(self) -> None:
        self.assertEqual(WAVEFORMATEX.wFormatTag.offset, 0)
        self.assertEqual(WAVEFORMATEX.nSamplesPerSec.offset, 4)
        self.assertEqual(WAVEFORMATEX.cbSize.offset, 16)
        self.assertEqual(ctypes.sizeof(WAVEFORMATEX), 20)
        self.assertEqual(ctypes.sizeof(WAVEHDR), 48)
        self.assertEqual(ctypes.sizeof(MMTIME), 12)

    @unittest.skipUnless(sys.platform != "win32", "Windowsではデバイス生成を実行する")
    def test_non_windows_constructor_fails_at_generation(self) -> None:
        with self.assertRaises(RuntimeError):
            WaveOut()


@unittest.skipUnless(sys.platform == "win32", "winmm実デバイスが必要")
class WaveOutDeviceTests(unittest.TestCase):
    """合成PCMを実デバイスへ送り、位置・停止・再開を検証する。"""

    def setUp(self) -> None:
        self.audio = WaveOut(sample_rate=44100, channels=2)
        self.addCleanup(self.audio.close)

    def _wait_position(self, minimum: int, timeout: float = 1.0) -> int:
        deadline = time.monotonic() + timeout
        current = self.audio.position_ms()
        while current <= minimum and time.monotonic() < deadline:
            time.sleep(0.02)
            current = self.audio.position_ms()
        return current

    def test_write_pause_resume_drain_reset_close(self) -> None:
        pcm = _sine_pcm()
        self.audio.write(pcm)
        progressed = self._wait_position(40)
        self.assertGreater(progressed, 40)

        self.audio.pause()
        paused = self.audio.position_ms()
        time.sleep(0.20)
        paused_again = self.audio.position_ms()
        self.assertLessEqual(abs(paused_again - paused), 30)

        self.audio.resume()
        resumed = self._wait_position(paused + 40)
        self.assertGreater(resumed, paused + 20)

        self.audio.drain()
        self.audio.reset()
        self.audio.close()
        self.audio.close()


    def test_waiting_write_keeps_controls_available(self) -> None:
        pcm = _sine_pcm(seconds=3.0)
        cancel_event = threading.Event()
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                self.audio.write(pcm, cancel_event)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=writer)
        thread.start()
        time.sleep(0.05)
        self.audio.pause()
        paused = self.audio.position_ms()
        time.sleep(0.10)
        self.assertLessEqual(abs(self.audio.position_ms() - paused), 30)
        self.audio.resume()
        cancel_event.set()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertTrue(any(isinstance(error, InterruptedError) for error in errors))
if __name__ == "__main__":
    unittest.main(verbosity=2)
