# V2 Testing Checklist

## 1) Environment + smoke

```powershell
python speak_v2/run.py --smoke
```

Expected: `Speak V2 smoke OK`

## 2) Error log pipeline

```powershell
python speak_v2/run.py --test-error
```

Expected files updated:
- `speak_v2/logs/speak_v2_errors.log`
- `speak_v2/logs/speak_v2_events.jsonl`

## 3) Full UI run

```powershell
python speak_v2/run.py
```

Then:
1. Focus Notepad (or target app)
2. Hold left mouse on floating button to record
3. Release to transcribe + insert

## 4) Validate IPC envelope logging

Check `speak_v2/logs/speak_v2_events.jsonl` for command/event/response/error envelopes between:
- `ui`
- `orchestrator`
- `stt`
- `insert`

## 5) Validate strategy memory

Check `speak_v2/data/strategy_memory.json` for app key:

`{topClass}|{focusClass}`

and fields:
- `preferredStrategyOrder`
- `lastSuccess`
- `successRate`
