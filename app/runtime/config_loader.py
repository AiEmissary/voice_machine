import json
from pathlib import Path
from typing import Any, Dict


DEFAULT_CONFIG_PATH = Path("config") / "runtime.json"


def load_config(project_root: Path) -> Dict[str, Any]:
    config_path = project_root / DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Missing config file: {config_path}")

    raw = config_path.read_text(encoding="utf-8").lstrip("\ufeff")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Config root must be an object")
    return data
