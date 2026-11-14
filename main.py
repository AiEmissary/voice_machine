from pathlib import Path

from app.logging.session_logger import SessionLogger
from app.runtime.config_loader import load_config
from app.runtime.voice_runtime import VoiceRuntime
from app.ui.main_window import VoiceMachineUI


def main() -> None:
    project_root = Path(__file__).resolve().parent
    config = load_config(project_root)

    logger = SessionLogger(project_root=project_root, config=config)
    logger.start_new_session()

    # UI and runtime are connected via emitter callback.
    runtime_ref = {"runtime": None}

    ui = VoiceMachineUI(runtime=None, app_title="voice_machine")  # type: ignore[arg-type]

    def emit_ui(event_type, payload):
        ui.emit_from_runtime(event_type, payload)

    runtime = VoiceRuntime(config=config, logger=logger, emit_ui=emit_ui)
    runtime_ref["runtime"] = runtime
    ui.runtime = runtime

    logger.info("app_initialized", {"projectRoot": str(project_root)})
    ui.run(auto_start=config.get("app", {}).get("autoStartListening", True) is True)


if __name__ == "__main__":
    main()
