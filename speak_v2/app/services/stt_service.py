from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel


class STTService:
    def __init__(self, logger):
        self.log = logger.log

        # Default to NVIDIA GPU (CUDA). If unavailable at runtime, loader falls back to CPU.
        self.model_name = "large-v3-turbo"
        self.device = "cuda"
        self.compute_type = "float16"
        self.language = "en"
        self.cpu_fallback_model = "small"

        self.model: Optional[WhisperModel] = None
        self.model_ready = False
        self.model_warmed = False
        self.warmup_last_ms: Optional[int] = None

        self.sample_rate = 16000
        self.stream = None
        self.frames = []
        self.recording = False
        self.active_session: Optional[str] = None
        self.captures: dict[str, np.ndarray] = {}

        self._configure_cuda_dll_paths()
        threading.Thread(target=self._load_model, daemon=True).start()

    def _configure_cuda_dll_paths(self):
        # Make pip-installed NVIDIA DLLs discoverable on Windows.
        if os.name != "nt":
            return

        try:
            base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
            candidate_dirs = [
                base / "cublas" / "bin",
                base / "cudnn" / "bin",
                base / "cuda_runtime" / "bin",
            ]

            added = []
            for d in candidate_dirs:
                if d.exists():
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                    try:
                        os.add_dll_directory(str(d))
                    except Exception:
                        pass
                    added.append(str(d))

            if added:
                self.log(f"STT CUDA DLL paths added: {added}")
        except Exception as e:
            self.log(f"STT CUDA DLL path setup failed: {e}")

    def _load_model(self):
        # Try preferred runtime first (CUDA), then fall back gracefully to CPU.
        attempts = [(self.model_name, self.device, self.compute_type)]
        if self.device.lower() == "cuda":
            attempts.append((self.model_name, "cpu", "int8"))

        last_error = None
        for model_name, device, compute_type in attempts:
            try:
                self.log(f"STT loading model={model_name} device={device} compute={compute_type}")
                self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
                self.model_name = model_name
                self.device = device
                self.compute_type = compute_type
                self.model_ready = True
                self.log(f"STT model ready device={self.device} compute={self.compute_type}")
                self._warmup_inference()
                return
            except Exception as e:
                last_error = e
                self.log(f"STT load failed model={model_name} device={device} compute={compute_type} err={e}")

        raise RuntimeError(f"Unable to load Whisper model: {last_error}")


    def _warmup_inference(self):
        if not self.model:
            return
        t0 = time.perf_counter()
        try:
            # Tiny synthetic warmup to compile kernels / initialize first inference path.
            audio = np.zeros(int(self.sample_rate * 0.5), dtype=np.float32)
            _segments, _info = self.model.transcribe(
                audio,
                language=self.language,
                vad_filter=False,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                no_speech_threshold=1.0,
            )
            self.model_warmed = True
            self.warmup_last_ms = int((time.perf_counter() - t0) * 1000)
            self.log(f"STT warmup complete ms={self.warmup_last_ms}")
        except Exception as e:
            self.model_warmed = False
            self.warmup_last_ms = None
            self.log(f"STT warmup skipped/failed: {e}")

    def update_settings(self, profile: str | None, language: str | None):
        if language:
            self.language = language
        if profile == "best_accuracy":
            self.model_name, self.device, self.compute_type = "large-v3", "cpu", "int8"
        elif profile == "best_accuracy_gpu":
            self.model_name, self.device, self.compute_type = "large-v3", "cuda", "float16"
        elif profile == "balanced":
            self.model_name, self.device, self.compute_type = "large-v3-turbo", "cuda", "float16"
        elif profile == "fast":
            self.model_name, self.device, self.compute_type = "medium", "cuda", "float16"
        elif profile == "ultra_fast":
            self.model_name, self.device, self.compute_type = "small", "cuda", "float16"

    def _start_capture(self, session_id: str, sample_rate: int = 16000, channels: int = 1):
        if not self.model_ready:
            raise RuntimeError("Model not ready")
        if self.recording:
            raise RuntimeError("capture_already_active")

        self.sample_rate = int(sample_rate)
        self.frames = []
        self.recording = True
        self.active_session = session_id

        def audio_cb(indata, _frames, _t, status):
            if status:
                self.log(f"audio status: {status}")
            if self.recording:
                self.frames.append(indata.copy().flatten())

        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=channels,
            dtype="float32",
            callback=audio_cb,
        )
        self.stream.start()
        self.log(f"STT capture started session={session_id}")

    def _end_capture(self, session_id: str) -> dict:
        if not self.recording:
            raise RuntimeError("not_recording")
        if self.active_session != session_id:
            raise RuntimeError("session_mismatch")

        self.recording = False
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        finally:
            self.stream = None

        if not self.frames:
            self.captures[session_id] = np.array([], dtype=np.float32)
            return {"sessionId": session_id, "audioMs": 0}

        audio = np.concatenate(self.frames).astype(np.float32)
        self.frames = []
        self.captures[session_id] = audio
        audio_ms = int((len(audio) / self.sample_rate) * 1000)
        return {"sessionId": session_id, "audioMs": audio_ms}

    def _transcribe(self, session_id: str, language: str | None) -> dict:
        audio = self.captures.pop(session_id, None)
        if audio is None:
            raise RuntimeError("no_capture_for_session")
        if len(audio) == 0:
            return {"sessionId": session_id, "text": "", "audioMs": 0, "transcribeMs": 0, "model": self.model_name}

        lang = language or self.language
        audio_ms = int((len(audio) / self.sample_rate) * 1000)

        def run_transcribe():
            # Faster decode path on CPU fallback to reduce end-to-end latency.
            if self.device == "cpu":
                decode_kwargs = {
                    "vad_filter": True,
                    "vad_parameters": {"min_silence_duration_ms": 200},
                    "beam_size": 1,
                    "best_of": 1,
                    "temperature": 0.0,
                    "condition_on_previous_text": False,
                    "no_speech_threshold": 0.5,
                }
            else:
                decode_kwargs = {
                    "vad_filter": True,
                    "vad_parameters": {"min_silence_duration_ms": 300},
                    "beam_size": 5,
                    "best_of": 5,
                    "temperature": [0.0, 0.2, 0.4, 0.6, 0.8],
                    "condition_on_previous_text": True,
                    "no_speech_threshold": 0.5,
                }

            segments, _ = self.model.transcribe(audio, language=lang, **decode_kwargs)
            return " ".join(seg.text.strip() for seg in segments if seg.text and seg.text.strip()).strip()

        t0 = time.perf_counter()
        try:
            text = run_transcribe()
        except Exception as e:
            msg = str(e).lower()
            gpu_lib_error = ("cublas" in msg) or ("cudnn" in msg) or ("cuda" in msg and "dll" in msg)
            if self.device == "cuda" and gpu_lib_error:
                self.log(f"STT CUDA runtime error, falling back to CPU for this session: {e}")
                self.model = WhisperModel(self.cpu_fallback_model, device="cpu", compute_type="int8")
                self.model_name = self.cpu_fallback_model
                self.device = "cpu"
                self.compute_type = "int8"
                self.model_ready = True
                self.log(f"STT fallback model ready device={self.device} compute={self.compute_type} model={self.model_name}")
                text = run_transcribe()
            else:
                raise

        transcribe_ms = int((time.perf_counter() - t0) * 1000)
        self.log(f"STT transcribed session={session_id} text_len={len(text)} audio_ms={audio_ms} transcribe_ms={transcribe_ms}")

        return {
            "sessionId": session_id,
            "text": text,
            "audioMs": audio_ms,
            "transcribeMs": transcribe_ms,
            "model": self.model_name,
        }

    def handle(self, envelope: dict) -> dict:
        name = envelope["name"]
        payload = envelope.get("payload", {})
        req_id = envelope["id"]

        if name == "stt.begin_capture":
            self._start_capture(
                session_id=payload["sessionId"],
                sample_rate=payload.get("sampleRate", 16000),
                channels=payload.get("channels", 1),
            )
            return {
                "id": req_id,
                "type": "response",
                "ts": envelope["ts"],
                "source": "stt",
                "target": envelope["source"],
                "name": "stt.capture_started",
                "payload": {"sessionId": payload["sessionId"]},
            }

        if name == "stt.end_capture":
            result = self._end_capture(session_id=payload["sessionId"])
            return {
                "id": req_id,
                "type": "response",
                "ts": envelope["ts"],
                "source": "stt",
                "target": envelope["source"],
                "name": "stt.capture_ended",
                "payload": result,
            }

        if name == "stt.transcribe":
            result = self._transcribe(session_id=payload["sessionId"], language=payload.get("language"))
            return {
                "id": req_id,
                "type": "response",
                "ts": envelope["ts"],
                "source": "stt",
                "target": envelope["source"],
                "name": "stt.result",
                "payload": result,
            }

        if name == "stt.status":
            return {
                "id": req_id,
                "type": "response",
                "ts": envelope["ts"],
                "source": "stt",
                "target": envelope["source"],
                "name": "stt.status",
                "payload": {
                    "ready": bool(self.model_ready),
                    "device": self.device,
                    "computeType": self.compute_type,
                    "model": self.model_name,
                    "modelWarmed": bool(self.model_warmed),
                    "warmupMs": self.warmup_last_ms,
                },
            }

        raise RuntimeError(f"unknown_command:{name}")
