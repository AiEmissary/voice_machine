# voice_machine

Windows-first local wake + command transcription app.

## Current v1 target
- Always listening while app is on
- Wake word: **Andromeda**
- Quick acknowledgment on wake detect
- Capture follow-up command
- Transcribe and emit structured output JSON
- Latest-session-only logs in `logs/current`

## Folder layout
- `app/ui` - Tkinter indicators and runtime panel
- `app/audio` - microphone device selection + live stream
- `app/wake` - wake detection (openWakeWord + transcript fallback)
- `app/stt` - faster-whisper wrapper
- `app/runtime` - orchestration state machine
- `app/logging` - session-scoped logging contract
- `config` - runtime config
- `logs/current` - current session only
- `docs` - runbooks and handoff docs

## Run
```powershell
python main.py
```

## Config
Edit:
- `config/runtime.json`

Key settings:
- `audio.preferredInputDeviceLabel` (OBSBOT target)
- `audio.fallbackPolicy.*`
- `wake.*`
- `stt.*`

## Output contract
Latest output file:
- `logs/current/latest_result.json`

Shape:
```json
{
  "sessionId": "...",
  "timestamp": "...",
  "wakeDetected": true,
  "wakeScore": 0.87,
  "transcript": "...",
  "inputDeviceName": "...",
  "latencyMs": 1234,
  "status": "ok|blocked|error",
  "reason": "..."
}
```

## Logging contract
On startup the app wipes `logs/current/*` and recreates:
- `session.log`
- `events.jsonl`
- `errors.log`

Every failure includes reason code + context.

## GitHub source snapshot

This upload includes the desktop Voice_machine source and its voice_back add-on. Runtime logs, audio recordings, restore archives, Python caches, bundled Piper binaries, and downloaded models are excluded. Install Python dependencies separately and obtain/configure the Piper executable and voice model if using Piper. Existing configuration contains machine-specific Windows paths; adjust them for your installation.
