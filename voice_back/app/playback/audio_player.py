import wave
from pathlib import Path
from typing import Any, Dict

import numpy as np
import sounddevice as sd


class AudioPlayer:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self.volume = float(self.cfg.get("volume") or 1.0)

    def play_wav(self, path: Path) -> Dict[str, Any]:
        try:
            with wave.open(str(path), "rb") as wf:
                channels = wf.getnchannels()
                rate = wf.getframerate()
                sampwidth = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())

            if sampwidth == 2:
                dtype = np.int16
            elif sampwidth == 4:
                dtype = np.int32
            else:
                return {"ok": False, "reason": f"unsupported_sample_width_{sampwidth}"}

            audio = np.frombuffer(frames, dtype=dtype)
            if channels > 1:
                audio = audio.reshape(-1, channels)
            if self.volume != 1.0:
                audio = np.clip(audio * self.volume, np.iinfo(dtype).min, np.iinfo(dtype).max).astype(dtype)

            sd.play(audio, rate)
            sd.wait()
            return {"ok": True, "reason": "ok"}
        except Exception as exc:
            return {"ok": False, "reason": "playback_failed", "error": str(exc)}
