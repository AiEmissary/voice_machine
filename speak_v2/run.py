from __future__ import annotations

import argparse
import atexit
import ctypes
import os
from pathlib import Path

from app.common.logger import AppLogger
from app.common.ipc import LocalMessageBus
from app.services.stt_service import STTService
from app.services.inserter_service import InserterService
from app.bot.strategy_bot import StrategyBot
from app.core.orchestrator import Orchestrator
from app.ui.overlay import OverlayUI


class SingleInstanceGuard:
    def __init__(self, logger, name: str = "SpeakV2OverlaySingleton"):
        self.logger = logger
        self.name = name
        self.handle = None
        self.kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None

    def acquire(self) -> bool:
        if not self.kernel32:
            return True

        self.handle = self.kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
        if not self.handle:
            self.logger.log("single-instance: CreateMutexW failed, continuing without guard")
            return True

        already_running = self.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        if already_running:
            self.logger.log("single-instance: existing Speak V2 instance detected; ignoring second launch")
            return False
        return True

    def release(self):
        if self.kernel32 and self.handle:
            try:
                self.kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


def build_app(base: Path, logger: AppLogger | None = None):
    log_path = base / "logs" / "speak_v2.log"
    memory_path = base / "data" / "strategy_memory.json"

    logger = logger or AppLogger(log_path)
    logger.log("==== Speak V2 boot ====")

    bus = LocalMessageBus(logger=logger)
    stt = STTService(logger=logger)
    inserter = InserterService(logger=logger)
    bot = StrategyBot(memory_path=memory_path)
    orch = Orchestrator(logger=logger, bus=bus, stt_service=stt, inserter_service=inserter, strategy_bot=bot)

    bus.register("stt", stt.handle)
    bus.register("insert", inserter.handle)
    bus.register("orchestrator", orch.handle)

    return logger, bus, inserter


def main():
    parser = argparse.ArgumentParser(description="Speak V2")
    parser.add_argument("--smoke", action="store_true", help="Initialize services and exit")
    parser.add_argument("--test-error", action="store_true", help="Emit a test error log and exit")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    early_logger = AppLogger(base / "logs" / "speak_v2.log")
    guard = SingleInstanceGuard(logger=early_logger)
    if not guard.acquire():
        return
    atexit.register(guard.release)

    logger, bus, inserter = build_app(base, logger=early_logger)

    if args.test_error:
        logger.log_error("test.injected", {"code": "test_error", "detail": "manual test", "recoverable": True})
        print("Speak V2 test error logged")
        return

    if args.smoke:
        logger.log("smoke: initialized")
        print("Speak V2 smoke OK")
        return

    ui = OverlayUI(logger=logger, bus=bus, inserter_service=inserter)
    ui.run()


if __name__ == "__main__":
    main()
