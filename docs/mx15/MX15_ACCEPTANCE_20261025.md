# MX-15 / P7-A 最终门禁执行与验收（2026-10-25）

## Scope

独立分支 `feature/mx15-quality-gates`，基于 MX13/MX25 HEAD `9c0c2d5`；`p0-accepted` 解引用仍指向 `15dd2828ceb2e1ea7023fd09ae69955579a5b74d`。原客户端源码快照以未跟踪目录链接只读提供；原服务端业务代码与客户端 DLL 未修改。

### 门禁运行证据

| 门禁 | 实际结果 | 证据 |
|---|---|---|
| `selftest` | PASS：6 个坏 JSON 样例拒绝、分派 AST 篡改拒绝、回归假 PASS 拒绝、核心服务端增量加测规则检查 | `MX15_SELFTEST_RESULT.json` |
| `lint` | PASS：21 个 JSON 严格重复键/NaN 检查，MX12 schema 与 123/134 冻结对账、10 项正反单测、P0 分派 AST、S2C 44/160 严格发送证据 | `MX15_LINT_RESULT.json` |
| `incremental` 旧快速全套 | 55/55 PASS，STATS=fast；约 120 秒整流程，反映过度测试 | 被更新为选测的 `MX15_INCREMENTAL_RESULT.json` 覆盖，原始忽略目录日志仍保留 |
| **优化后 `incremental --changed 协议资料/字典/itempet_opcodes_fields.json`** | **PASS：选测 `_extract_wire`、`_c2s_coverage`、`_test_dispatch` 各 1/1，约 12 秒（含严格静态门禁）** | `MX15_INCREMENTAL_RESULT.json` |
| **`full`** | **PASS：65/65，0 FAIL，mode=full，退出 0；另有独立 `_test_singleplayer` 连续两次 25 断言 PASS**。约 262 秒整流程 | `MX15_FULL_RESULT.json` |
| **`release` 无原生 GUI** | **FAIL（预期）：退出码 1，明确 `NOT_TESTED`，不得发布** | `MX15_RELEASE_RESULT.json` |

严格单项使用本机 Python 程序 subprocess 真正执行；报告记录命令状态、退出码、耗时及原始控制台 SHA256。真实 stdout/stderr 保存在被 Git 排除的 `docs/mx15/.runtime/logs/`，不可将原始登录认证帧提交到仓库。完整跑数在报告中，测试进程实际退出码已复核。

### 已修复的跨机器阻断

MX-28 原 `adjudicate_44_routes.py --check` 在 Git for Windows `core.autocrlf=true` 时把合法 LF↔CRLF 的 Git 工作树文件判为差分。已改为只归一化换行符；**P67 原始送帧事件、10 份测试回执、SHA256 与 opcode/handler 校验仍是强制门禁**。修复后在全新 MX15 Windows worktree 中重新完成 `44/160 = 27.50%` PASS。

### 不予升级的状态与剩余依赖

- **原生 GUI Unpack/handler 解析：`NOT_TESTED`。** 本轮 44/160 只证明自动化原始 `socket.sendall` 成功，不证明原生客户端成功解码、业务显示及原版 DLL 差分；真实 MX-25 同会话取证仍是发布阻断。
- **P67 历史失败测试**（`_test_singleplayer` 等待 112）的 67 帧继续排除，不会因为 MX25 新隔离分支测试脚本已修复而追溯补入旧 P67 结果。
- **P8 原服真实性**未被上述门禁证明。
- `full` 是阶段**代码回归测试**的 PASS，不是 `release` 的 PASS；不得把独立 GUI 阻断隐藏。
- 分派 AST 恒等只是当前 P0 分派不漂移检查；MX14 注册表重构需要独立证据审核和基线升级，不允许为通过门禁擅自关掉此检查。

### 操作规范

```powershell
$env:MX_CLIENT_SRC = 'C:\Users\Administrator\MX-mx15\客户端源码\src'
python docs/mx15/mx15_quality_gates.py lint
python docs/mx15/mx15_quality_gates.py incremental --changed 协议资料/字典/itempet_opcodes_fields.json
python docs/mx15/mx15_quality_gates.py full
python docs/mx15/mx15_quality_gates.py release
```

合并/冻结**必须**运行 `full` 并核对统计结果；若需要对外声明完整原生 GUI 验收，则 `release` 还必须取得原始 DLL 同会话解析日志并通过关键路由。没有配置远程 CI 的实际 runner，不能声称“云端自动运行”或“GitHub 分支保护已开启”。文件仅在本机分支可复现。
