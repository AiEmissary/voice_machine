# PROJECT_PLAN

## Phase 0 — foundation (now)
- Build modular folder structure.
- Implement latest-session-only logging contract.
- Implement OBSBOT-preferred input resolution + fallback policy.
- Bring up always-on audio stream and UI indicators.

## Phase 1 — wake + ack
- Add openWakeWord audio wake gate.
- Add transcript fallback wake detector.
- Trigger immediate acknowledgment when wake is detected.

## Phase 2 — command capture + transcript output
- After wake, capture follow-up command audio.
- End capture with silence hangover + max duration guard.
- Transcribe command and emit output contract object.

## Phase 3 — reliability hardening
- Tune wake thresholds and silence thresholds.
- Add soak tests and false wake tracking.
- Improve fallback and blocked/error reason diagnostics.
