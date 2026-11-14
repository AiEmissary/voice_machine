import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional


ISO = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


@dataclass
class SessionFiles:
    session_log: Path
    events_jsonl: Path
    errors_log: Path


class SessionLogger:
    """Latest-session-only logger.

    Contract:
    - startup wipes logs/current/*
    - creates session.log, events.jsonl, errors.log
    - emits startup banner with timestamp + config hash + mic target
    - all failures include reasonCode + context
    """

    def __init__(self, project_root: Path, config: Dict[str, Any]):
        self.project_root = Path(project_root)
        self.config = config
        self.logs_dir = self.project_root / "logs" / "current"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

        self.files = SessionFiles(
            session_log=self.logs_dir / "session.log",
            events_jsonl=self.logs_dir / "events.jsonl",
            errors_log=self.logs_dir / "errors.log",
        )

        self.session_id = f"vm_{int(datetime.now(timezone.utc).timestamp())}"
        self.config_hash = hashlib.sha256(
            _safe_json(config).encode("utf-8")
        ).hexdigest()[:12]

    def start_new_session(self) -> None:
        self._wipe_current_logs()
        for p in (self.files.session_log, self.files.events_jsonl, self.files.errors_log):
            p.write_text("", encoding="utf-8")

        mic_target = (
            self.config.get("audio", {}).get("preferredInputDeviceLabel")
            or "unknown"
        )
        self.info(
            "startup_banner",
            {
                "sessionId": self.session_id,
                "timestamp": utc_now_iso(),
                "configHash": self.config_hash,
                "micTarget": mic_target,
            },
        )

    def _wipe_current_logs(self) -> None:
        for item in self.logs_dir.glob("*"):
            if item.is_file():
                try:
                    item.unlink()
                except Exception:
                    pass

    def _append_line(self, path: Path, line: str) -> None:
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def info(self, event: str, context: Optional[Dict[str, Any]] = None) -> None:
        payload = {
            "ts": utc_now_iso(),
            "level": "info",
            "event": event,
            "context": context or {},
        }
        self._append_line(self.files.session_log, _safe_json(payload))

    def event(self, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        row = {
            "ts": utc_now_iso(),
            "sessionId": self.session_id,
            "name": name,
            "payload": payload or {},
        }
        self._append_line(self.files.events_jsonl, _safe_json(row))

    def failure(
        self,
        reason_code: str,
        context: Optional[Dict[str, Any]] = None,
        status: str = "error",
    ) -> None:
        row = {
            "ts": utc_now_iso(),
            "level": "error" if status == "error" else "warn",
            "reasonCode": reason_code,
            "status": status,
            "context": context or {},
        }
        encoded = _safe_json(row)
        self._append_line(self.files.session_log, encoded)
        self._append_line(self.files.errors_log, encoded)

    def latest_result_path(self) -> Path:
        return self.logs_dir / "latest_result.json"

    def write_latest_result(self, result: Dict[str, Any]) -> None:
        p = self.latest_result_path()
        p.write_text(_safe_json(result) + "\n", encoding="utf-8")
        self.event("transcript_result", result)
