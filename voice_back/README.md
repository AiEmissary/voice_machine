# voice_back

Local Windows add-on that reads transcript output from **voice_machine** and immediately speaks it back.

## Quick start
1. Install deps:
   ```
   pip install -r requirements.txt
   ```
2. Configure `config/runtime.json`:
   - `bridge.eventsPath` and `bridge.latestResultPath` point to voice_machine logs.
   - Set `tts.piperModelPath` to your Piper model (required for Piper).
   - Optionally set `tts.piperPath` to the Piper executable path.
3. Run:
   ```
   python main.py
   ```

## Notes
- Logs are written to `logs/current/` and overwritten on each start.
- If Piper fails/unavailable, system TTS (pyttsx3) is used when `systemFallback` is true.
