# MX-28: 36-vs-38 cross-branch route reconciliation

Both sources match the external client NET_FUNCTION_NAME dictionary SHA256.
P67 is PARTIAL with a failed singleplayer opcode112 test; MX13 has a clean 65/65 P0 regression.

| Group | Routes |
|---|---:|
| Common | 30 |
| P67 only | 8 |
| MX13 only | 6 |
| Candidate union | 44 |
| Neither | 116 |

44 is a candidate union only. It is NOT an accepted coverage numerator or native GUI parse result.

P67 only (route/opcode):
- R005 / 35
- R023 / 117
- R024 / 119
- R031 / 121
- R029 / 122
- R035 / 123
- R027 / 125
- R131 / 149

MX13 only (route/opcode):
- R003 / 34
- R140 / 51
- R089 / 87
- R134 / 155
- R122 / 160
- R039 / 164

Full 160-row details: MX13_CROSS_BRANCH_160_RECONCILIATION.csv.
Re-check original successful-send event-to-test provenance and P67 failing test exclusion before claiming new coverage.
