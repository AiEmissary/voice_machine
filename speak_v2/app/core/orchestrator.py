from __future__ import annotations


class Orchestrator:
    def __init__(self, logger, bus, stt_service, inserter_service, strategy_bot):
        self.log = logger.log
        self.log_error = logger.log_error
        self.bus = bus
        self.stt = stt_service
        self.inserter = inserter_service
        self.bot = strategy_bot

        self.settings = {"profile": "balanced", "language": "en", "insertionPolicy": "auto"}
        self.current: dict | None = None

    def _emit_ui_state(self, state: str, session_id: str, message: str, metrics: dict | None = None):
        self.bus.send(
            source="orchestrator",
            target="ui",
            msg_type="event",
            name=f"ui.state.{state}",
            payload={"sessionId": session_id, "message": message, "metrics": metrics or {"audioMs": 0, "transcribeMs": 0, "insertMs": 0}},
        )

    def _error(self, req: dict, code: str, detail: str, recoverable: bool = True):
        payload = {
            "sessionId": req.get("payload", {}).get("sessionId"),
            "code": code,
            "detail": detail,
            "recoverable": recoverable,
        }
        self.log_error(req.get("name", "orchestrator.error"), payload)
        return {
            "id": req["id"],
            "type": "error",
            "ts": req["ts"],
            "source": "orchestrator",
            "target": req["source"],
            "name": f"{req['name']}.failed",
            "payload": payload,
        }

    def _handle_press_start(self, req: dict) -> dict:
        payload = req["payload"]
        session_id = payload["sessionId"]

        if self.current is not None:
            return self._error(req, "session_already_active", "A session is already active", True)

        target_resp = self.bus.send(
            source="orchestrator",
            target="insert",
            msg_type="command",
            name="insert.capture_target",
            payload={"sessionId": session_id},
        )
        if target_resp.get("type") == "error":
            self._emit_ui_state("error", session_id, "Target capture failed")
            return target_resp

        stt_begin = self.bus.send(
            source="orchestrator",
            target="stt",
            msg_type="command",
            name="stt.begin_capture",
            payload={"sessionId": session_id, "sampleRate": 16000, "channels": 1, "deviceId": None},
        )
        if stt_begin.get("type") == "error":
            self._emit_ui_state("error", session_id, "Capture start failed")
            return stt_begin

        self.current = {"sessionId": session_id, "target": target_resp.get("payload", {})}
        self._emit_ui_state("listening", session_id, "Listening...")

        return {
            "id": req["id"],
            "type": "response",
            "ts": req["ts"],
            "source": "orchestrator",
            "target": req["source"],
            "name": "session.started",
            "payload": {"sessionId": session_id},
        }

    def _handle_press_end(self, req: dict) -> dict:
        payload = req["payload"]
        session_id = payload["sessionId"]

        if self.current is None or self.current.get("sessionId") != session_id:
            return self._error(req, "no_active_session", "No matching active session", True)

        self._emit_ui_state("transcribing", session_id, "Transcribing...")

        ended = self.bus.send(
            source="orchestrator",
            target="stt",
            msg_type="command",
            name="stt.end_capture",
            payload={"sessionId": session_id},
        )
        if ended.get("type") == "error":
            self._emit_ui_state("error", session_id, "Capture end failed")
            self.current = None
            return ended

        transcribed = self.bus.send(
            source="orchestrator",
            target="stt",
            msg_type="command",
            name="stt.transcribe",
            payload={"sessionId": session_id, "profile": self.settings["profile"], "language": self.settings["language"]},
        )
        if transcribed.get("type") == "error":
            self._emit_ui_state("error", session_id, "Transcription failed")
            self.current = None
            return transcribed

        stt_payload = transcribed.get("payload", {})
        text = stt_payload.get("text", "")
        if not text:
            self._emit_ui_state("error", session_id, "No speech detected", {"audioMs": stt_payload.get("audioMs", 0), "transcribeMs": stt_payload.get("transcribeMs", 0), "insertMs": 0})
            self.current = None
            return self._error(req, "empty_transcript", "No text returned by STT", True)

        self._emit_ui_state("inserting", session_id, "Inserting text...", {"audioMs": stt_payload.get("audioMs", 0), "transcribeMs": stt_payload.get("transcribeMs", 0), "insertMs": 0})

        target = self.current["target"]
        order = self.bot.get_strategy_order(target.get("topClass", ""), target.get("focusClass", ""))
        applied = self.bus.send(
            source="orchestrator",
            target="insert",
            msg_type="command",
            name="insert.apply",
            payload={"sessionId": session_id, "text": text, "strategyOrder": order},
        )

        self.current = None
        if applied.get("type") == "error":
            self._emit_ui_state("error", session_id, "Insert failed", {"audioMs": stt_payload.get("audioMs", 0), "transcribeMs": stt_payload.get("transcribeMs", 0), "insertMs": 0})
            return applied

        insert_payload = applied.get("payload", {})
        self.bot.report_result(target.get("topClass", ""), target.get("focusClass", ""), insert_payload.get("strategyUsed") or "none", bool(insert_payload.get("success")))

        if insert_payload.get("success"):
            self._emit_ui_state(
                "success",
                session_id,
                "Done",
                {"audioMs": stt_payload.get("audioMs", 0), "transcribeMs": stt_payload.get("transcribeMs", 0), "insertMs": insert_payload.get("insertMs", 0)},
            )
            return {
                "id": req["id"],
                "type": "response",
                "ts": req["ts"],
                "source": "orchestrator",
                "target": req["source"],
                "name": "session.completed",
                "payload": {
                    "sessionId": session_id,
                    "stt": stt_payload,
                    "insert": insert_payload,
                },
            }

        self._emit_ui_state(
            "error",
            session_id,
            "Insert failed",
            {"audioMs": stt_payload.get("audioMs", 0), "transcribeMs": stt_payload.get("transcribeMs", 0), "insertMs": insert_payload.get("insertMs", 0)},
        )
        return {
            "id": req["id"],
            "type": "error",
            "ts": req["ts"],
            "source": "orchestrator",
            "target": req["source"],
            "name": "insert.failed",
            "payload": {
                "sessionId": session_id,
                "code": "all_strategies_failed",
                "detail": "No insertion strategy succeeded",
                "recoverable": True,
                "attempts": insert_payload.get("attempts", []),
            },
        }

    def _handle_cancel(self, req: dict) -> dict:
        session_id = req.get("payload", {}).get("sessionId")
        if self.current and self.current.get("sessionId") == session_id:
            self.bus.send(source="orchestrator", target="stt", msg_type="command", name="stt.end_capture", payload={"sessionId": session_id})
            self.current = None
        self._emit_ui_state("idle", session_id or "", "Cancelled")
        return {
            "id": req["id"],
            "type": "response",
            "ts": req["ts"],
            "source": "orchestrator",
            "target": req["source"],
            "name": "session.cancelled",
            "payload": {"sessionId": session_id},
        }

    def _handle_settings_update(self, req: dict) -> dict:
        payload = req.get("payload", {})
        self.settings.update({k: v for k, v in payload.items() if v is not None})
        self.stt.update_settings(self.settings.get("profile"), self.settings.get("language"))
        return {
            "id": req["id"],
            "type": "response",
            "ts": req["ts"],
            "source": "orchestrator",
            "target": req["source"],
            "name": "settings.updated",
            "payload": self.settings,
        }

    def handle(self, envelope: dict) -> dict:
        name = envelope["name"]
        if name == "session.press_start":
            return self._handle_press_start(envelope)
        if name == "session.press_end":
            return self._handle_press_end(envelope)
        if name == "session.cancel":
            return self._handle_cancel(envelope)
        if name == "settings.update":
            return self._handle_settings_update(envelope)

        return self._error(envelope, "unknown_command", f"Unknown command: {name}", True)
