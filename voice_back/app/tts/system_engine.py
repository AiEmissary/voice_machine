from typing import Any, Dict


class SystemTtsEngine:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self._engine = None

    def _load(self):
        if self._engine is not None:
            return self._engine
        try:
            import pyttsx3
        except Exception:
            return None
        self._engine = pyttsx3.init()
        return self._engine

    def speak(self, text: str) -> Dict[str, Any]:
        engine = self._load()
        if engine is None:
            return {"ok": False, "reason": "system_tts_unavailable"}
        try:
            engine.say(text)
            engine.runAndWait()
            return {"ok": True, "reason": "ok"}
        except Exception as exc:
            return {"ok": False, "reason": "system_tts_failed", "error": str(exc)}
