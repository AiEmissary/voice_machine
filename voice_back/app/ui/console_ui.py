import threading
from typing import Dict


class ConsoleUI:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._status = {
            "bridge": "idle",
            "tts": "idle",
            "playback": "idle",
            "errors": 0,
        }

    def update_status(self, updates: Dict[str, object]) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._status.update({k: v for k, v in updates.items() if v is not None})
            line = (
                f"Bridge: {self._status['bridge']} | "
                f"TTS: {self._status['tts']} | "
                f"Playback: {self._status['playback']} | "
                f"Errors: {self._status['errors']}"
            )
            print(line)

    def log(self, message: str) -> None:
        if self.enabled:
            print(message)

    def error(self, message: str) -> None:
        if self.enabled:
            print(f"ERROR: {message}")
