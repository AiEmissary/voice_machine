import queue
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import sounddevice as sd


@dataclass
class AudioChunk:
    samples: np.ndarray
    rms: float
    ts: float


class AudioInputStream:
    def __init__(self, sample_rate: int, channels: int, block_size: int):
        self.sample_rate = int(sample_rate)  # target runtime rate (wake/stt pipeline)
        self.channels = int(channels)
        self.block_size = int(block_size)

        self._stream: Optional[sd.InputStream] = None
        self._queue: "queue.Queue[AudioChunk]" = queue.Queue(maxsize=512)
        self._lock = threading.Lock()
        self._running = False
        self.frame_count = 0
        self.last_rms = 0.0
        self.last_chunk_ts = 0.0
        self._device_index: Optional[int] = None
        self._device_name = ""
        self._source_sample_rate = int(sample_rate)
        self._source_block_size = int(block_size)

    @property
    def device_index(self) -> Optional[int]:
        return self._device_index

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def source_sample_rate(self) -> int:
        return int(self._source_sample_rate)

    @property
    def source_block_size(self) -> int:
        return int(self._source_block_size)

    def start(
        self,
        device_index: Optional[int],
        device_name: str = "",
        sample_rate_override: Optional[int] = None,
        block_size_override: Optional[int] = None,
    ) -> None:
        with self._lock:
            if self._running:
                return

            self.frame_count = 0
            self.last_rms = 0.0
            self.last_chunk_ts = 0.0
            self._device_index = device_index
            self._device_name = device_name or ""
            self._source_sample_rate = int(sample_rate_override or self.sample_rate)
            if block_size_override is None:
                self._source_block_size = int(self.block_size)
            else:
                self._source_block_size = int(block_size_override)

            kwargs = {
                "samplerate": self._source_sample_rate,
                "channels": self.channels,
                "dtype": "float32",
                "callback": self._callback,
            }
            if self._source_block_size > 0:
                kwargs["blocksize"] = self._source_block_size
            if device_index is not None:
                kwargs["device"] = int(device_index)

            self._stream = sd.InputStream(**kwargs)
            self._stream.start()
            self._running = True

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._stream is not None:
                try:
                    self._stream.stop()
                except Exception:
                    pass
                try:
                    self._stream.close()
                except Exception:
                    pass
            self._stream = None

        self._drain_queue()

    def wait_for_callbacks(self, timeout_ms: int) -> bool:
        timeout_s = max(0.1, timeout_ms / 1000.0)
        until = time.time() + timeout_s
        while time.time() < until:
            if self.frame_count > 0:
                return True
            time.sleep(0.02)
        return self.frame_count > 0

    def read_chunk(self, timeout: float = 0.2) -> Optional[AudioChunk]:
        if not self._running:
            return None
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _drain_pending_chunks(self) -> List[AudioChunk]:
        drained: List[AudioChunk] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return drained

    def take_recent_pending_chunks(self, keep_last_samples: int = 0) -> Tuple[int, List[AudioChunk]]:
        """Drain queued audio and keep only recent tail chunks.

        Returns (dropped_count, kept_tail_chunks).
        """
        drained = self._drain_pending_chunks()
        if not drained:
            return 0, []

        if keep_last_samples <= 0:
            return len(drained), []

        keep_last_samples = max(1, int(keep_last_samples))
        kept: List[AudioChunk] = []
        kept_samples = 0
        for chunk in reversed(drained):
            kept.insert(0, chunk)
            kept_samples += int(getattr(chunk.samples, "size", 0) or 0)
            if kept_samples >= keep_last_samples:
                break

        dropped = max(0, len(drained) - len(kept))
        return dropped, kept

    def take_earliest_pending_chunks(self, keep_first_samples: int = 0) -> Tuple[int, List[AudioChunk]]:
        """Drain queued audio and keep only earliest head chunks.

        Useful when command speech likely occurred during a blocked wake probe.
        Returns (dropped_count, kept_head_chunks).
        """
        drained = self._drain_pending_chunks()
        if not drained:
            return 0, []

        if keep_first_samples <= 0:
            return len(drained), []

        keep_first_samples = max(1, int(keep_first_samples))
        kept: List[AudioChunk] = []
        kept_samples = 0
        for chunk in drained:
            kept.append(chunk)
            kept_samples += int(getattr(chunk.samples, "size", 0) or 0)
            if kept_samples >= keep_first_samples:
                break

        dropped = max(0, len(drained) - len(kept))
        return dropped, kept

    def clear_pending_chunks(self) -> int:
        dropped, _ = self.take_recent_pending_chunks(keep_last_samples=0)
        return dropped

    def _callback(self, indata, frames, time_info, status) -> None:
        if not self._running:
            return

        try:
            mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        except Exception:
            mono = np.asarray(indata, dtype=np.float32).reshape(-1).copy()

        if self._source_sample_rate != self.sample_rate:
            mono = self._resample_linear(mono, src_rate=self._source_sample_rate, dst_rate=self.sample_rate)

        rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
        chunk = AudioChunk(samples=mono, rms=rms, ts=time.time())

        self.frame_count += 1
        self.last_rms = rms
        self.last_chunk_ts = chunk.ts

        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            # Drop oldest to keep low-lag operation.
            try:
                _ = self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(chunk)
            except queue.Full:
                pass

    @staticmethod
    def _resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if samples is None or getattr(samples, "size", 0) <= 0:
            return np.zeros(0, dtype=np.float32)
        if src_rate <= 0 or dst_rate <= 0 or src_rate == dst_rate:
            return samples.astype(np.float32, copy=False)

        src_len = int(samples.size)
        dst_len = max(1, int(round(src_len * float(dst_rate) / float(src_rate))))
        x_src = np.linspace(0.0, 1.0, num=src_len, endpoint=False, dtype=np.float64)
        x_dst = np.linspace(0.0, 1.0, num=dst_len, endpoint=False, dtype=np.float64)
        out = np.interp(x_dst, x_src, samples.astype(np.float32, copy=False)).astype(np.float32)
        return out

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
