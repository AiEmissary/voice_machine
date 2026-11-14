import json
from pathlib import Path

from app.logging.session_logger import SessionLogger
from app.runtime.voice_back_runtime import VoiceBackRuntime
from app.ui.console_ui import ConsoleUI


def load_config(root: Path) -> dict:
    cfg_path = root / "config" / "runtime.json"
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def main() -> None:
    root = Path(__file__).resolve().parent
    config = load_config(root)

    ui = ConsoleUI(enabled=bool(config.get("ui", {}).get("enabled", True)))
    logger = SessionLogger(root, config)
    voice = str(config.get("tts", {}).get("voice") or "default")
    logger.start_new_session(selected_voice=voice)

    ui.log("voice_back runtime starting...")

    runtime = VoiceBackRuntime(config, logger, ui)
    try:
        runtime.start()
    except KeyboardInterrupt:
        ui.log("Shutting down...")
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
