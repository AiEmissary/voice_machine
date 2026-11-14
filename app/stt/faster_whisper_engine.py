from typing import Any, Dict, Optional

import numpy as np
from faster_whisper import WhisperModel


class FasterWhisperEngine:
    def __init__(self, stt_cfg: Dict[str, Any]):
        self.cfg = stt_cfg or {}
        self.model_name = str(self.cfg.get("model") or "large-v3-turbo")
        self.device = str(self.cfg.get("device") or "cpu")
        self.compute_type = str(self.cfg.get("computeType") or "int8")
        self.language = str(self.cfg.get("language") or "en")
        self.beam_size = int(self.cfg.get("beamSize") or 3)
        self.vad_filter = self.cfg.get("vadFilter", True) is True
        self.initial_prompt = str(self.cfg.get("initialPrompt") or "")

        # Dedicated low-lag wake model to avoid blocking the main loop on CPU-heavy models.
        self.enable_wake_model = self.cfg.get("enableWakeModel", True) is True
        self.wake_model_name = str(self.cfg.get("wakeModel") or "small.en")
        self.wake_device = str(self.cfg.get("wakeDevice") or "cpu")
        self.wake_compute_type = str(self.cfg.get("wakeComputeType") or "int8")
        self.wake_auto_gain_enabled = self.cfg.get("wakeAutoGainEnabled", True) is True
        self.wake_auto_gain_target_rms = float(self.cfg.get("wakeAutoGainTargetRms", 0.045))
        self.wake_auto_gain_max = float(self.cfg.get("wakeAutoGainMax", 12.0))

        self.model: Optional[WhisperModel] = None
        self.wake_model: Optional[WhisperModel] = None

        self.active_model_name = self.model_name
        self.active_device = self.device
        self.active_compute_type = self.compute_type

        self.active_wake_model_name: Optional[str] = None
        self.active_wake_device: Optional[str] = None
        self.active_wake_compute_type: Optional[str] = None

    def load(self) -> Dict[str, Any]:
        attempts = [(self.model_name, self.device, self.compute_type)]
        if self.device.lower() == "cuda":
            attempts.append((self.model_name, "cpu", "int8"))
            attempts.append(("small.en", "cpu", "int8"))

        last_error = None
        for model_name, device, compute_type in attempts:
            try:
                self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
                self.active_model_name = model_name
                self.active_device = device
                self.active_compute_type = compute_type
                self._warmup_model(self.model)

                wake_model_state = self._load_wake_model_if_configured()
                return {
                    "ok": True,
                    "model": model_name,
                    "device": device,
                    "computeType": compute_type,
                    "wakeModel": wake_model_state,
                }
            except Exception as exc:
                last_error = str(exc)

        return {
            "ok": False,
            "reason": "stt_model_load_failed",
            "error": last_error,
            "model": self.model_name,
            "device": self.device,
            "computeType": self.compute_type,
        }

    def _load_wake_model_if_configured(self) -> Dict[str, Any]:
        self.wake_model = None
        self.active_wake_model_name = None
        self.active_wake_device = None
        self.active_wake_compute_type = None

        if not self.enable_wake_model:
            return {"ok": True, "reason": "wake_model_disabled"}

        if self.model is None:
            return {"ok": False, "reason": "wake_model_primary_not_loaded"}

        # Reuse primary model when it already matches wake model config.
        if (
            self.wake_model_name == self.active_model_name
            and self.wake_device == self.active_device
            and self.wake_compute_type == self.active_compute_type
        ):
            self.wake_model = self.model
            self.active_wake_model_name = self.active_model_name
            self.active_wake_device = self.active_device
            self.active_wake_compute_type = self.active_compute_type
            return {
                "ok": True,
                "reason": "wake_model_shared_primary",
                "model": self.active_wake_model_name,
                "device": self.active_wake_device,
                "computeType": self.active_wake_compute_type,
            }

        try:
            self.wake_model = WhisperModel(
                self.wake_model_name,
                device=self.wake_device,
                compute_type=self.wake_compute_type,
            )
            self._warmup_model(self.wake_model)
            self.active_wake_model_name = self.wake_model_name
            self.active_wake_device = self.wake_device
            self.active_wake_compute_type = self.wake_compute_type
            return {
                "ok": True,
                "reason": "wake_model_loaded",
                "model": self.active_wake_model_name,
                "device": self.active_wake_device,
                "computeType": self.active_wake_compute_type,
            }
        except Exception as exc:
            self.wake_model = None
            return {
                "ok": False,
                "reason": "wake_model_load_failed",
                "error": str(exc),
                "model": self.wake_model_name,
                "device": self.wake_device,
                "computeType": self.wake_compute_type,
            }

    def _warmup_model(self, model: Optional[WhisperModel]) -> None:
        if model is None:
            return
        silence = np.zeros(16000, dtype=np.float32)
        segments, _ = model.transcribe(
            silence,
            language=self._lang_or_none(),
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        _ = list(segments)

    def _lang_or_none(self):
        if not self.language or self.language.lower() == "auto":
            return None
        return self.language

    def transcribe(self, audio_f32: np.ndarray, purpose: str = "dictation") -> Dict[str, Any]:
        if self.model is None:
            return {
                "success": False,
                "reason": "stt_not_loaded",
                "transcript": "",
            }

        if audio_f32 is None or getattr(audio_f32, "size", 0) <= 0:
            return {
                "success": False,
                "reason": "empty_audio_capture",
                "transcript": "",
            }

        is_wake = str(purpose or "").lower() == "wake"
        model = self.wake_model if is_wake and self.wake_model is not None else self.model

        try:
            # Wake fallback must stay low-lag and permissive for short phrases like "Andromeda".
            # VAD on tiny rolling buffers can suppress wake words; disable it for wake probes.
            beam_size = 1 if is_wake else max(1, self.beam_size)
            vad_filter = False if is_wake else self.vad_filter

            model_name = self.active_wake_model_name if is_wake and self.wake_model is not None else self.active_model_name
            model_device = self.active_wake_device if is_wake and self.wake_model is not None else self.active_device
            model_compute = self.active_wake_compute_type if is_wake and self.wake_model is not None else self.active_compute_type

            audio = audio_f32.astype(np.float32)
            input_rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
            applied_gain = 1.0

            if is_wake and self.wake_auto_gain_enabled and audio.size:
                target = max(0.005, float(self.wake_auto_gain_target_rms))
                max_gain = max(1.0, float(self.wake_auto_gain_max))
                if 0.0 < input_rms < target:
                    applied_gain = min(max_gain, target / max(input_rms, 1e-6))
                    audio = np.clip(audio * applied_gain, -1.0, 1.0)

            transcribe_kwargs = {
                "language": self._lang_or_none(),
                "beam_size": beam_size,
                "vad_filter": vad_filter,
                "initial_prompt": self.initial_prompt if not is_wake else "",
                "condition_on_previous_text": False,
            }
            if is_wake:
                # Wake detection only needs text presence; skipping timestamp decoding
                # keeps probe latency lower and more stable after idle periods.
                transcribe_kwargs["without_timestamps"] = True

            segments, info = model.transcribe(
                audio,
                **transcribe_kwargs,
            )
            segs = list(segments)
            text = " ".join((s.text or "").strip() for s in segs if (s.text or "").strip()).strip()
            if not text:
                return {
                    "success": False,
                    "reason": "empty_transcript",
                    "transcript": "",
                    "language": getattr(info, "language", None),
                    "languageProbability": getattr(info, "language_probability", None),
                    "purpose": "wake" if is_wake else "command",
                    "model": model_name,
                    "device": model_device,
                    "computeType": model_compute,
                    "inputRms": round(input_rms, 6),
                    "appliedGain": round(applied_gain, 3),
                }

            return {
                "success": True,
                "reason": "ok",
                "transcript": text,
                "language": getattr(info, "language", None),
                "languageProbability": getattr(info, "language_probability", None),
                "purpose": "wake" if is_wake else "command",
                "model": model_name,
                "device": model_device,
                "computeType": model_compute,
                "inputRms": round(input_rms, 6),
                "appliedGain": round(applied_gain, 3),
            }
        except Exception as exc:
            return {
                "success": False,
                "reason": "stt_transcribe_failed",
                "error": str(exc),
                "transcript": "",
                "purpose": "wake" if is_wake else "command",
                "model": self.active_wake_model_name if is_wake and self.wake_model is not None else self.active_model_name,
                "device": self.active_wake_device if is_wake and self.wake_model is not None else self.active_device,
                "computeType": self.active_wake_compute_type if is_wake and self.wake_model is not None else self.active_compute_type,
            }
