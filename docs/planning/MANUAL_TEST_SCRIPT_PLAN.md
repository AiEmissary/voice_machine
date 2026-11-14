# MANUAL_TEST_SCRIPT_PLAN

## TS-01 Startup log rotation
1. Start app.
2. Verify `logs/current` has only `session.log`, `events.jsonl`, `errors.log`.
3. Verify startup banner entry exists.

## TS-02 OBSBOT preferred binding
1. Start app with OBSBOT connected.
2. Verify UI input device label references OBSBOT.
3. Verify `preferred_input_selected` in `session.log`.

## TS-03 Wake + ack
1. Say: "Andromeda".
2. Verify ack appears quickly in UI log.
3. Verify `wake_detected` event in `events.jsonl`.

## TS-04 Wake then command
1. Say wake phrase only.
2. Then speak command within 1-3 seconds.
3. Verify transcript output object appears and status is `ok`.

## TS-05 Single utterance
1. Say: "Andromeda open notes".
2. Verify transcript is captured and output status is `ok`.

## TS-06 OBSBOT unavailable
1. Unplug/disable OBSBOT.
2. Start app.
3. Verify block/fallback reason code appears as configured.

## TS-07 Soak
1. Keep app running for 30 minutes with periodic wake+command tests.
2. Verify no crash and no stuck states.
