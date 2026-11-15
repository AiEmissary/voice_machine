# Speak V2 (IPC Modular Architecture)

This folder contains the modular hold-to-talk dictation system using a local JSON envelope IPC bus.

## Transport model

Local in-process IPC bus (`app/common/ipc.py`) with standardized envelope:

```json
{
  "id": "uuid",
  "type": "command|event|response|error",
  "ts": "2026-02-19T21:15:00.000Z",
  "source": "ui|stt|insert|orchestrator",
  "target": "ui|stt|insert|orchestrator",
  "name": "message_name",
  "payload": {}
}
```

## Commands implemented

- `session.press_start`
- `session.press_end`
- `session.cancel`
- `settings.update`
- `stt.begin_capture`
- `stt.end_capture`
- `stt.transcribe`
- `insert.capture_target`
- `insert.apply`

## UI state events

- `ui.state.idle`
- `ui.state.listening`
- `ui.state.transcribing`
- `ui.state.inserting`
- `ui.state.success`
- `ui.state.error`

## Run

```powershell
python speak_v2/run.py
```

## Smoke test

```powershell
python speak_v2/run.py --smoke
```

## Error log test

```powershell
python speak_v2/run.py --test-error
```

## Logs

- Main log: `speak_v2/logs/speak_v2.log`
- Error log: `speak_v2/logs/speak_v2_errors.log`
- Envelope/event JSONL: `speak_v2/logs/speak_v2_events.jsonl`
- Strategy memory: `speak_v2/data/strategy_memory.json`

## Notes

- Windows-focused.
- Requires `faster-whisper`, `sounddevice`, `numpy`, `pyperclip`, `keyboard`.
- Strategy memory key format: `{topClass}|{focusClass}` with adaptive order + `lastSuccess` + `successRate`.
