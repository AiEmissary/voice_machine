# RISK_MATRIX

| ID | Risk | Detection | Mitigation | Reason Codes |
|---|---|---|---|---|
| R1 | Preferred OBSBOT opens but no callbacks | frame counter stays zero after start | fallback to default if enabled; log full context | `preferred_input_no_callbacks`, `preferred_input_silent_fallback` |
| R2 | Preferred OBSBOT not found | device resolve fails | enforce policy (block or fallback) | `preferred_input_not_found` |
| R3 | No input devices available | enumerate returns none | fail fast with diagnostic | `no_input_devices_available` |
| R4 | Wake misses in noise | low wake-hit metric | tune threshold, keep transcript fallback | `wake_word_not_detected` |
| R5 | False wakes | wake in idle/no speech | threshold tuning + alias control | `wake_false_positive` (telemetry) |
| R6 | STT model load fail (cuda path) | load exception | fallback cpu/int8 attempts | `stt_model_load_failed` |
| R7 | Empty transcript after capture | stt success=false empty | prompt retry, keep status blocked | `empty_transcript` |
| R8 | Capture too short / clipped | duration below min | enforce minimum + retry prompt | `command_too_short` |
| R9 | OBSBOT control software not running | preflight check | optionally auto-launch, log state | `obsbot_control_not_running`, `obsbot_control_software_missing` |
| R10 | Recovery loops | repeated blocked reasons | bounded fallback and cooldown | `capture_start_failed`, `fallback_capture_start_failed` |
