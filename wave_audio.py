"""Windows winmm による PCM 音声出力。"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Any, Optional


WAVE_FORMAT_PCM = 0x0001
WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_NULL = 0x00000000
WHDR_DONE = 0x00000001
TIME_MILLISECONDS = 0x0001
# Windows ヘッダで使われる別名。
TIME_MS = TIME_MILLISECONDS
TIME_SAMPLES = 0x0002
TIME_BYTES = 0x0004
MMSYSERR_NOERROR = 0
WAVERR_STILLPLAYING = 33

_MAX_BUFFERS = 4
_BUFFER_BYTES = 64 * 1024
_POLL_SECONDS = 0.01
_RESET_TIMEOUT_SECONDS = 5.0


class WAVEFORMATEX(ctypes.Structure):
    """Windows の WAVEFORMATEX 構造体。"""

    _fields_ = [
        ("wFormatTag", ctypes.c_ushort),
        ("nChannels", ctypes.c_ushort),
        ("nSamplesPerSec", ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_ushort),
        ("wBitsPerSample", ctypes.c_ushort),
        ("cbSize", ctypes.c_ushort),
    ]


class WAVEHDR(ctypes.Structure):
    """Windows の WAVEHDR 構造体。"""

    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", ctypes.c_uint32),
        ("dwBytesRecorded", ctypes.c_uint32),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint32),
        ("dwLoops", ctypes.c_uint32),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


class _MMTIMEUnion(ctypes.Union):
    """MMTIME の8バイト union。"""

    _fields_ = [
        ("ms", ctypes.c_uint32),
        ("sample", ctypes.c_uint32),
        ("cb", ctypes.c_uint32),
        ("ticks", ctypes.c_uint32),
        ("_reserved", ctypes.c_ubyte * 8),
    ]


class MMTIME(ctypes.Structure):
    """Windows の MMTIME 構造体。合計サイズは12バイト。"""

    _fields_ = [
        ("uType", ctypes.c_uint32),
        ("u", _MMTIMEUnion),
    ]


class WaveOutError(RuntimeError):
    """winmm のエラーを表す例外。"""

    def __init__(self, function: str, code: int) -> None:
        self.function = function
        self.code = int(code)
        super().__init__(f"{function} が失敗しました (MMRESULT={self.code})")


class _QueuedBuffer:
    """再生中のヘッダとPCM領域を完了まで保持する内部オブジェクト。"""

    __slots__ = ("data", "header", "prepared")

    def __init__(self, pcm: bytes) -> None:
        self.data = ctypes.create_string_buffer(pcm, len(pcm))
        self.header = WAVEHDR()
        self.header.lpData = ctypes.cast(self.data, ctypes.c_void_p)
        self.header.dwBufferLength = len(pcm)
        self.header.dwBytesRecorded = 0
        self.header.dwUser = None
        self.header.dwFlags = 0
        self.header.dwLoops = 0
        self.header.lpNext = None
        self.header.reserved = None
        self.prepared = False


class WaveOut:
    """Windows winmm のPCM ステレオ16bit出力デバイス。"""

    def __init__(self, sample_rate: int = 44100, channels: int = 2) -> None:
        """指定形式でWAVE_MAPPERを開く。Windows以外では生成時に失敗する。"""
        if sys.platform != "win32":
            raise RuntimeError("WaveOut は Windows でのみ利用できます")
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
            raise TypeError("sample_rate は整数で指定してください")
        if sample_rate <= 0 or sample_rate > 0xFFFFFFFF:
            raise ValueError("sample_rate は1以上で指定してください")
        if isinstance(channels, bool) or not isinstance(channels, int):
            raise TypeError("channels は整数で指定してください")
        if channels not in (1, 2):
            raise ValueError("channels は1または2で指定してください")

        self.sample_rate = sample_rate
        self.channels = channels
        self._block_align = channels * 2
        self._lock = threading.RLock()
        self._closed = False
        self._buffers: list[_QueuedBuffer] = []
        self._winmm = self._load_winmm()
        self._handle = ctypes.c_void_p()
        self._format = WAVEFORMATEX(
            WAVE_FORMAT_PCM,
            channels,
            sample_rate,
            sample_rate * self._block_align,
            self._block_align,
            16,
            0,
        )
        result = self._winmm.waveOutOpen(
            ctypes.byref(self._handle),
            WAVE_MAPPER,
            ctypes.byref(self._format),
            0,
            0,
            CALLBACK_NULL,
        )
        self._check("waveOutOpen", result)

    @staticmethod
    def _load_winmm() -> Any:
        """winmm.dllをロードし、利用する関数のABIを設定する。"""
        winmm = ctypes.WinDLL("winmm")
        handle = ctypes.c_void_p
        dword_ptr = ctypes.c_size_t

        winmm.waveOutOpen.argtypes = [
            ctypes.POINTER(handle),
            ctypes.c_uint32,
            ctypes.POINTER(WAVEFORMATEX),
            dword_ptr,
            dword_ptr,
            ctypes.c_uint32,
        ]
        winmm.waveOutOpen.restype = ctypes.c_uint32

        for name in ("waveOutClose", "waveOutReset", "waveOutPause", "waveOutRestart"):
            function = getattr(winmm, name)
            function.argtypes = [handle]
            function.restype = ctypes.c_uint32

        for name in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
            function = getattr(winmm, name)
            function.argtypes = [handle, ctypes.POINTER(WAVEHDR), ctypes.c_uint32]
            function.restype = ctypes.c_uint32

        winmm.waveOutGetPosition.argtypes = [
            handle,
            ctypes.POINTER(MMTIME),
            ctypes.c_uint32,
        ]
        winmm.waveOutGetPosition.restype = ctypes.c_uint32
        return winmm

    @staticmethod
    def _check(function: str, result: Any) -> None:
        """MMRESULTが成功か確認する。"""
        code = int(result)
        if code != MMSYSERR_NOERROR:
            raise WaveOutError(function, code)

    @staticmethod
    def _is_cancelled(cancel_event: Optional[Any]) -> bool:
        return cancel_event is not None and bool(cancel_event.is_set())

    def _ensure_open_locked(self) -> None:
        if self._closed or not self._handle:
            raise RuntimeError("WaveOut はすでに close されています")

    def _unprepare_one_locked(self, item: _QueuedBuffer) -> bool:
        """完了済みヘッダをunprepareし、解放できたか返す。"""
        if not item.prepared:
            return True
        result = self._winmm.waveOutUnprepareHeader(
            self._handle, ctypes.byref(item.header), ctypes.sizeof(WAVEHDR)
        )
        code = int(result)
        if code == MMSYSERR_NOERROR:
            item.prepared = False
            return True
        if code == WAVERR_STILLPLAYING:
            return False
        raise WaveOutError("waveOutUnprepareHeader", code)

    def _release_completed_locked(self) -> int:
        """WHDR_DONEのバッファをunprepareしてリストから外す。"""
        released = 0
        remaining: list[_QueuedBuffer] = []
        for item in self._buffers:
            if item.header.dwFlags & WHDR_DONE:
                if self._unprepare_one_locked(item):
                    released += 1
                    continue
            remaining.append(item)
        self._buffers = remaining
        return released

    def _wait_for_capacity(self, cancel_event: Optional[Any]) -> None:
        """空き容量を待つ。待機中は他の制御操作のためロックを解放する。"""
        while True:
            with self._lock:
                self._ensure_open_locked()
                self._release_completed_locked()
                if len(self._buffers) < _MAX_BUFFERS:
                    return
                if self._is_cancelled(cancel_event):
                    self._reset_locked()
                    raise InterruptedError("PCM 書き込みがキャンセルされました")
            time.sleep(_POLL_SECONDS)

    def write(self, pcm: bytes, cancel_event: Optional[Any] = None) -> None:
        """PCMをキューに追加する。4バッファを超えると完了を待つ。"""
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            raise TypeError("pcm はbytes系で指定してください")
        pcm_bytes = bytes(pcm)
        if not pcm_bytes:
            return

        with self._lock:
            self._ensure_open_locked()
            if len(pcm_bytes) % self._block_align:
                raise ValueError("PCMの長さがフレーム境界にそろっていません")

        offset = 0
        while offset < len(pcm_bytes):
            self._wait_for_capacity(cancel_event)
            with self._lock:
                self._ensure_open_locked()
                if self._is_cancelled(cancel_event):
                    self._reset_locked()
                    raise InterruptedError("PCM 書き込みがキャンセルされました")
                # 待機から戻った直後に別スレッドがキューを満たした場合に再確認する。
                self._release_completed_locked()
                if len(self._buffers) >= _MAX_BUFFERS:
                    continue
                chunk_size = min(_BUFFER_BYTES, len(pcm_bytes) - offset)
                chunk_size -= chunk_size % self._block_align
                if chunk_size <= 0:
                    chunk_size = self._block_align
                item = _QueuedBuffer(pcm_bytes[offset : offset + chunk_size])
                result = self._winmm.waveOutPrepareHeader(
                    self._handle, ctypes.byref(item.header), ctypes.sizeof(WAVEHDR)
                )
                self._check("waveOutPrepareHeader", result)
                item.prepared = True
                result = self._winmm.waveOutWrite(
                    self._handle, ctypes.byref(item.header), ctypes.sizeof(WAVEHDR)
                )
                if int(result) != MMSYSERR_NOERROR:
                    self._unprepare_one_locked(item)
                    self._check("waveOutWrite", result)
                self._buffers.append(item)
                offset += chunk_size

    def position_ms(self) -> int:
        """現在のデバイス再生位置をミリ秒で返す。"""
        with self._lock:
            self._ensure_open_locked()
            mmtime = MMTIME()
            mmtime.uType = TIME_MILLISECONDS
            result = self._winmm.waveOutGetPosition(
                self._handle, ctypes.byref(mmtime), ctypes.sizeof(MMTIME)
            )
            self._check("waveOutGetPosition", result)
            if mmtime.uType == TIME_MILLISECONDS:
                return int(mmtime.u.ms)
            if mmtime.uType == TIME_SAMPLES:
                return int(mmtime.u.sample * 1000 // self.sample_rate)
            if mmtime.uType == TIME_BYTES:
                return int(mmtime.u.cb * 1000 // (self.sample_rate * self._block_align))
            raise WaveOutError("waveOutGetPosition", int(mmtime.uType))

    def pause(self) -> None:
        """再生を一時停止する。"""
        with self._lock:
            self._ensure_open_locked()
            self._check("waveOutPause", self._winmm.waveOutPause(self._handle))

    def resume(self) -> None:
        """一時停止中の再生を再開する。"""
        with self._lock:
            self._ensure_open_locked()
            self._check("waveOutRestart", self._winmm.waveOutRestart(self._handle))

    def _reset_locked(self) -> None:
        """reset後、全ヘッダをunprepareしてから再生を空にする。"""
        self._ensure_open_locked()
        self._check("waveOutReset", self._winmm.waveOutReset(self._handle))
        deadline = time.monotonic() + _RESET_TIMEOUT_SECONDS
        while self._buffers:
            self._release_completed_locked()
            if not self._buffers:
                return
            if time.monotonic() >= deadline:
                raise WaveOutError("waveOutUnprepareHeader", WAVERR_STILLPLAYING)
            time.sleep(_POLL_SECONDS)

    def reset(self) -> None:
        """保留中のPCMを破棄し、デバイスを安全な順序でリセットする。"""
        with self._lock:
            self._reset_locked()

    def drain(self, cancel_event: Optional[Any] = None) -> None:
        """現在キューにあるPCMがすべて完了するまで待つ。"""
        while True:
            with self._lock:
                self._ensure_open_locked()
                self._release_completed_locked()
                if not self._buffers:
                    return
                if self._is_cancelled(cancel_event):
                    self._reset_locked()
                    raise InterruptedError("PCM再生待ちがキャンセルされました")
            time.sleep(_POLL_SECONDS)

    def close(self) -> None:
        """reset→unprepare→closeの順でデバイスを閉じる。"""
        with self._lock:
            if self._closed:
                return
            self._reset_locked()
            self._check("waveOutClose", self._winmm.waveOutClose(self._handle))
            self._closed = True
            self._handle = ctypes.c_void_p()

    def __enter__(self) -> "WaveOut":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


__all__ = [
    "MMTIME",
    "WAVEFORMATEX",
    "WAVEHDR",
    "WaveOut",
    "WaveOutError",
]
