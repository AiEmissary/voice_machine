# ACCEPTANCE_CHECKLIST_V1

## Scope
- [ ] Always-listening loop active while app is running.
- [ ] Wake word `Andromeda` reliably detected.
- [ ] App acknowledges wake quickly.
- [ ] App captures follow-up command and transcribes it.
- [ ] Structured output JSON emitted for downstream app.
- [ ] OBSBOT Tiny SE is preferred input path.
- [ ] Logs keep latest session only.
- [ ] UI indicators update: Mic, Wake, Capture, Transcribe, Errors.

## Performance targets
- [ ] Wake -> ack P95 <= 700ms
- [ ] End-of-speech -> transcript P95 <= 2500ms
- [ ] End-to-end wake->result P95 <= 5000ms

## Reliability targets
- [ ] Wake hit rate (quiet room) >= 95%
- [ ] False wake rate (quiet idle) <= 1/hour
- [ ] No 30-minute soak crash or stuck capture state

## Logging contract
- [ ] Startup clears `logs/current/*`
- [ ] Creates `session.log`, `events.jsonl`, `errors.log`
- [ ] Startup banner includes timestamp + config hash + mic target
- [ ] Every failure includes reason code + context fields
