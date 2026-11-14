import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

import numpy as np

from app.audio.audio_stream import AudioInputStream
from app.audio.device_manager import DeviceResolver, ObsbotControl
from app.logging.session_logger import SessionLogger
from app.stt.faster_whisper_engine import FasterWhisperEngine
from app.wake.openwakeword_engine import OpenWakeWordEngine
from app.wake.wake_word_listener import contains_wake_word, strip_wake_word


UiEmitter = Callable[[str, Dict[str, Any]], None]


class VoiceRuntime:
    def __init__(self, config: Dict[str, Any], logger: SessionLogger, emit_ui: UiEmitter):
        self.config = config
        self.logger = logger
        self.emit_ui = emit_ui

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False

        audio_cfg = self.config.get("audio", {})
        self.sample_rate = int(audio_cfg.get("sampleRate", 16000))
        self.channels = int(audio_cfg.get("channels", 1))
        self.block_size = int(audio_cfg.get("blockSize", 1600))

        cap_cfg = self.config.get("capture", {})
        self.max_command_seconds = float(cap_cfg.get("maxCommandSeconds", 8.0))
        self.min_command_seconds = float(cap_cfg.get("minCommandSeconds", 0.6))
        self.silence_hangover_ms = int(cap_cfg.get("silenceHangoverMs", 1000))
        self.speech_rms_threshold = float(cap_cfg.get("speechRmsThreshold", 0.018))
        self.silence_rms_threshold = float(cap_cfg.get("silenceRmsThreshold", 0.010))
        self.prewake_keep_ms = int(cap_cfg.get("preWakeKeepMs", 900))
        self.no_speech_finalize_ms = int(cap_cfg.get("noSpeechFinalizeMs", 1400))
        self.slow_wake_probe_ms = int(cap_cfg.get("slowWakeProbeMs", 2500))
        self.slow_wake_prewake_keep_ms = int(cap_cfg.get("slowWakePreKeepMs", 2600))

        wake_cfg = self.config.get("wake", {})
        self.wake_word = str(wake_cfg.get("wakeWord") or "andromeda")
        self.wake_aliases = list(wake_cfg.get("wakeAliases") or [])
        self.wake_fallback_interval_ms = int(wake_cfg.get("transcriptFallbackIntervalMs", 1800))
        self.enable_transcript_fallback = wake_cfg.get("enableTranscriptFallback", True) is True
        self.wake_fallback_recent_speech_ms = int(wake_cfg.get("transcriptFallbackRecentSpeechMs", 2600))
        self.wake_fallback_max_audio_seconds = float(wake_cfg.get("transcriptFallbackMaxAudioSeconds", 1.8))
        self.wake_fallback_min_rms = float(
            wake_cfg.get("transcriptFallbackMinRms", max(0.006, self.silence_rms_threshold))
        )
        self.wake_fallback_min_speech_ms = int(wake_cfg.get("transcriptFallbackMinSpeechMs", 320))

        fallback_cfg = audio_cfg.get("fallbackPolicy", {})
        self.allow_default_on_silent_preferred = fallback_cfg.get("allowDefaultOnSilentPreferred", True) is True
        obsbot_cfg = audio_cfg.get("obsbotControl", {})
        self.require_obsbot_control_ready = obsbot_cfg.get("requireControlSoftwareRunning", True) is True

        diag_cfg = self.config.get("diagnostics", {})
        self.silence_no_frames_timeout_ms = int(diag_cfg.get("silenceNoFramesTimeoutMs", 1800))
        self.startup_no_callback_retries = int(diag_cfg.get("startupNoCallbackRetries", 2))
        self.startup_retry_delay_ms = int(diag_cfg.get("startupRetryDelayMs", 850))
        self.runtime_no_audio_timeout_ms = int(diag_cfg.get("runtimeNoAudioTimeoutMs", 4500))
        self.runtime_recover_cooldown_ms = int(diag_cfg.get("runtimeRecoverCooldownMs", 2500))
        self.runtime_max_recover_failures = int(diag_cfg.get("runtimeMaxRecoverFailures", 4))
        self.restart_obsbot_on_persistent_no_callbacks = (
            diag_cfg.get("restartObsbotOnPersistentNoCallbacks", True) is True
        )
        self.restart_obsbot_settle_ms = int(diag_cfg.get("restartObsbotSettleMs", 2600))

        osc_cfg = (obsbot_cfg.get("osc") or {})
        self.keep_awake_enabled = osc_cfg.get("keepAwakeEnabled", True) is True
        self.keep_awake_interval_ms = int(osc_cfg.get("keepAwakeIntervalMs", 45000))
        self.force_resume_settle_ms = int(osc_cfg.get("forceResumeSettleMs", 2200))

        self.stream = AudioInputStream(self.sample_rate, self.channels, self.block_size)
        self.device_resolver = DeviceResolver(audio_cfg)
        self.obsbot_control = ObsbotControl(audio_cfg.get("obsbotControl") or {})
        self.stt_engine = FasterWhisperEngine(self.config.get("stt", {}))
        self.wake_engine = OpenWakeWordEngine(
            enabled=wake_cfg.get("enableOpenWakeWord", True),
            threshold=float(wake_cfg.get("openWakeWordThreshold", 0.5)),
            model_paths=list(wake_cfg.get("openWakeWordModelPaths") or []),
            load_default_models=wake_cfg.get("openWakeWordLoadDefaultModels", True) is True,
        )

        self.input_device_name = ""
        self.mode = "idle"  # idle | listening | capturing | transcribing
        self.wake_detected_at = 0.0
        self.wake_score: Optional[float] = None
        self._startup_restart_recover_used = False

    def start(self) -> bool:
        if self._running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voice-runtime", daemon=True)
        self._thread.start()
        return True

    def stop(self, join_timeout: float = 3.0) -> None:
        self._stop.set()
        self._running = False
        try:
            self.stream.stop()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)
        self._set_status(mic="off", wake="idle", capture="idle", transcribe="idle")

    def _run(self) -> None:
        self._running = True
        self._set_status(mic="starting", wake="idle", capture="idle", transcribe="idle")

        obsbot_state = self.obsbot_control.ensure()
        self.logger.info("obsbot_control_check", obsbot_state)
        if not obsbot_state.get("ok"):
            self.logger.failure(obsbot_state.get("reason", "obsbot_control_not_ready"), obsbot_state, status="blocked")
            self.emit_ui("error", {"message": f"OBSBOT control: {obsbot_state.get('reason')}"})
            if self.require_obsbot_control_ready:
                self._set_status(mic="error", wake="idle", capture="idle", transcribe="idle")
                self._running = False
                return
        else:
            force_resume = self.obsbot_control.force_resume_burst(reason="startup_force_resume")
            self.logger.info("obsbot_force_resume", force_resume)
            if not force_resume.get("ok"):
                self.logger.failure(
                    "obsbot_force_resume_partial_failure",
                    force_resume,
                    status="blocked",
                )
            if self.force_resume_settle_ms > 0:
                self.emit_ui("log", {"message": f"Waiting {self.force_resume_settle_ms}ms for OBSBOT resume settle..."})
                time.sleep(self.force_resume_settle_ms / 1000.0)
                self.logger.info("obsbot_force_resume_settle", {"waitMs": self.force_resume_settle_ms})

        sel = self.device_resolver.resolve()
        if sel.index is None:
            self.logger.failure(sel.reason_code, sel.context, status="blocked")
            self.emit_ui("error", {"message": f"Audio input blocked: {sel.reason_code}"})
            self._set_status(mic="error", wake="idle", capture="idle", transcribe="idle")
            self._running = False
            return

        if not self._start_stream_with_selection(sel):
            self._running = False
            return

        stt_out = self.stt_engine.load()
        if not stt_out.get("ok"):
            self.logger.failure("stt_model_load_failed", stt_out, status="error")
            self.emit_ui("error", {"message": "Failed to load STT model."})
            self._set_status(mic="listening", wake="degraded", capture="idle", transcribe="error")
        else:
            self.logger.info("stt_loaded", stt_out)
            self._set_status(mic="listening", wake="waiting", capture="idle", transcribe="ready")

        wake_status = self.wake_engine.load()
        self.logger.info("audio_wake_status", wake_status)

        wake_ring: Deque[np.ndarray] = deque()
        wake_ring_samples = 0
        max_wake_ring_samples = int(self.sample_rate * 2.4)
        max_wake_fallback_samples = max(1, int(self.sample_rate * self.wake_fallback_max_audio_seconds))
        last_wake_fallback_ts = 0.0
        last_speech_like_ts = 0.0
        speech_run_started_ts = 0.0
        speech_run_peak_rms = 0.0
        pending_wake_probe = False
        pending_speech_run_ms = 0
        pending_speech_run_peak_rms = 0.0

        capture_chunks: List[np.ndarray] = []
        speech_started = False
        last_speech_ts = 0.0
        capture_no_speech_finalize_ms = 0

        self.mode = "listening"
        last_audio_chunk_ts = time.time()
        last_keep_awake_ts = 0.0
        last_recover_attempt_ts = 0.0
        recover_failures = 0

        while not self._stop.is_set():
            now = time.time()
            if self.keep_awake_enabled and self.keep_awake_interval_ms > 0:
                if (now - last_keep_awake_ts) * 1000 >= self.keep_awake_interval_ms:
                    keep_awake = self.obsbot_control.send_wake_command(reason="runtime_keep_awake")
                    self.logger.info("obsbot_keep_awake", keep_awake)
                    last_keep_awake_ts = now

            chunk = self.stream.read_chunk(timeout=0.25)
            now = time.time()
            if chunk is None:
                stale_ms = (now - last_audio_chunk_ts) * 1000
                recover_cooldown_elapsed = (now - last_recover_attempt_ts) * 1000 >= self.runtime_recover_cooldown_ms
                if stale_ms >= self.runtime_no_audio_timeout_ms and recover_cooldown_elapsed:
                    last_recover_attempt_ts = now
                    recovered = self._recover_stream_after_stall(stale_ms=stale_ms)
                    if recovered:
                        recover_failures = 0
                        self.mode = "listening"
                        wake_ring.clear()
                        wake_ring_samples = 0
                        capture_chunks = []
                        speech_started = False
                        last_audio_chunk_ts = time.time()
                        continue

                    recover_failures += 1
                    if recover_failures >= self.runtime_max_recover_failures:
                        self.logger.failure(
                            "runtime_audio_stream_recovery_exhausted",
                            {
                                "recoverFailures": recover_failures,
                                "staleMs": int(stale_ms),
                                "maxRecoverFailures": self.runtime_max_recover_failures,
                                "inputDeviceName": self.input_device_name,
                            },
                            status="blocked",
                        )
                        self.emit_ui("error", {"message": "OBSBOT audio stalled repeatedly; restart OBSBOT Center/device."})
                        self._set_status(mic="error", wake="idle", capture="idle", transcribe="idle")
                        break
                continue

            last_audio_chunk_ts = now

            if self.mode == "listening":
                wake_ring.append(chunk.samples)
                wake_ring_samples += int(chunk.samples.size)
                while wake_ring_samples > max_wake_ring_samples and wake_ring:
                    old = wake_ring.popleft()
                    wake_ring_samples -= int(old.size)

                if chunk.rms >= self.wake_fallback_min_rms:
                    if speech_run_started_ts <= 0:
                        speech_run_started_ts = now
                        speech_run_peak_rms = chunk.rms
                    else:
                        speech_run_peak_rms = max(speech_run_peak_rms, chunk.rms)
                    last_speech_like_ts = now
                elif speech_run_started_ts > 0:
                    speech_run_ms = int((now - speech_run_started_ts) * 1000)
                    if speech_run_ms >= self.wake_fallback_min_speech_ms:
                        pending_wake_probe = True
                        pending_speech_run_ms = speech_run_ms
                        pending_speech_run_peak_rms = speech_run_peak_rms
                    speech_run_started_ts = 0.0
                    speech_run_peak_rms = 0.0

                # First stage: audio wake detection (if available)
                wake_pred = self.wake_engine.predict(
                    chunk.samples,
                    wake_targets=[self.wake_word, *self.wake_aliases],
                )
                if wake_pred.get("available") and wake_pred.get("triggered"):
                    capture_no_speech_finalize_ms = 0
                    capture_chunks = self._on_wake_detected(float(wake_pred.get("score") or 0.0), "audio_wake")
                    speech_started = False
                    last_speech_ts = now
                    pending_wake_probe = False
                    pending_speech_run_ms = 0
                    pending_speech_run_peak_rms = 0.0
                    speech_run_started_ts = 0.0
                    speech_run_peak_rms = 0.0
                    self.mode = "capturing"
                    continue

                # Fallback: transcript wake gate over short rolling buffer.
                # Trigger once per speech burst after short silence.
                speech_recent = (
                    last_speech_like_ts > 0
                    and (now - last_speech_like_ts) * 1000 <= self.wake_fallback_recent_speech_ms
                )
                fallback_due = (now - last_wake_fallback_ts) * 1000 >= self.wake_fallback_interval_ms
                if self.enable_transcript_fallback and pending_wake_probe and speech_recent and fallback_due:
                    pending_wake_probe = False
                    last_wake_fallback_ts = now

                    wake_audio = self._merge_chunks(list(wake_ring))
                    if wake_audio.size > max_wake_fallback_samples:
                        wake_audio = wake_audio[-max_wake_fallback_samples:]

                    wake_started = time.time()
                    wake_stt = self.stt_engine.transcribe(wake_audio, purpose="wake")
                    wake_ms = int((time.time() - wake_started) * 1000)

                    wake_check = {"detected": False, "score": None, "matchedPhrase": None}
                    if wake_stt.get("success"):
                        wake_check = contains_wake_word(
                            transcript=str(wake_stt.get("transcript") or ""),
                            wake_word=self.wake_word,
                            aliases=self.wake_aliases,
                        )

                    if wake_check.get("detected"):
                        self.logger.info(
                            "wake_fallback_probe",
                            {
                                "detected": True,
                                "wakeScore": wake_check.get("score"),
                                "wakeMs": wake_ms,
                                "audioMs": int((wake_audio.size / self.sample_rate) * 1000),
                                "speechRecentMs": int((now - last_speech_like_ts) * 1000),
                                "speechRunMs": pending_speech_run_ms,
                                "speechRunPeakRms": round(float(pending_speech_run_peak_rms), 6),
                                "transcript": str(wake_stt.get("transcript") or "")[:120],
                                "sttModel": wake_stt.get("model"),
                                "sttDevice": wake_stt.get("device"),
                                "sttInputRms": wake_stt.get("inputRms"),
                                "sttAppliedGain": wake_stt.get("appliedGain"),
                            },
                        )
                        pending_speech_run_ms = 0
                        pending_speech_run_peak_rms = 0.0
                        capture_no_speech_finalize_ms = (
                            self.no_speech_finalize_ms if wake_ms >= self.slow_wake_probe_ms else 0
                        )
                        capture_chunks = self._on_wake_detected(
                            float(wake_check.get("score") or 0.7),
                            "transcript_wake",
                            wake_probe_ms=wake_ms,
                        )
                        speech_started = False
                        last_speech_ts = now
                        self.mode = "capturing"
                        continue

                    # Log only slow/failed probes to keep diagnostics useful, not noisy.
                    if wake_ms >= 1500 or not wake_stt.get("success"):
                        self.logger.info(
                            "wake_fallback_probe",
                            {
                                "detected": False,
                                "wakeMs": wake_ms,
                                "audioMs": int((wake_audio.size / self.sample_rate) * 1000),
                                "speechRecentMs": int((now - last_speech_like_ts) * 1000),
                                "speechRunMs": pending_speech_run_ms,
                                "speechRunPeakRms": round(float(pending_speech_run_peak_rms), 6),
                                "sttSuccess": bool(wake_stt.get("success")),
                                "sttReason": wake_stt.get("reason"),
                                "transcript": str(wake_stt.get("transcript") or "")[:120],
                                "sttModel": wake_stt.get("model"),
                                "sttDevice": wake_stt.get("device"),
                                "sttInputRms": wake_stt.get("inputRms"),
                                "sttAppliedGain": wake_stt.get("appliedGain"),
                            },
                        )
                    pending_speech_run_ms = 0
                    pending_speech_run_peak_rms = 0.0

            elif self.mode == "capturing":
                capture_chunks.append(chunk.samples)
                self._set_status(mic="listening", wake="detected", capture="capturing", transcribe="idle")

                if chunk.rms >= self.speech_rms_threshold:
                    speech_started = True
                    last_speech_ts = now
                elif speech_started:
                    # Finalize once speech has been absent for long enough.
                    # Do not require rms to fall below silence threshold, because
                    # low-level noise in the dead zone can otherwise block finalize.
                    no_speech_ms = (now - last_speech_ts) * 1000
                    if no_speech_ms >= self.silence_hangover_ms and chunk.rms < self.speech_rms_threshold:
                        result = self._finalize_command_capture(capture_chunks)
                        self._emit_result(result)
                        self.mode = "listening"
                        wake_ring.clear()
                        wake_ring_samples = 0
                        capture_no_speech_finalize_ms = 0
                        continue

                elapsed_ms = (now - self.wake_detected_at) * 1000
                if (
                    not speech_started
                    and capture_no_speech_finalize_ms > 0
                    and elapsed_ms >= capture_no_speech_finalize_ms
                ):
                    self.logger.info(
                        "capture_no_new_speech_finalize",
                        {
                            "elapsedMs": int(elapsed_ms),
                            "timeoutMs": int(capture_no_speech_finalize_ms),
                            "capturedSeconds": round(float(self._merge_chunks(capture_chunks).size / self.sample_rate), 3),
                            "wakeScore": self.wake_score,
                        },
                    )
                    result = self._finalize_command_capture(capture_chunks)
                    self._emit_result(result)
                    self.mode = "listening"
                    wake_ring.clear()
                    wake_ring_samples = 0
                    capture_no_speech_finalize_ms = 0
                    continue

                elapsed = now - self.wake_detected_at
                if elapsed >= self.max_command_seconds:
                    result = self._finalize_command_capture(capture_chunks)
                    self._emit_result(result)
                    self.mode = "listening"
                    wake_ring.clear()
                    wake_ring_samples = 0
                    capture_no_speech_finalize_ms = 0
                    continue

        self.stream.stop()
        self._running = False

    def _start_stream_with_selection(self, sel) -> bool:
        timeout_ms = self.silence_no_frames_timeout_ms
        retry_count = max(0, int(self.startup_no_callback_retries))
        retry_delay_s = max(0.05, int(self.startup_retry_delay_ms) / 1000.0)

        def normalize_rate(raw: Any) -> Optional[int]:
            try:
                r = int(float(raw))
            except Exception:
                return None
            if r < 8000 or r > 192000:
                return None
            return r

        def candidate_rates(device_default_rate: Any) -> List[int]:
            rates = [int(self.sample_rate)]
            d = normalize_rate(device_default_rate)
            if d is not None and d not in rates:
                rates.append(d)
            return rates

        def source_block_size_for_rate(source_rate: int) -> int:
            if source_rate <= 0:
                return self.block_size
            scaled = int(round(self.block_size * (float(source_rate) / float(self.sample_rate))))
            return max(self.block_size, scaled)

        def try_stream(device_index, device_name: str, device_default_rate: Any = None, probe_timeout_ms: int = timeout_ms):
            probe_attempts: List[Dict[str, Any]] = []
            total_tries = 1 + retry_count
            rates = candidate_rates(device_default_rate)
            last_error: Optional[str] = None

            for attempt in range(1, total_tries + 1):
                for source_rate in rates:
                    fixed_block_size = source_block_size_for_rate(source_rate)
                    block_sizes = [0, fixed_block_size]
                    seen_blocks = set()

                    for source_block_size in block_sizes:
                        if source_block_size in seen_blocks:
                            continue
                        seen_blocks.add(source_block_size)

                        try:
                            self.stream.start(
                                device_index,
                                device_name,
                                sample_rate_override=source_rate,
                                block_size_override=source_block_size,
                            )
                        except Exception as exc:
                            last_error = str(exc)
                            probe_attempts.append(
                                {
                                    "attempt": attempt,
                                    "sourceSampleRate": source_rate,
                                    "sourceBlockSize": source_block_size,
                                    "result": f"start_error:{exc}",
                                }
                            )
                            continue

                        ok = self.stream.wait_for_callbacks(timeout_ms=probe_timeout_ms)
                        if ok:
                            probe_attempts.append(
                                {
                                    "attempt": attempt,
                                    "sourceSampleRate": source_rate,
                                    "sourceBlockSize": source_block_size,
                                    "result": "ok",
                                }
                            )
                            return True, None, probe_attempts

                        self.stream.stop()
                        probe_attempts.append(
                            {
                                "attempt": attempt,
                                "sourceSampleRate": source_rate,
                                "sourceBlockSize": source_block_size,
                                "result": f"no_callbacks:{device_index}",
                            }
                        )

                if attempt < total_tries:
                    wake_retry = self.obsbot_control.send_resume_command(reason="startup_no_callbacks_retry")
                    self.logger.info(
                        "obsbot_wake_retry",
                        {
                            "attempt": attempt,
                            "deviceIndex": device_index,
                            "deviceName": device_name,
                            "sourceRatesTried": rates,
                            "wake": wake_retry,
                        },
                    )
                    time.sleep(retry_delay_s)

            if last_error and all(str(p.get("result") or "").startswith("start_error:") for p in probe_attempts):
                return False, f"start_error:{last_error}", probe_attempts
            return False, f"no_callbacks:{device_index}", probe_attempts

        sel_default_rate = (((sel.context or {}).get("inputDevice") or {}).get("defaultSampleRate"))
        ok, err, probe_attempts = try_stream(sel.index, sel.name, device_default_rate=sel_default_rate)
        if ok:
            self.input_device_name = sel.name
            self.logger.info("mic_ready", {
                "inputDevice": sel.index,
                "inputDeviceName": sel.name,
                "selectionReason": sel.reason_code,
                "usedFallback": sel.used_fallback,
                "sourceSampleRate": self.stream.source_sample_rate,
                "sourceBlockSize": self.stream.source_block_size,
                "targetSampleRate": self.sample_rate,
                "probeAttempts": probe_attempts,
            })
            self.emit_ui("log", {"message": f"Mic ready: {sel.name}"})
            return True

        attempts: List[Dict[str, Any]] = [{
            "index": sel.index,
            "name": sel.name,
            "result": err,
            "selectionReason": sel.reason_code,
            "probeAttempts": probe_attempts,
        }]

        # Strict OBSBOT path: try alternate preferred-matching OBSBOT endpoints before declaring failure.
        preferred_candidates = self.device_resolver.resolve_preferred_candidates(limit=8)
        for cand in preferred_candidates:
            cand_idx = cand.get("index")
            if cand_idx == sel.index:
                continue

            cand_name = str(cand.get("name") or "")
            cand_default_rate = cand.get("defaultSampleRate")
            cand_ok, cand_err, cand_probe_attempts = try_stream(
                cand_idx,
                cand_name,
                device_default_rate=cand_default_rate,
            )
            attempts.append({
                "index": cand_idx,
                "name": cand_name,
                "result": cand_err or "ok",
                "probeAttempts": cand_probe_attempts,
            })
            if not cand_ok:
                continue

            self.input_device_name = cand_name
            self.logger.info("mic_ready", {
                "inputDevice": cand_idx,
                "inputDeviceName": cand_name,
                "selectionReason": "preferred_input_selected_alternate",
                "usedFallback": False,
                "alternateFromIndex": sel.index,
                "sourceSampleRate": self.stream.source_sample_rate,
                "sourceBlockSize": self.stream.source_block_size,
                "targetSampleRate": self.sample_rate,
                "probeAttempts": cand_probe_attempts,
            })
            self.emit_ui("log", {"message": f"Mic ready (alternate OBSBOT endpoint): {cand_name}"})
            return True

        if self.restart_obsbot_on_persistent_no_callbacks and not self._startup_restart_recover_used:
            self._startup_restart_recover_used = True
            self.emit_ui("log", {"message": "No OBSBOT callbacks; restarting OBSBOT Center and retrying once..."})
            restart_state = self.obsbot_control.restart_and_resume(reason="startup_no_callbacks_restart_recover")
            self.logger.info("obsbot_restart_recover", restart_state)

            if restart_state.get("ok"):
                if self.restart_obsbot_settle_ms > 0:
                    time.sleep(self.restart_obsbot_settle_ms / 1000.0)
                    self.logger.info("obsbot_restart_recover_settle", {"waitMs": self.restart_obsbot_settle_ms})

                re_sel = self.device_resolver.resolve()
                if re_sel.index is not None:
                    re_default_rate = (((re_sel.context or {}).get("inputDevice") or {}).get("defaultSampleRate"))
                    rec_ok, rec_err, rec_probe_attempts = try_stream(
                        re_sel.index,
                        re_sel.name,
                        device_default_rate=re_default_rate,
                    )
                    attempts.append(
                        {
                            "phase": "post_obsbot_restart",
                            "index": re_sel.index,
                            "name": re_sel.name,
                            "result": rec_err or "ok",
                            "selectionReason": re_sel.reason_code,
                            "probeAttempts": rec_probe_attempts,
                        }
                    )
                    if rec_ok:
                        self.input_device_name = re_sel.name
                        self.logger.info(
                            "mic_ready",
                            {
                                "inputDevice": re_sel.index,
                                "inputDeviceName": re_sel.name,
                                "selectionReason": "preferred_input_after_obsbot_restart",
                                "usedFallback": re_sel.used_fallback,
                                "sourceSampleRate": self.stream.source_sample_rate,
                                "sourceBlockSize": self.stream.source_block_size,
                                "targetSampleRate": self.sample_rate,
                                "probeAttempts": rec_probe_attempts,
                            },
                        )
                        self.emit_ui("log", {"message": f"Mic ready after OBSBOT restart: {re_sel.name}"})
                        return True

        self.logger.failure(
            "preferred_input_no_callbacks",
            {
                "inputDevice": sel.index,
                "inputDeviceName": sel.name,
                "selectionReason": sel.reason_code,
                "attempts": attempts,
            },
            status="blocked",
        )

        if not self.allow_default_on_silent_preferred:
            self.emit_ui("error", {"message": "Preferred OBSBOT mic opened but no callbacks on all OBSBOT endpoints; fallback disabled."})
            self._set_status(mic="error", wake="idle", capture="idle", transcribe="idle")
            return False

        fallback_candidates = self.device_resolver.resolve_fallback_candidates(limit=8)
        if not fallback_candidates:
            self.logger.failure("no_fallback_input_available", {"phase": "silent_preferred_recover", "attempts": attempts}, status="error")
            self.emit_ui("error", {"message": "No fallback mic available."})
            self._set_status(mic="error", wake="idle", capture="idle", transcribe="idle")
            return False

        last_error = None
        for cand in fallback_candidates:
            cand_idx = cand.get("index")
            cand_name = str(cand.get("name") or "")
            cand_default_rate = cand.get("defaultSampleRate")
            cand_ok, cand_err, cand_probe_attempts = try_stream(
                cand_idx,
                cand_name,
                device_default_rate=cand_default_rate,
                probe_timeout_ms=1800,
            )
            attempts.append({
                "index": cand_idx,
                "name": cand_name,
                "result": cand_err or "ok",
                "probeAttempts": cand_probe_attempts,
            })
            if not cand_ok:
                last_error = cand_err
                continue

            self.input_device_name = cand_name
            self.logger.failure(
                "preferred_input_silent_fallback",
                {
                    "fallbackInput": self.input_device_name,
                    "fallbackIndex": cand_idx,
                    "attempts": attempts,
                },
                status="blocked",
            )
            self.emit_ui("log", {"message": f"Preferred mic silent; using fallback: {self.input_device_name}"})
            return True

        self.logger.failure("fallback_capture_start_failed", {"error": str(last_error or 'unknown'), "attempts": attempts}, status="error")
        self.emit_ui("error", {"message": f"Fallback mic failed: {last_error}"})
        self._set_status(mic="error", wake="idle", capture="idle", transcribe="idle")
        return False

    def _recover_stream_after_stall(self, stale_ms: float) -> bool:
        self.logger.failure(
            "runtime_audio_callbacks_stalled",
            {
                "staleMs": int(stale_ms),
                "inputDeviceName": self.input_device_name,
                "mode": self.mode,
            },
            status="blocked",
        )
        self.emit_ui("log", {"message": "Audio callbacks stalled; attempting OBSBOT re-wake + stream recovery..."})

        wake_state = self.obsbot_control.send_resume_command(reason="runtime_audio_stall_recover")
        self.logger.info("obsbot_rewake_on_stall", wake_state)

        try:
            self.stream.stop()
        except Exception:
            pass

        sel = self.device_resolver.resolve()
        if sel.index is None:
            self.logger.failure(
                sel.reason_code,
                {
                    **sel.context,
                    "phase": "runtime_stall_recover",
                    "staleMs": int(stale_ms),
                },
                status="blocked",
            )
            return False

        recovered = self._start_stream_with_selection(sel)
        if not recovered:
            return False

        self.logger.info(
            "runtime_audio_stall_recovered",
            {
                "staleMs": int(stale_ms),
                "inputDeviceName": self.input_device_name,
                "selectionReason": sel.reason_code,
            },
        )
        self._set_status(mic="listening", wake="waiting", capture="idle", transcribe="ready")
        return True

    def _on_wake_detected(self, score: float, source: str, wake_probe_ms: Optional[int] = None) -> List[np.ndarray]:
        self.wake_detected_at = time.time()
        self.wake_score = score

        keep_mode = "tail"
        keep_ms = int(max(0, self.prewake_keep_ms))

        if source == "transcript_wake":
            # Transcript wake can block while STT runs; keep earliest queued audio
            # so we preserve command words spoken immediately after wake.
            keep_mode = "head"
            if wake_probe_ms is not None and wake_probe_ms >= self.slow_wake_probe_ms:
                keep_ms = max(keep_ms, int(max(0, self.slow_wake_prewake_keep_ms)))
                keep_ms = min(keep_ms, int(max(1000, self.max_command_seconds * 1000)))

        keep_samples = int(max(0, keep_ms) * self.sample_rate / 1000.0)
        dropped_chunks = 0
        kept_chunks = []
        try:
            if keep_mode == "head":
                dropped_chunks, kept_chunks = self.stream.take_earliest_pending_chunks(keep_first_samples=keep_samples)
            else:
                dropped_chunks, kept_chunks = self.stream.take_recent_pending_chunks(keep_last_samples=keep_samples)
        except Exception:
            dropped_chunks, kept_chunks = 0, []

        prefill_chunks = [c.samples for c in kept_chunks if getattr(c, "samples", None) is not None and c.samples.size > 0]
        prefill_samples = sum(int(c.size) for c in prefill_chunks)
        prefill_ms = int((prefill_samples / self.sample_rate) * 1000) if prefill_samples > 0 else 0

        self._set_status(mic="listening", wake="detected", capture="armed", transcribe="idle")
        self.logger.event(
            "wake_detected",
            {
                "wakeWord": self.wake_word,
                "wakeScore": score,
                "source": source,
                "wakeProbeMs": int(wake_probe_ms) if wake_probe_ms is not None else None,
                "preWakeKeepMode": keep_mode,
                "preWakeKeepTargetMs": int(keep_ms),
                "inputDeviceName": self.input_device_name,
                "droppedPreWakeChunks": int(dropped_chunks),
                "keptPreWakeChunks": int(len(prefill_chunks)),
                "keptPreWakeMs": int(prefill_ms),
            },
        )
        self.emit_ui("ack", {"message": "Wake detected. Listening for command."})
        return prefill_chunks

    def _finalize_command_capture(self, capture_chunks: List[np.ndarray]) -> Dict[str, Any]:
        started_at = self.wake_detected_at or time.time()
        captured_audio = self._merge_chunks(capture_chunks)
        captured_seconds = float(captured_audio.size / self.sample_rate) if captured_audio.size else 0.0

        self.mode = "transcribing"
        self._set_status(mic="listening", wake="processing", capture="done", transcribe="running")

        if captured_seconds < self.min_command_seconds:
            return self._make_result(
                status="blocked",
                reason="command_too_short",
                transcript="",
                latency_ms=int((time.time() - started_at) * 1000),
            )

        stt_out = self.stt_engine.transcribe(captured_audio, purpose="command")
        latency_ms = int((time.time() - started_at) * 1000)

        if not stt_out.get("success"):
            reason = str(stt_out.get("reason") or "stt_failed")
            if reason == "empty_transcript" and captured_audio.size > 0:
                # One low-cost retry on boosted audio to recover quiet commands.
                input_rms = float(np.sqrt(np.mean(np.square(captured_audio)))) if captured_audio.size else 0.0
                gain = 1.0
                if 0.0 < input_rms < 0.05:
                    gain = min(8.0, 0.05 / max(input_rms, 1e-6))
                retry_audio = np.clip(captured_audio * gain, -1.0, 1.0).astype(np.float32)
                retry_out = self.stt_engine.transcribe(retry_audio, purpose="command")
                if retry_out.get("success"):
                    self.logger.info(
                        "command_transcript_retry_recovered",
                        {
                            "capturedSeconds": round(captured_seconds, 3),
                            "latencyMs": latency_ms,
                            "inputRms": round(input_rms, 6),
                            "retryGain": round(gain, 3),
                        },
                    )
                    stt_out = retry_out
                else:
                    self.logger.info(
                        "command_transcript_retry_failed",
                        {
                            "capturedSeconds": round(captured_seconds, 3),
                            "latencyMs": latency_ms,
                            "inputRms": round(input_rms, 6),
                            "retryGain": round(gain, 3),
                            "retryReason": retry_out.get("reason"),
                        },
                    )
                    return self._make_result(
                        status="blocked",
                        reason="empty_transcript",
                        transcript="",
                        latency_ms=latency_ms,
                    )
            else:
                return self._make_result(
                    status="error" if reason.startswith("stt_") else "blocked",
                    reason=reason,
                    transcript="",
                    latency_ms=latency_ms,
                )

        raw_transcript = str(stt_out.get("transcript") or "").strip()
        cleaned_transcript = raw_transcript
        removed_wake_count = 0
        for _ in range(10):
            stripped = strip_wake_word(cleaned_transcript, self.wake_word, self.wake_aliases)
            if stripped == cleaned_transcript:
                break
            cleaned_transcript = stripped
            removed_wake_count += 1

        cleaned_transcript = " ".join(cleaned_transcript.split()).strip()
        if not cleaned_transcript:
            self.logger.info(
                "command_transcript_postprocess",
                {
                    "reason": "wake_only_command",
                    "capturedSeconds": round(captured_seconds, 3),
                    "rawTranscript": raw_transcript[:200],
                    "removedWakeCount": removed_wake_count,
                    "latencyMs": latency_ms,
                },
            )
            return self._make_result(
                status="blocked",
                reason="wake_only_command",
                transcript="",
                latency_ms=latency_ms,
            )

        self.logger.info(
            "command_transcript_postprocess",
            {
                "capturedSeconds": round(captured_seconds, 3),
                "rawLen": len(raw_transcript),
                "cleanLen": len(cleaned_transcript),
                "removedWakeCount": removed_wake_count,
                "latencyMs": latency_ms,
            },
        )
        return self._make_result(
            status="ok",
            reason="ok",
            transcript=cleaned_transcript,
            latency_ms=latency_ms,
        )

    def _emit_result(self, result: Dict[str, Any]) -> None:
        self.logger.write_latest_result(result)
        if result.get("status") == "ok":
            self._set_status(mic="listening", wake="waiting", capture="idle", transcribe="ready")
            self.emit_ui("transcript", {"message": result.get("transcript", ""), "result": result})
        else:
            self._set_status(mic="listening", wake="waiting", capture="idle", transcribe="ready")
            self.logger.failure(
                str(result.get("reason") or "unknown_runtime_error"),
                {"result": result},
                status="blocked" if result.get("status") == "blocked" else "error",
            )
            self.emit_ui("error", {"message": f"{result.get('reason')}"})

    def _make_result(self, status: str, reason: str, transcript: str, latency_ms: int) -> Dict[str, Any]:
        return {
            "sessionId": self.logger.session_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "wakeDetected": True,
            "wakeScore": self.wake_score,
            "transcript": transcript,
            "inputDeviceName": self.input_device_name,
            "latencyMs": int(latency_ms),
            "status": status,
            "reason": reason,
        }

    @staticmethod
    def _merge_chunks(chunks: List[np.ndarray]) -> np.ndarray:
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        total = sum(int(c.size) for c in chunks)
        out = np.zeros(total, dtype=np.float32)
        offset = 0
        for c in chunks:
            n = int(c.size)
            out[offset:offset + n] = c.astype(np.float32)
            offset += n
        return out

    def _set_status(self, mic: str, wake: str, capture: str, transcribe: str) -> None:
        self.emit_ui(
            "status",
            {
                "mic": mic,
                "wake": wake,
                "capture": capture,
                "transcribe": transcribe,
                "inputDeviceName": self.input_device_name,
            },
        )
