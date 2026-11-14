# compaction_statement

## 1) Current phase and objective
- **Phase:** voice_back bring-up with working system TTS fallback.
- **Objective:** ensure fallback TTS plays cleanly when Piper is unavailable and provide a combined launcher.

## 2) What changed (files + intent)
- `voice_back/requirements.txt`
  - Added `pywin32` to ensure pyttsx3 works on Windows.
- `voice_back/start_voice_back.bat`
  - Launcher for voice_back runtime.
- `start_voice_stack.bat`
  - Combined launcher to start voice_machine + voice_back together.

## 3) What was tested and outcomes
- Installed Python deps: `pyttsx3`, `pywin32`, `comtypes`, `pypiwin32`.
- `py_compile` across voice_back modules -> **PASS**.
- New run not yet validated after install (restart required to load pyttsx3).

## 4) Active blockers
- Piper model path still not present; system TTS is expected primary until Piper is available.
- Need restart to confirm system TTS now works (previous errors were `system_tts_unavailable`).

## 5) Exact next 3 actions
1. Relaunch voice_back (or use `start_voice_stack.bat`).
2. Trigger a voice_machine transcript and verify fallback TTS speaks it.
3. If Piper model becomes available later, set `tts.piperModelPath` in `voice_back/config/runtime.json`.

## 6) Last session log path + last 20-line summary
- **Log path:** `voice_back/logs/current/session.log`
- **Summary:** previous run showed `system_tts_unavailable` errors because pyttsx3/pywin32 were missing. Dependencies are now installed.

## 7) Any config toggles altered
- None.

## 8) Risks to watch next session
- If multiple voice_back instances are launched, overlapping playback can occur. Use the stack launcher or close old console before starting.

## restore_point
- `voice_back/restore_points/voice_back_restore_20260315_1547_scaffold.zip`
