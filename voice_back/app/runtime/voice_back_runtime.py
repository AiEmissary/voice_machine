import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict

from app.bridge.bridge_service import BridgeService
from app.logging.session_logger import SessionLogger
from app.playback.audio_player import AudioPlayer
from app.tts.piper_engine import PiperEngine
from app.tts.system_engine import SystemTtsEngine


class VoiceBackRuntime:
    def __init__(self, config: Dict[str, Any], logger: SessionLogger, ui):
        self.config = config
        self.logger = logger
        self.ui = ui

        self.bridge = BridgeService(config.get("bridge", {}), logger, ui)
        self.piper = PiperEngine(config.get("tts", {}))
        self.system_tts = SystemTtsEngine(config.get("tts", {}))
        self.player = AudioPlayer(config.get("playback", {}))

        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._tts_worker, daemon=True, name="tts-worker")
        self._errors = 0

    def start(self) -> None:
        self._worker.start()
        self.ui.update_status({"bridge": "listening", "tts": "idle", "playback": "idle", "errors": self._errors})

        poll_interval = max(0.05, int(self.config.get("bridge", {}).get("pollIntervalMs") or 200) / 1000.0)
        while not self._stop.is_set():
            items = self.bridge.poll()
            for item in items:
                self._queue.put(item)
            time.sleep(poll_interval)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put({"_stop": True})
        except Exception:
            pass

    def _tts_worker(self) -> None:
        output_dir = Path(self.config.get("tts", {}).get("piperOutputDir") or "logs/current/tts_cache")
        output_dir.mkdir(parents=True, exist_ok=True)

        while not self._stop.is_set():
            item = self._queue.get()
            if item.get("_stop"):
                break

            transcript = str(item.get("transcript") or "").strip()
            if not transcript:
                continue

            voice = str(self.config.get("tts", {}).get("voice") or "default")
            session_id = str(item.get("sessionId") or "")
            timestamp = str(item.get("timestamp") or "")

            self.ui.update_status({"tts": "synth", "playback": "idle"})

            start = time.time()
            engine = "piper"
            status = "error"
            reason = "piper_failed"
            latency_ms = 0

            if self.piper.available():
                wav_path = output_dir / f"tts_{int(time.time() * 1000)}.wav"
                synth = self.piper.synthesize(transcript, wav_path)
                if synth.get("ok"):
                    self.ui.update_status({"playback": "playing"})
                    play = self.player.play_wav(wav_path)
                    latency_ms = int((time.time() - start) * 1000)
                    if play.get("ok"):
                        status = "ok"
                        reason = "ok"
                    else:
                        status = "error"
                        reason = play.get("reason") or "playback_failed"
                else:
                    status = "error"
                    reason = synth.get("reason") or "piper_failed"
            else:
                reason = "piper_unavailable"

            if status != "ok" and self.config.get("tts", {}).get("systemFallback", True):
                engine = "system"
                self.ui.update_status({"tts": "fallback", "playback": "playing"})
                fallback = self.system_tts.speak(transcript)
                latency_ms = int((time.time() - start) * 1000)
                if fallback.get("ok"):
                    status = "ok"
                    reason = "ok"
                else:
                    status = "error"
                    reason = fallback.get("reason") or "system_tts_failed"

            if status != "ok":
                self._errors += 1
                self.ui.update_status({"errors": self._errors})
                self.logger.failure(str(reason), {"transcript": transcript, "engine": engine})
            else:
                self.ui.update_status({"tts": "idle", "playback": "idle"})

            payload = {
                "sessionId": session_id,
                "timestamp": timestamp,
                "transcript": transcript,
                "voice": voice,
                "engine": engine,
                "latencyMs": int(latency_ms),
                "status": status,
                "reason": reason,
            }
            self.logger.write_tts_event(payload)
