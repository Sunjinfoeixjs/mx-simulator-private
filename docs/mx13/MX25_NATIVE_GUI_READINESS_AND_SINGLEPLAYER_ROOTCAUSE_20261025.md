# MX-25 — Native GUI same-session parser evidence readiness (2026-10-25)

## Evidence status, not a completion claim

- **Native GUI S2C parse: NOT_TESTED.** At the inspection time there was no running `mx.exe`, `gameplayer.exe`, `x32dbg.exe` or `x64dbg.exe` process.
- A Python server had a loopback **LISTEN** socket on port 19492, but no native client **ESTABLISHED** connection was observed. The server is **not presumed to be the frozen P0 binary or an instrumented socket observer**.
- Neither `native_gui_live/server_physical_sent.jsonl` nor `native_gui_live/native_client_parser.jsonl` exists. The strict `gui_parse_acceptance_gate.py` returned `NOT_TESTED`, exit code **2**, as intended.
- P1 historical GUI screenshots (five SHA-matched functional examples) do **not** provide native Unpack/handler return events for the current P0 session. No native-client XOR/LZSS equivalence or original-server authenticity has been proven.
- The **44/160 (27.50%) accepted emission statistic** is a union of independent **automated** P0 sendall tests; **none** of those 44 routes may be counted as native GUI parsed on this basis.

## Generated 160-route readiness matrix

`mx25_prepare_native_gui_matrix.py` produces
`MX25_GUI_ROUTE_VERIFICATION_BASELINE_160.csv` from the adjudicated 44/160 ledger. Each row separates:
1. `automated_sendall_evidence` (44 PASS, 116 NOT_TESTED);
2. `native_gui_session_send` (NOT_TESTED for all);
3. `native_client_tcp_receive` (NOT_TESTED for all);
4. `native_client_unpack` (NOT_TESTED for all);
5. `native_handler_executed` (NOT_TESTED for all);
6. `native_gui_effect` (NOT_TESTED for all, historical screenshots only contextual).

The matrix is **a capture planning baseline**, not synthetic native evidence. No positive native-GUI rows are fabricated.
Reproduce with:

```powershell
python docs/mx13/mx25_prepare_native_gui_matrix.py --check
python docs/mx13/gui_parse_acceptance_gate.py
```

The second command is *expected* to exit 2 until actual same-session source captures are present.

## Exact observation requirements for a future native session

- Use an authorized, isolated loopback **P0** test server with an observer installed **before** the native client connects; record the server branch/commit and the original client executable and loaded DLL SHA256. Do not silently reuse a running P1 server as if it were P0.
- The server's successful physical `socket.sendall` observation must identify a unique connection/session, TCP endpoint pair, route ID, opcode, frame/send ordinal, wire SHA256, logical payload SHA256 after appropriate framing/decryption, and observation timestamp.
- The **actual original game client's mxmain.dll Unpack/dispatch/handler path** must be observed during the **same** TCP session: game PID, loaded DLL hash, instrumented function address/version, decoded opcode, handler reached, logical payload SHA256, callback success/error and frame ordinal. Trace the encrypted/LZSS transform at the real DLL boundary when applicable.
- Pair events by session identity plus opcode/handler/logical-payload hash and order; time proximity or matching screenshots alone are **insufficient**. Match server send success to client parser **successful return**, not only receiving bytes.
- Screenshots (world/character/battle/quest/pet/craft/trade) and redacted state evidence corroborate visible effects **after** the parser events are obtained.
- User authentication should be completed manually inside the original GUI; never log/commit passwords, authentication frames, cookies, raw private C2S payload, or private account identifiers. Keep raw traces in the ignored `native_gui_live/` directory and store only redacted hashes and dispositions in Git.
- The existing gate requires the two live files. Their absence preserves **NOT_TESTED**, not a false GUI rate of 0%.

## Adjacent _test_singleplayer issue (distinct from GUI)

The old test expected a natural encounter after three steps on starter-town map **1001**. That became invalid when `ENCOUNTER_PER_MAP=True` restricted natural encounters to mapped wild zones. It timed out waiting for S2C opcode **112** even without the P67 send tap.

The test-only repair on `feature/mx-13-s2c-emission` moves the session by its public GM test interface to real wild map **1028**; three genuine C2S moves produce WarStart/WarAdd/WarTurn. After exercising battle actions, it returns to town before NPC tests, and joins the memory-server thread before closing the server endpoint. No gameplay formula, map rules, protocol fields, binary client or frozen P0 tag changed.

The P67 archived failed `_test_singleplayer` scenario remains **FAIL** for that historical run; its 67 frame events remain excluded from the P67 numerator. A newly passing test does **not** retroactively reclassify the excluded old events. MX-28 emission remains **44/160**, while native GUI remains **NOT_TESTED**.

## Remaining blocker

No running original client GUI or trustworthy native parser instrumentation events were available at this inspection. To complete actual MX-25 validation, a person must start and authenticate the original game GUI on the Windows test machine, and a real parser hook with contemporaneous server send instrumentation must capture the **same** session. The present static client handler dictionary and Python test decoder are not substitutes.
