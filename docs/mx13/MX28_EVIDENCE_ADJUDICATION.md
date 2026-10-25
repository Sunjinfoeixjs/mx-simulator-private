# MX-28 | Cross-branch physical-emission evidence adjudication

**PASS (physical socket.sendall emission metric only): 44/160 = 27.50%.**
The prior 36/160 expands by 8 P67-only routes each evidenced by at least one
real socket.sendall SUCCESS from an independently PASS test.

| Group | Unique routes |
|---|---:|
| Both branches PASS | 30 |
| MX13-only PASS | 6 |
| P67-only PASS, newly admitted | 8 |
| Not observed | 116 |

## P67-only admitted routes (failed tests excluded)
| Route | Opcode | Handler | PASS send events | PASS tests |
|---|---:|---|---:|---|
| R005 | 35 | netitem.S2CItemQuantity | 2 | _e2e_m4, _test_war2 |
| R023 | 117 | netwar.S2CWarAttackNormal | 91 | _e2e_m4, _e2e_m5, _e2e_m7, _test_tap, _test_war2 |
| R024 | 119 | netwar.S2CWarAttackNormalEnd | 92 | _e2e_m4, _e2e_m5, _e2e_m7, _test_tap, _test_war2 |
| R031 | 121 | netwar.S2CWarProtect | 1 | _test_war2 |
| R029 | 122 | netwar.S2CWarPerform | 6 | _e2e_m4, _e2e_m7, _test_war2 |
| R035 | 123 | netwar.S2CWarWState | 99 | _e2e_m4, _e2e_m5, _e2e_m7, _test_tap, _test_war2 |
| R027 | 125 | netwar.S2CWarHPPercent | 82 | _e2e_m4, _e2e_m5, _e2e_m7, _test_tap, _test_war2 |
| R131 | 149 | netnpc.S2CNpcLearnList | 1 | _e2e_m9 |

## Strict exclusions and limitations
- The P67 `_test_singleplayer` test FAILED; its 67 emitted events are excluded
  from the P67 accepted evidence stream. No accepted event cites that test.
- One unsuccessful send attempt is excluded. Six opcode-250 heartbeat frames
  are outside the frozen 160-route denominator and are not new routes.
- No cross-branch frame totals are added: 251 and 1,474 belong to
  separate runs. Only unique (opcode, handler) routes are unioned.
- P67 full-suite status stays PARTIAL; this is not full-regression PASS.
- Native-client GUI parser return remains NOT_TESTED, with no verified routes.
- No original-client DLL XOR/LZSS equivalence is claimed.

## Reproduction
`python docs/mx13/adjudicate_44_routes.py --check`

All source SHA256 values and 160 individual decisions are stored in
`S2C_EMISSION_COVERAGE_ADJUDICATED.json` and
`S2C_ROUTE_ADJUDICATION_160.csv`. Raw login frames are not copied.
