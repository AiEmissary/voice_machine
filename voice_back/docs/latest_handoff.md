# compaction_statement

## 1) Current phase and objective
- **Phase:** repeat-block guard + startup backlog skip added in bridge.
- **Objective:** prevent voice_back from replaying stale/foreign phrases or looping repeated transcripts.

## 2) What changed (files + intent)
- `app/bridge/bridge_service.py`
  - Added `skipExistingEventsOnStart` handling to start reading `events.jsonl` from EOF.
  - Added repeat-block guard for identical transcripts within a configurable time window.
  - Added transcript normalization + in-memory recent hash cache.
  - Logged `bridge_skip_existing_events` and `bridge_repeat_blocked` (info) for diagnostics.
- `config/runtime.json`
  - Added bridge config: `skipExistingEventsOnStart`, `repeatBlockWindowMs`, `repeatBlockMinChars`.
- `scripts/create_restore_repeat_block.ps1`
  - Restore point helper.
- `restore_points/voice_back_restore_20260315_1642_repeat_block.zip`
  - Restore point after repeat-block changes.

## 3) What was tested and outcomes
- `python -m py_compile app/bridge/bridge_service.py` → **PASS**.
- **No runtime test yet** after repeat-block changes.

## 4) Active blockers
- Need live runtime validation that repeated/foreign phrases are blocked.

## 5) Exact next 3 actions
1. Close any old instances of voice_back / Andromeda.
2. Start `start_voice_stack.bat`, trigger a transcript, and confirm no repeats.
3. Check `logs/current/session.log` for `bridge_repeat_blocked` entries if a repeat is detected.

## 6) Last session log path + last 20-line summary
- **Log path:** `logs/current/session.log`
- **Summary:** only startup banner present (no runtime events logged in session.log).

## 7) Any config toggles altered
- `bridge.skipExistingEventsOnStart`: `true`
- `bridge.repeatBlockWindowMs`: `600000`
- `bridge.repeatBlockMinChars`: `20`

## 8) Risks to watch next session
- Repeat-block may suppress intentional repeats of long phrases within 10 minutes.
- Starting at EOF means old backlog won’t be spoken (expected), but if you need backlog playback, disable `skipExistingEventsOnStart`.

## restore_point
- `restore_points/voice_back_restore_20260315_1642_repeat_block.zip`
