# Voice Machine: current S button and earlier wake-word project

## Current S button: Speak V2

The floating round **S** dictation app is backed up in **[speak_v2](speak_v2)**. Hold S to record, then release to transcribe and insert into the focused app. On the development PC its live source is under `%APPDATA%\graviton\custom-tools\whisper_flow_free\v2`.

For a fresh Windows setup with Python 3.12 and Tk:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r speak_v2/requirements.txt
.\.venv\Scripts\pythonw.exe speak_v2/run.py
```

Dependencies include NVIDIA acceleration libraries; transcription attempts CUDA with a smaller CPU fallback. Model downloads and microphone access are needed on a new PC. Dependency versions are not locked.

See [architecture](speak_v2/README.md) and [manual testing](speak_v2/TESTING.md).

### S shortcut / restore launcher

[The Windows launcher source](windows/VoiceMachineLauncher.cs) restores the existing S button without clicking it or recording. If no button exists, it starts the configured app.

```powershell
powershell -ExecutionPolicy Bypass -File windows/build-launcher.ps1 -PythonPath "$PWD\.venv\Scripts\pythonw.exe" -ScriptPath "$PWD\speak_v2\run.py"
```

This builds to `%LOCALAPPDATA%\VoiceMachine` and creates a Start menu shortcut that Windows can pin to the taskbar. The local `launcher.json` contains machine-specific paths and must be regenerated on each PC. Building does not start recording.

### Source backup checks

`speak_v2/SOURCE_SHA256.json` records hashes matching the live runtime Python files and requirements. All copied Python files passed syntax parsing. The running dictation app was left untouched. Fresh installation and end-to-end dictation have not been retested from this clone.

Recordings, transcripts, logs, learned insertion memory, model weights and local launcher configuration are excluded.

## Earlier Andromeda2 precursor (original project)

The root `main.py`, `app`, `config` and `voice_back` folders below are the earlier Andromeda2 wake-word project, **not** the current S button. They are preserved for reference. An unchanged copy of the original README is in [docs/andromeda2_precursor.md](docs/andromeda2_precursor.md).

---

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
