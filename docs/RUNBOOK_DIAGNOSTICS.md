# RUNBOOK_DIAGNOSTICS

## Where to inspect first
- `logs/current/session.log`
- `logs/current/errors.log`
- `logs/current/events.jsonl`
- `logs/current/latest_result.json`

## Common reason codes

### Audio / Mic policy
- `preferred_input_not_found`
  - Preferred OBSBOT label not found.
  - Check device name in Windows + `config/runtime.json`.
- `preferred_input_no_callbacks`
  - Preferred mic opened but produced no callbacks.
  - Driver path is likely silent/unusable.
- `preferred_input_silent_fallback`
  - Legacy reason for silent preferred recovery. In strict OBSBOT mode this should not appear.
- `fallback_capture_start_failed`
  - Legacy fallback chain failed.
- `no_input_devices_available`
  - No valid input devices detected.
- `runtime_audio_callbacks_stalled`
  - Runtime stopped receiving mic callbacks (common when OBSBOT sleeps or USB path stalls).
  - Runtime now attempts automatic OBSBOT re-wake + stream recovery.
- `runtime_audio_stream_recovery_exhausted`
  - Too many consecutive runtime recovery failures.
  - Restart OBSBOT Center and re-seat OBSBOT USB path.

### OBSBOT control bridge
- `obsbot_control_path_missing`
  - OBSBOT executable path missing in config.
- `obsbot_control_software_missing`
  - Path configured but executable not found.
- `obsbot_control_not_running`
  - OBSBOT Center is not active and could not be reused.
- `obsbot_control_launch_failed`
  - Launch attempt threw immediately.
- `obsbot_control_launch_exited_early`
  - Process launched but exited before readiness wait window.
- `obsbot_osc_dependency_missing`
  - `python-osc` is not installed but OSC control is enabled.
- `obsbot_osc_port_invalid`
  - OSC port configuration is invalid.
- `obsbot_osc_send_failed`
  - OSC command dispatch failed.
- `obsbot_force_resume_partial_failure`
  - Startup forced-resume burst sent but one or more attempts failed.
  - Check OBSBOT Center state and OSC command routing.
- `obsbot_osc_resume_commands_sent` *(info reason in context)*
  - Indicates explicit sleep->wake resume pulse commands were sent on launch/retry.
- `obsbot_restart_recover` *(info event)*
  - Runtime restarted OBSBOT control software and attempted one additional mic bring-up pass.

### Wake
- `wake_word_not_detected`
  - Wake phrase not found in wake stage.
- `command_too_short`
  - Wake detected but captured follow-up audio too short.

### STT
- `stt_model_load_failed`
  - Whisper model load failed (check CUDA / model availability).
- `stt_transcribe_failed`
  - Runtime transcription error.
- `empty_transcript`
  - Audio captured but no speech text returned.

## Fallback policy behavior
Configured at:
- `config/runtime.json -> audio.fallbackPolicy`

Policy controls:
- `allowDefaultOnPreferredFailure`
- `allowDefaultOnSilentPreferred`
- `denyDeviceNamePatterns`

For strict OBSBOT-only operation both `allowDefault*` flags must stay `false`.

## Session rotation contract
Each startup clears prior session logs under `logs/current/*` and recreates:
- `session.log`
- `events.jsonl`
- `errors.log`
