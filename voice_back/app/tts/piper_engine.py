import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional


class PiperEngine:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self.piper_path = str(self.cfg.get("piperPath") or "piper")
        self.model_path = str(self.cfg.get("piperModelPath") or "").strip()
        self.config_path = str(self.cfg.get("piperConfigPath") or "").strip()
        self.timeout_ms = int(self.cfg.get("piperTimeoutMs") or 20000)

    def available(self) -> bool:
        return bool(self.model_path)

    def synthesize(self, text: str, output_path: Path) -> Dict[str, Any]:
        if not self.model_path:
            return {"ok": False, "reason": "piper_model_missing"}

        cmd = [self.piper_path, "--model", self.model_path, "--output_file", str(output_path)]
        if self.config_path:
            cmd += ["--config", self.config_path]

        started = time.time()
        try:
            result = subprocess.run(
                cmd,
                input=text,
                text=True,
                capture_output=True,
                timeout=max(1, int(self.timeout_ms / 1000)),
                encoding="utf-8",
            )
            latency_ms = int((time.time() - started) * 1000)
            if result.returncode != 0:
                return {
                    "ok": False,
                    "reason": "piper_failed",
                    "stderr": (result.stderr or "").strip()[:400],
                    "latencyMs": latency_ms,
                }
            return {"ok": True, "reason": "ok", "latencyMs": latency_ms}
        except FileNotFoundError:
            return {"ok": False, "reason": "piper_not_found"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "piper_timeout"}
        except Exception as exc:
            return {"ok": False, "reason": "piper_error", "error": str(exc)}
