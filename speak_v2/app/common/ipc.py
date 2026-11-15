from __future__ import annotations

import json
import traceback
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Any


class LocalMessageBus:
    """In-process local RPC/IPC bus with JSON envelope logging."""

    def __init__(self, logger):
        self.log = logger.log
        self.log_error = logger.log_error
        self.log_json = logger.log_json
        self.handlers: Dict[str, Callable[[dict], dict]] = {}

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def register(self, target: str, handler: Callable[[dict], dict]) -> None:
        self.handlers[target] = handler
        self.log(f"ipc register target={target}")

    def envelope(self, *, source: str, target: str, msg_type: str, name: str, payload: dict, msg_id: str | None = None) -> dict:
        return {
            "id": msg_id or str(uuid.uuid4()),
            "type": msg_type,
            "ts": self._ts(),
            "source": source,
            "target": target,
            "name": name,
            "payload": payload or {},
        }

    def send(self, *, source: str, target: str, msg_type: str, name: str, payload: dict, msg_id: str | None = None) -> dict:
        env = self.envelope(source=source, target=target, msg_type=msg_type, name=name, payload=payload, msg_id=msg_id)
        self.log_json("ipc", env)

        handler = self.handlers.get(target)
        if not handler:
            err = self.envelope(
                source="ipc",
                target=source,
                msg_type="error",
                name="ipc.route_not_found",
                msg_id=env["id"],
                payload={"code": "route_not_found", "detail": f"No handler for target '{target}'", "recoverable": False},
            )
            self.log_error("ipc.route_not_found", err["payload"])
            self.log_json("ipc", err)
            return err

        try:
            response = handler(env)
            if not isinstance(response, dict):
                response = self.envelope(
                    source=target,
                    target=source,
                    msg_type="response",
                    msg_id=env["id"],
                    name=f"{name}.ack",
                    payload={"ok": True},
                )
            self.log_json("ipc", response)
            return response
        except Exception as e:
            err = self.envelope(
                source=target,
                target=source,
                msg_type="error",
                msg_id=env["id"],
                name=f"{name}.failed",
                payload={
                    "code": "exception",
                    "detail": str(e),
                    "recoverable": True,
                    "trace": traceback.format_exc(),
                },
            )
            self.log_error(f"{name}.failed", err["payload"])
            self.log_json("ipc", err)
            return err
