import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import sounddevice as sd

try:
    from pythonosc import udp_client
except Exception:
    udp_client = None


@dataclass
class DeviceSelection:
    index: Optional[int]
    name: str
    used_fallback: bool
    reason_code: str
    context: Dict[str, Any]


def _normalize(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _is_denied(name: str, deny_patterns: List[str]) -> bool:
    n = _normalize(name)
    for p in deny_patterns:
        if _normalize(p) and _normalize(p) in n:
            return True
    return False


def _score_name(name: str) -> int:
    n = _normalize(name)
    score = 0
    if "obsbot" in n:
        score += 40
    if "microphone" in n or "mic" in n:
        score += 20
    if "steam" in n or "stereo mix" in n or "loopback" in n:
        score -= 30
    return score


def list_input_devices() -> List[Dict[str, Any]]:
    devices = sd.query_devices()
    out: List[Dict[str, Any]] = []
    for idx, dev in enumerate(devices):
        if int(dev.get("max_input_channels") or 0) <= 0:
            continue
        out.append(
            {
                "index": idx,
                "name": str(dev.get("name") or ""),
                "maxInputChannels": int(dev.get("max_input_channels") or 0),
                "defaultSampleRate": int(dev.get("default_samplerate") or 0),
            }
        )
    return out


class ObsbotControl:
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config or {}

    def ensure(self) -> Dict[str, Any]:
        auto_launch = self.cfg.get("autoLaunchControlSoftware") is True
        path = str(self.cfg.get("controlSoftwarePath") or "").strip()
        start_wait_ms = int(self.cfg.get("startWaitMs") or 2500)
        stability_wait_ms = int(self.cfg.get("processStabilityWaitMs") or 1200)

        if not path:
            return {"ok": False, "reason": "obsbot_control_path_missing"}

        if not Path(path).exists():
            return {"ok": False, "reason": "obsbot_control_software_missing", "path": path}

        process_candidates = self._candidate_process_names(path)
        running_name = self._find_running_process_name(process_candidates)
        launch_context: Dict[str, Any] = {
            "attempted": False,
            "attempts": [],
            "processCandidates": process_candidates,
        }

        if not running_name and auto_launch:
            launch_context["attempted"] = True
            launch_paths = self._candidate_launch_paths(path)
            for launch_path in launch_paths:
                launch_result = self._launch_process(launch_path)
                launch_result["path"] = launch_path
                launch_context["attempts"].append(launch_result)
                if not launch_result.get("ok"):
                    continue

                running_name = self._wait_for_any_process(process_candidates, wait_ms=start_wait_ms)
                if running_name:
                    launch_context["launchedPath"] = launch_path
                    break

            if not running_name:
                return {
                    "ok": False,
                    "reason": "obsbot_control_launch_exited_early",
                    "path": path,
                    "process": process_candidates,
                    "launch": launch_context,
                    "waitMs": start_wait_ms,
                }

        if not running_name:
            return {
                "ok": False,
                "reason": "obsbot_control_not_running",
                "path": path,
                "process": process_candidates,
                "launch": launch_context,
            }

        osc_state = self._osc_handshake()
        require_osc = ((self.cfg.get("osc") or {}).get("requireOscControl", False)) is True
        if not osc_state.get("ok") and require_osc:
            return {
                "ok": False,
                "reason": str(osc_state.get("reason") or "obsbot_osc_control_failed"),
                "path": path,
                "process": running_name,
                "launch": launch_context,
                "osc": osc_state,
            }

        if launch_context.get("attempted") and stability_wait_ms > 0:
            time.sleep(max(0.1, min(stability_wait_ms, 5000) / 1000.0))
            running_name = self._find_running_process_name(process_candidates)

        if launch_context.get("attempted") and not running_name:
            return {
                "ok": False,
                "reason": "obsbot_control_launch_exited_early",
                "path": path,
                "process": process_candidates,
                "launch": launch_context,
                "osc": osc_state,
                "stabilityWaitMs": stability_wait_ms,
            }

        return {
            "ok": True,
            "reason": "obsbot_control_running",
            "path": path,
            "process": running_name,
            "launch": launch_context,
            "osc": osc_state,
        }

    def send_wake_command(self, reason: str = "runtime_request") -> Dict[str, Any]:
        state = self.ensure()
        if not state.get("ok"):
            return {
                "ok": False,
                "reason": str(state.get("reason") or "obsbot_control_not_ready"),
                "requestReason": reason,
                "state": state,
            }

        osc_state = state.get("osc") or {}
        return {
            "ok": bool(osc_state.get("ok", True)),
            "reason": str(osc_state.get("reason") or state.get("reason") or "obsbot_osc_control_failed"),
            "requestReason": reason,
            "state": {
                "path": state.get("path"),
                "process": state.get("process"),
            },
            "osc": osc_state,
        }

    def send_resume_command(self, reason: str = "runtime_resume") -> Dict[str, Any]:
        state = self.ensure()
        if not state.get("ok"):
            return {
                "ok": False,
                "reason": str(state.get("reason") or "obsbot_control_not_ready"),
                "requestReason": reason,
                "state": state,
            }

        osc_state = self._osc_resume_handshake()
        return {
            "ok": bool(osc_state.get("ok", False)),
            "reason": str(osc_state.get("reason") or "obsbot_osc_resume_failed"),
            "requestReason": reason,
            "state": {
                "path": state.get("path"),
                "process": state.get("process"),
            },
            "osc": osc_state,
        }

    def force_resume_burst(self, reason: str = "startup_force_resume") -> Dict[str, Any]:
        osc_cfg = self.cfg.get("osc") or {}
        if osc_cfg.get("forceResumeOnLaunch", True) is not True:
            return {
                "ok": True,
                "reason": "obsbot_force_resume_disabled",
                "requestReason": reason,
                "attempts": [],
            }

        burst_count = max(1, int(osc_cfg.get("forceResumeBurstCount") or 3))
        delay_ms = max(0, int(osc_cfg.get("forceResumeBurstDelayMs") or 450))
        attempts: List[Dict[str, Any]] = []

        for idx in range(1, burst_count + 1):
            out = self.send_resume_command(reason=f"{reason}_{idx}")
            attempts.append({"attempt": idx, "result": out})
            if idx < burst_count and delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

        all_ok = all((a.get("result") or {}).get("ok") for a in attempts)
        return {
            "ok": all_ok,
            "reason": "obsbot_force_resume_sent" if all_ok else "obsbot_force_resume_partial_failure",
            "requestReason": reason,
            "attempts": attempts,
            "burstCount": burst_count,
            "burstDelayMs": delay_ms,
        }

    def restart_and_resume(self, reason: str = "restart_recover") -> Dict[str, Any]:
        path = str(self.cfg.get("controlSoftwarePath") or "").strip()
        if not path:
            return {"ok": False, "reason": "obsbot_control_path_missing", "requestReason": reason}

        process_candidates = self._candidate_process_names(path)
        kill_attempts: List[Dict[str, Any]] = []
        for proc in process_candidates:
            try:
                out = subprocess.run(
                    ["taskkill", "/F", "/IM", proc],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=8,
                )
                kill_attempts.append(
                    {
                        "process": proc,
                        "returnCode": int(out.returncode),
                        "stdout": str(out.stdout or "").strip()[:240],
                        "stderr": str(out.stderr or "").strip()[:240],
                    }
                )
            except Exception as exc:
                kill_attempts.append({"process": proc, "error": str(exc)})

        wait_ms = max(200, int((self.cfg.get("restartWaitMs") or 2200)))
        time.sleep(wait_ms / 1000.0)

        ensured = self.ensure()
        if not ensured.get("ok"):
            return {
                "ok": False,
                "reason": str(ensured.get("reason") or "obsbot_control_restart_failed"),
                "requestReason": reason,
                "killAttempts": kill_attempts,
                "ensure": ensured,
                "waitMs": wait_ms,
            }

        resumed = self.send_resume_command(reason=f"{reason}_resume")
        return {
            "ok": bool(resumed.get("ok")),
            "reason": "obsbot_control_restarted_and_resumed" if resumed.get("ok") else "obsbot_control_restart_resume_failed",
            "requestReason": reason,
            "killAttempts": kill_attempts,
            "ensure": {
                "ok": ensured.get("ok"),
                "reason": ensured.get("reason"),
                "process": ensured.get("process"),
            },
            "resume": resumed,
            "waitMs": wait_ms,
        }

    @staticmethod
    def _candidate_process_names(path: str) -> List[str]:
        base = Path(path).name
        candidates = [base, "OBSBOT_Main.exe", "OBSBOT_Center.exe"]
        out: List[str] = []
        for c in candidates:
            if c and c not in out:
                out.append(c)
        return out

    @staticmethod
    def _candidate_launch_paths(path: str) -> List[str]:
        p = Path(path)
        candidates = [str(p)]
        main_path = p.with_name("OBSBOT_Main.exe")
        if main_path.exists():
            candidates.append(str(main_path))
        out: List[str] = []
        for c in candidates:
            if c not in out:
                out.append(c)
        return out

    @staticmethod
    def _find_running_process_name(process_names: List[str]) -> Optional[str]:
        for name in process_names:
            if ObsbotControl._is_process_running(name):
                return name
        return None

    @staticmethod
    def _wait_for_any_process(process_names: List[str], wait_ms: int) -> Optional[str]:
        wait_ms = max(200, min(wait_ms, 12000))
        until = time.time() + (wait_ms / 1000.0)
        while time.time() < until:
            running = ObsbotControl._find_running_process_name(process_names)
            if running:
                return running
            time.sleep(0.1)
        return ObsbotControl._find_running_process_name(process_names)

    def _osc_client(self):
        cfg = self.cfg.get("osc") or {}
        enabled = cfg.get("enabled", False) is True
        if not enabled:
            return None, None, {"ok": True, "reason": "obsbot_osc_disabled"}

        if udp_client is None:
            return None, None, {"ok": False, "reason": "obsbot_osc_dependency_missing"}

        host = str(cfg.get("host") or "127.0.0.1").strip() or "127.0.0.1"
        try:
            port = int(cfg.get("port") or 16284)
        except Exception:
            return None, None, {"ok": False, "reason": "obsbot_osc_port_invalid", "port": cfg.get("port")}

        try:
            client = udp_client.SimpleUDPClient(host, port)
            return client, {"host": host, "port": port}, None
        except Exception as exc:
            return None, None, {"ok": False, "reason": "obsbot_osc_send_failed", "host": host, "port": port, "error": str(exc)}

    def _wake_indices(self) -> List[int]:
        cfg = self.cfg.get("osc") or {}
        default_index = int(cfg.get("wakeDeviceIndex") or 0)
        raw_indices = cfg.get("wakeDeviceIndices")
        if isinstance(raw_indices, list):
            wake_indices: List[int] = []
            for raw in raw_indices:
                try:
                    idx = int(raw)
                except Exception:
                    continue
                if idx < 0 or idx in wake_indices:
                    continue
                wake_indices.append(idx)
            if wake_indices:
                return wake_indices
        return [default_index]

    def _osc_send_commands(self, commands: List[Dict[str, Any]], reason: str) -> Dict[str, Any]:
        client, endpoint, err = self._osc_client()
        if err:
            return err
        if not client or not endpoint:
            return {"ok": True, "reason": "obsbot_osc_disabled"}
        if not commands:
            return {"ok": True, "reason": "obsbot_osc_noop", **endpoint}

        delay = max(0.0, float((self.cfg.get("osc") or {}).get("interCommandDelayMs") or 30) / 1000.0)
        try:
            for cmd in commands:
                client.send_message(cmd["path"], cmd["args"])
                if delay > 0:
                    time.sleep(delay)
            return {"ok": True, "reason": reason, **endpoint, "commands": commands}
        except Exception as exc:
            return {"ok": False, "reason": "obsbot_osc_send_failed", **endpoint, "error": str(exc), "commands": commands}

    def _osc_handshake(self) -> Dict[str, Any]:
        cfg = self.cfg.get("osc") or {}
        commands: List[Dict[str, Any]] = []
        if cfg.get("sendConnectedPing", True) is True:
            commands.append({"path": "/OBSBOT/WebCam/General/Connected", "args": [0]})

        if cfg.get("sendWakeCommand", True) is True:
            wake_state = int(cfg.get("wakeState") or 1)
            for device_index in self._wake_indices():
                commands.append({"path": "/OBSBOT/WebCam/General/WakeSleep", "args": [device_index, wake_state]})

        if cfg.get("selectDeviceOnStart", True) is True:
            select_index = int(cfg.get("wakeDeviceIndex") or 0)
            commands.append({"path": "/OBSBOT/WebCam/General/SelectDevice", "args": [select_index]})

        return self._osc_send_commands(commands, reason="obsbot_osc_commands_sent")

    def _osc_resume_handshake(self) -> Dict[str, Any]:
        cfg = self.cfg.get("osc") or {}
        commands: List[Dict[str, Any]] = []
        if cfg.get("sendConnectedPing", True) is True:
            commands.append({"path": "/OBSBOT/WebCam/General/Connected", "args": [0]})

        pulse_sleep_ms = max(0, int(cfg.get("resumePulseSleepMs") or 200))
        wake_state = int(cfg.get("wakeState") or 1)
        sleep_state = 0 if wake_state != 0 else 1

        for device_index in self._wake_indices():
            commands.append({"path": "/OBSBOT/WebCam/General/WakeSleep", "args": [device_index, sleep_state]})
            if pulse_sleep_ms > 0:
                commands.append({"path": "__sleep__", "args": [pulse_sleep_ms]})
            commands.append({"path": "/OBSBOT/WebCam/General/WakeSleep", "args": [device_index, wake_state]})

        if cfg.get("selectDeviceOnStart", True) is True:
            select_index = int(cfg.get("wakeDeviceIndex") or 0)
            commands.append({"path": "/OBSBOT/WebCam/General/SelectDevice", "args": [select_index]})

        client, endpoint, err = self._osc_client()
        if err:
            return err
        if not client or not endpoint:
            return {"ok": True, "reason": "obsbot_osc_disabled"}

        delay = max(0.0, float((cfg.get("interCommandDelayMs") or 30)) / 1000.0)
        filtered_commands: List[Dict[str, Any]] = []
        try:
            for cmd in commands:
                if cmd.get("path") == "__sleep__":
                    ms = int((cmd.get("args") or [0])[0])
                    if ms > 0:
                        time.sleep(ms / 1000.0)
                    continue
                client.send_message(cmd["path"], cmd["args"])
                filtered_commands.append(cmd)
                if delay > 0:
                    time.sleep(delay)
            return {"ok": True, "reason": "obsbot_osc_resume_commands_sent", **endpoint, "commands": filtered_commands}
        except Exception as exc:
            return {"ok": False, "reason": "obsbot_osc_send_failed", **endpoint, "error": str(exc), "commands": filtered_commands}

    @staticmethod
    def _launch_process(path: str) -> Dict[str, Any]:
        try:
            exe_path = Path(path)
            cwd = str(exe_path.parent) if exe_path.parent.exists() else None
            proc = subprocess.Popen(
                [str(exe_path)],
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"ok": True, "pid": proc.pid, "cwd": cwd}
        except Exception as exc:
            return {"ok": False, "reason": "obsbot_control_launch_failed", "error": str(exc)}

    @staticmethod
    def _is_process_running(exe_name: str) -> bool:
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
            )
            return exe_name.lower() in out.lower()
        except Exception:
            return False


class DeviceResolver:
    def __init__(self, audio_cfg: Dict[str, Any]):
        self.audio_cfg = audio_cfg or {}

    def resolve_fallback_candidates(self, limit: int = 8) -> List[Dict[str, Any]]:
        devices = list_input_devices()
        fallback_cfg = (self.audio_cfg.get("fallbackPolicy") or {})
        deny_patterns = list(fallback_cfg.get("denyDeviceNamePatterns") or [])
        preferred_label = _normalize(str(self.audio_cfg.get("preferredInputDeviceLabel") or ""))

        base_candidates = [d for d in devices if not _is_denied(d["name"], deny_patterns)]
        non_preferred = [
            d for d in base_candidates
            if not preferred_label or preferred_label not in _normalize(d.get("name", ""))
        ]
        candidates = non_preferred if non_preferred else base_candidates

        ranked = sorted(
            candidates,
            key=lambda d: (
                -_score_name(d["name"]),
                -(d.get("maxInputChannels") or 0),
                -len(str(d.get("name") or "")),
                d.get("index") or 0,
            ),
        )
        return ranked[: max(1, int(limit))]

    def resolve(self) -> DeviceSelection:
        devices = list_input_devices()
        preferred_label = str(self.audio_cfg.get("preferredInputDeviceLabel") or "").strip()
        match_strategy = str(self.audio_cfg.get("inputDeviceMatchStrategy") or "contains").lower()
        fallback_cfg = (self.audio_cfg.get("fallbackPolicy") or {})
        allow_fallback_on_missing = fallback_cfg.get("allowDefaultOnPreferredFailure", True) is True
        deny_patterns = list(fallback_cfg.get("denyDeviceNamePatterns") or [])

        candidates = [d for d in devices if not _is_denied(d["name"], deny_patterns)]

        if preferred_label:
            matched = self._match(preferred_label, match_strategy, candidates)
            if matched:
                best = self._pick_best(matched)
                return DeviceSelection(
                    index=best["index"],
                    name=best["name"],
                    used_fallback=False,
                    reason_code="preferred_input_selected",
                    context={
                        "preferredLabel": preferred_label,
                        "matchStrategy": match_strategy,
                        "inputDevice": best,
                        "availableCount": len(devices),
                    },
                )

            if not allow_fallback_on_missing:
                return DeviceSelection(
                    index=None,
                    name="",
                    used_fallback=False,
                    reason_code="preferred_input_not_found",
                    context={
                        "preferredLabel": preferred_label,
                        "matchStrategy": match_strategy,
                        "availableCount": len(devices),
                        "deviceNames": [d["name"] for d in devices],
                    },
                )

        if not candidates:
            return DeviceSelection(
                index=None,
                name="",
                used_fallback=True,
                reason_code="no_input_devices_available",
                context={"availableCount": len(devices)},
            )

        best = self._pick_best(candidates)
        return DeviceSelection(
            index=best["index"],
            name=best["name"],
            used_fallback=True,
            reason_code="fallback_input_selected",
            context={
                "preferredLabel": preferred_label,
                "availableCount": len(devices),
                "selected": best,
            },
        )

    def resolve_fallback(self) -> DeviceSelection:
        devices = list_input_devices()
        candidates = self.resolve_fallback_candidates(limit=1)
        if not candidates:
            return DeviceSelection(
                index=None,
                name="",
                used_fallback=True,
                reason_code="no_fallback_input_available",
                context={"availableCount": len(devices)},
            )

        best = candidates[0]
        return DeviceSelection(
            index=best["index"],
            name=best["name"],
            used_fallback=True,
            reason_code="fallback_input_selected_after_silent_preferred",
            context={"selected": best, "availableCount": len(devices)},
        )

    def resolve_preferred_candidates(self, limit: int = 8) -> List[Dict[str, Any]]:
        devices = list_input_devices()
        preferred_label = str(self.audio_cfg.get("preferredInputDeviceLabel") or "").strip()
        if not preferred_label:
            return []

        match_strategy = str(self.audio_cfg.get("inputDeviceMatchStrategy") or "contains").lower()
        fallback_cfg = (self.audio_cfg.get("fallbackPolicy") or {})
        deny_patterns = list(fallback_cfg.get("denyDeviceNamePatterns") or [])

        candidates = [d for d in devices if not _is_denied(d["name"], deny_patterns)]
        matched = self._match(preferred_label, match_strategy, candidates)
        ranked = sorted(
            matched,
            key=lambda d: (
                -_score_name(d["name"]),
                -(d.get("maxInputChannels") or 0),
                -len(str(d.get("name") or "")),
                d.get("index") or 0,
            ),
        )
        return ranked[: max(1, int(limit))]

    @staticmethod
    def _match(label: str, strategy: str, devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        wanted = _normalize(label)
        if not wanted:
            return []

        out = []
        for d in devices:
            name = _normalize(d["name"])
            if strategy == "exact" and name == wanted:
                out.append(d)
            elif strategy == "startswith" and name.startswith(wanted):
                out.append(d)
            elif strategy == "contains" and wanted in name:
                out.append(d)
        return out

    @staticmethod
    def _pick_best(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        return sorted(
            candidates,
            key=lambda d: (
                -_score_name(d["name"]),
                -(d.get("maxInputChannels") or 0),
                -len(str(d.get("name") or "")),
                d.get("index") or 0,
            ),
        )[0]
