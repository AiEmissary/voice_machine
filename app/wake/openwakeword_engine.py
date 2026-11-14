from typing import Any, Dict, List, Optional

import numpy as np

# Lazy-import openwakeword only when enabled to avoid noisy backend warnings
# in transcript-fallback-only mode.
openwakeword = None
OpenWakeWordModel = None


class OpenWakeWordEngine:
    def __init__(
        self,
        enabled: bool,
        threshold: float,
        model_paths: Optional[List[str]] = None,
        load_default_models: bool = True,
    ):
        self.enabled = enabled is True
        self.threshold = max(0.0, min(1.0, float(threshold or 0.5)))
        self.model_paths = [str(p) for p in (model_paths or []) if str(p).strip()]
        self.load_default_models = load_default_models is True

        self.available = False
        self.reason = None
        self.model = None

    def load(self) -> Dict[str, Any]:
        global openwakeword, OpenWakeWordModel

        if not self.enabled:
            self.reason = "audio_wake_disabled"
            return self.status()

        if OpenWakeWordModel is None:
            try:
                import openwakeword as _oww
                from openwakeword.model import Model as _OpenWakeWordModel

                openwakeword = _oww
                OpenWakeWordModel = _OpenWakeWordModel
            except Exception as exc:
                self.reason = f"openwakeword_import_failed:{exc}"
                return self.status()

        kwargs = {}
        if self.model_paths:
            kwargs["wakeword_models"] = self.model_paths
        elif not self.load_default_models:
            self.reason = "openwakeword_model_paths_required"
            return self.status()

        try:
            if openwakeword is not None and hasattr(openwakeword, "utils") and hasattr(openwakeword.utils, "download_models"):
                openwakeword.utils.download_models()
        except Exception:
            # Not fatal: model may already exist locally.
            pass

        try:
            self.model = OpenWakeWordModel(**kwargs)
            self.available = True
            self.reason = None
        except Exception as exc:
            self.available = False
            self.reason = f"openwakeword_model_load_failed:{exc}"

        return self.status()

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "threshold": self.threshold,
            "reason": self.reason,
            "modelPaths": self.model_paths,
            "loadDefaultModels": self.load_default_models,
        }

    def predict(self, audio_f32: np.ndarray, wake_targets: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.available or self.model is None:
            return {
                "available": False,
                "triggered": False,
                "score": 0.0,
                "model": None,
                "scores": {},
                "threshold": self.threshold,
                "reason": self.reason,
            }

        if audio_f32 is None or getattr(audio_f32, "size", 0) <= 0:
            return {
                "available": True,
                "triggered": False,
                "score": 0.0,
                "model": None,
                "scores": {},
                "threshold": self.threshold,
                "reason": "empty_audio",
            }

        audio = np.clip(audio_f32.astype(np.float32), -1.0, 1.0)
        pcm16 = (audio * 32767.0).astype(np.int16)

        frame_size = 1280  # 80ms @16k
        best_scores: Dict[str, float] = {}

        try:
            if hasattr(self.model, "reset"):
                self.model.reset()
        except Exception:
            pass

        for start in range(0, int(pcm16.size), frame_size):
            frame = pcm16[start:start + frame_size]
            if frame.size <= 0:
                continue
            if frame.size < frame_size:
                frame = np.pad(frame, (0, frame_size - frame.size))

            pred = self.model.predict(frame)
            if not isinstance(pred, dict):
                continue
            for k, v in pred.items():
                try:
                    fv = float(v)
                except Exception:
                    continue
                best_scores[k] = max(best_scores.get(k, 0.0), fv)

        if not best_scores:
            return {
                "available": True,
                "triggered": False,
                "score": 0.0,
                "model": None,
                "scores": {},
                "threshold": self.threshold,
                "reason": "no_scores",
            }

        candidates = list(best_scores.items())
        targets = [str(t).lower().strip() for t in (wake_targets or []) if str(t).strip()]
        if targets:
            filtered = []
            for name, score in candidates:
                n = str(name).lower()
                if any(t in n or n in t for t in targets):
                    filtered.append((name, score))
            if filtered:
                candidates = filtered

        model_name, score = max(candidates, key=lambda kv: kv[1])
        triggered = float(score) >= self.threshold

        return {
            "available": True,
            "triggered": triggered,
            "score": float(score),
            "model": str(model_name),
            "scores": {k: float(v) for k, v in sorted(best_scores.items(), key=lambda kv: kv[1], reverse=True)},
            "threshold": self.threshold,
            "reason": None,
        }
