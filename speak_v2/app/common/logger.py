from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_LOG_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_ROTATED_FILES = 2
MAX_LOG_BYTES = int(os.environ.get("WHISPER_FLOW_MAX_LOG_BYTES", DEFAULT_MAX_LOG_BYTES))
MAX_ROTATED_FILES = int(os.environ.get("WHISPER_FLOW_MAX_ROTATED_LOGS", DEFAULT_MAX_ROTATED_FILES))


def _rotated_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def _rotate_if_needed(path: Path, max_bytes: int = MAX_LOG_BYTES, keep: int = MAX_ROTATED_FILES) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size < max(1024 * 1024, int(max_bytes)):
            return
        keep_count = max(0, int(keep))
        try:
            _rotated_path(path, keep_count).unlink(missing_ok=True)
        except Exception:
            pass
        for i in range(keep_count - 1, 0, -1):
            src = _rotated_path(path, i)
            if src.exists():
                try:
                    src.replace(_rotated_path(path, i + 1))
                except Exception:
                    pass
        try:
            path.replace(_rotated_path(path, 1))
        except Exception:
            # Preserve app functionality if Windows has the log open.
            try:
                path.write_text("", encoding="utf-8")
            except Exception:
                pass
    except Exception:
        # Logging must never break dictation functionality.
        pass


class AppLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.error_path = self.path.with_name("speak_v2_errors.log")
        self.jsonl_path = self.path.with_name("speak_v2_events.jsonl")
        self._lock = threading.Lock()

    @staticmethod
    def _ts() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _append_text(self, path: Path, text: str) -> None:
        with self._lock:
            _rotate_if_needed(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(text)
            _rotate_if_needed(path)

    def log(self, message: str) -> None:
        self._append_text(self.path, f"[{self._ts()}] {message}\n")

    def log_error(self, name: str, payload: Any) -> None:
        line = f"[{self._ts()}] ERROR {name}: {json.dumps(payload, ensure_ascii=False)}\n"
        self._append_text(self.error_path, line)
        self.log(f"error logged: {name}")

    def log_json(self, channel: str, obj: dict) -> None:
        rec = {"ts": self._ts(), "channel": channel, "event": obj}
        self._append_text(self.jsonl_path, json.dumps(rec, ensure_ascii=False) + "\n")
