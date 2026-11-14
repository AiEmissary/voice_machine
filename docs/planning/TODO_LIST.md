# TODO_LIST

- [x] Confirm baseline project folder `C:\Users\rajka\Desktop\voice_machine`
- [x] Create modular folder boundaries (`app/ui`, `app/audio`, `app/wake`, `app/stt`, `app/runtime`, `app/logging`, `config`, `logs/current`, `docs`)
- [x] Implement latest-session-only logging bootstrap
- [x] Implement OBSBOT preferred-device resolver + fallback policy
- [x] Implement always-on audio input stream and callback guard
- [x] Implement wake detection (openWakeWord + transcript fallback)
- [x] Implement wake acknowledgment path
- [x] Implement command capture + transcribe + output contract emit
- [x] Build basic UI indicators (Mic, Wake, Capture, Transcribe, Errors)
- [ ] Run live mic validation with OBSBOT and tune thresholds
- [ ] Run 30-minute soak and gather failure reasons from logs/current
- [ ] Package startup scripts + operator runbook

## Notes
- This is intentionally simple v1 for reliability first.
- TTS response is deferred.
