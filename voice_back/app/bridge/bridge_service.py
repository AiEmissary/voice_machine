import hashlib
import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from app.logging.session_logger import SessionLogger


class BridgeService:
    def __init__(self, cfg: Dict[str, Any], logger: SessionLogger, ui):
        self.cfg = cfg or {}
        self.logger = logger
        self.ui = ui
        self.events_path = Path(self.cfg.get("eventsPath") or "")
        self.latest_result_path = Path(self.cfg.get("latestResultPath") or "")
        self.poll_interval_ms = int(self.cfg.get("pollIntervalMs") or 200)
        self.idle_fallback_ms = int(self.cfg.get("idleFallbackMs") or 1200)
        self.dedupe_max = int(self.cfg.get("dedupeMaxEntries") or 200)
        self.skip_existing_events = bool(self.cfg.get("skipExistingEventsOnStart", True))
        self.repeat_block_window_ms = int(self.cfg.get("repeatBlockWindowMs") or 0)
        self.repeat_block_min_chars = int(self.cfg.get("repeatBlockMinChars") or 0)

        self._events_offset = 0
        self._last_event_ts = 0.0
        self._seen: Deque[str] = deque()
        self._seen_set = set()
        self._recent_transcripts: Dict[str, float] = {}

        if self.skip_existing_events and self.events_path.exists():
            try:
                self._events_offset = self.events_path.stat().st_size
                self._last_event_ts = time.time()
                self.logger.info(
                    "bridge_skip_existing_events",
                    {"path": str(self.events_path), "offset": self._events_offset},
                )
            except Exception as exc:
                self.logger.failure(
                    "bridge_skip_existing_events_failed",
                    {"error": str(exc), "path": str(self.events_path)},
                    status="warn",
                )

    def poll(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        now = time.time()
        events = self._read_events()
        if events:
            out.extend(events)
            self._last_event_ts = now
        else:
            idle_ms = (now - self._last_event_ts) * 1000 if self._last_event_ts else None
            if idle_ms is None or idle_ms >= self.idle_fallback_ms:
                latest = self._read_latest_result()
                if latest:
                    out.append(latest)
        return out

    def _read_events(self) -> List[Dict[str, Any]]:
        if not self.events_path.exists():
            return []

        try:
            size = self.events_path.stat().st_size
            if size < self._events_offset:
                self._events_offset = 0
        except Exception:
            self._events_offset = 0

        new_items: List[Dict[str, Any]] = []
        try:
            with self.events_path.open("r", encoding="utf-8") as f:
                f.seek(self._events_offset)
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except Exception as exc:
                        self.logger.failure("bridge_event_decode_failed", {"error": str(exc), "line": line[:200]}, status="error")
                        continue
                    if row.get("name") != "transcript_result":
                        continue
                    payload = row.get("payload") or {}
                    item = self._normalize_payload(payload)
                    if item:
                        new_items.append(item)
                self._events_offset = f.tell()
        except Exception as exc:
            self.logger.failure("bridge_events_read_failed", {"error": str(exc), "path": str(self.events_path)})
            self.ui.update_status({"bridge": "error"})
            return []

        if new_items:
            self.ui.update_status({"bridge": "ok"})
        return new_items

    def _read_latest_result(self) -> Optional[Dict[str, Any]]:
        if not self.latest_result_path.exists():
            return None
        try:
            raw = self.latest_result_path.read_text(encoding="utf-8").strip()
            if not raw:
                return None
            payload = json.loads(raw)
            item = self._normalize_payload(payload)
            return item
        except Exception as exc:
            self.logger.failure("bridge_latest_read_failed", {"error": str(exc), "path": str(self.latest_result_path)})
            return None

    def _normalize_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not payload:
            return None
        status = str(payload.get("status") or "").lower()
        transcript = str(payload.get("transcript") or "").strip()
        if status != "ok" or not transcript:
            return None

        session_id = str(payload.get("sessionId") or "")
        timestamp = str(payload.get("timestamp") or "")
        key = self._dedupe_key(session_id, timestamp, transcript)
        if self._is_duplicate(key):
            return None

        if self._is_recent_repeat(transcript, session_id, timestamp):
            return None

        return {
            "sessionId": session_id,
            "timestamp": timestamp,
            "transcript": transcript,
        }

    def _normalize_transcript(self, transcript: str) -> str:
        text = transcript.strip().lower()
        text = re.sub(r"\s+", " ", text)
        text = text.strip(" .!?\"'")
        return text

    def _is_recent_repeat(self, transcript: str, session_id: str, timestamp: str) -> bool:
        if self.repeat_block_window_ms <= 0:
            return False
        if self.repeat_block_min_chars and len(transcript) < self.repeat_block_min_chars:
            return False
        normalized = self._normalize_transcript(transcript)
        if not normalized:
            return False

        now = time.time()
        window_sec = self.repeat_block_window_ms / 1000.0
        expired = [key for key, seen_at in self._recent_transcripts.items() if now - seen_at > window_sec]
        for key in expired:
            self._recent_transcripts.pop(key, None)

        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        last_seen = self._recent_transcripts.get(digest)
        self._recent_transcripts[digest] = now
        if last_seen and (now - last_seen) <= window_sec:
            self.logger.info(
                "bridge_repeat_blocked",
                {
                    "transcript": transcript[:200],
                    "digest": digest,
                    "windowMs": self.repeat_block_window_ms,
                    "sessionId": session_id,
                    "timestamp": timestamp,
                },
            )
            return True
        return False

    def _dedupe_key(self, session_id: str, timestamp: str, transcript: str) -> str:
        digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()[:12]
        return f"{session_id}|{timestamp}|{digest}"

    def _is_duplicate(self, key: str) -> bool:
        if key in self._seen_set:
            return True
        self._seen.append(key)
        self._seen_set.add(key)
        while len(self._seen) > self.dedupe_max:
            old = self._seen.popleft()
            self._seen_set.discard(old)
        return False
