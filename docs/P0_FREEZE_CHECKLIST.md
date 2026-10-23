# P0 基线冻结清单

日期：2026-10-24  
当前结论：**可冻结（已冻结）**

## 验收清单

- [x] 完整回归套件通过：`65/65` 步骤、失败 `0`、耗时 `266.3 s`、退出码 `0`（`acceptance_20261023/run2/regression_freezerun.log`，套件自报 `STATS steps_passed=65 steps_total=65`）。
- [x] 协议分帧、组包、GBK/NUL、10,000 帧连续压力通过。
- [x] 核心文件编译检查：`python -m py_compile tests/sim/m4_server.py tests/sim/tap.py tests/_test_debugger.py tests/_test_save_async.py tests/_test_multisession.py tests/_test_save_lock.py tests/_test_p0_crash_recovery.py tests/sim/tests/_run_all.py` 退出码 0，唯一输出为既有 `tap.py:85` SyntaxWarning（`P0-R003`）。
- [x] C2S 分派静态覆盖：`134/134`（套件自报 `coverage_numerator=134 / coverage_denominator=134`）。
- [x] 存档锁：`20/20`；异步保存、冲突拒写、顺序接管、失败重试、异常退出后接管通过。
- [x] 会话串号与重复登录：多会话用例通过；实机再现「拒绝重复登录」拒绝帧。
- [x] 战斗核心链路：四组定向测试（59/35/106/41 断言）+ 完整套件全绿；实机以客户端指令跑通一场完整战斗。
- [x] P0 异常恢复冒烟：通过。
- [x] 原版客户端实机验收 A–E 五项：通过（`P0_CLIENT_ACCEPTANCE.md`，证据在 `acceptance_20261023/run2/`）。
- [x] ≥30 分钟隔离稳定性：通过（`P0_STABILITY.md`，逐分钟采样 CSV 与汇总 JSON 均已入库）。
- [x] Git 工作区可核查：`main` 分支、工作区干净、冻结提交由 `p0-accepted` 标签指向（`git rev-parse p0-accepted`）。
- [x] 五份 P0 文档齐全：`P0_TEST_REPORT.md`、`P0_CLIENT_ACCEPTANCE.md`、`P0_STABILITY.md`、`P0_REMAINING_ISSUES.md`、`P0_FINAL_AUDIT.md`。

## 冻结门禁（三条均已解除）

| 门禁 | 原状态 | 本轮处理 |
|---|---|---|
| 原版客户端实机验收（A–E） | NOT_RUN | 全部执行并通过，附截图、协议日志、重登逐字段比对 |
| ≥30 分钟隔离稳定性 | NOT_RUN | 完成 32.6 分钟（1956 s）连续运行，28 个采样点；RSS 55.5 MB 全程无增长、异常 0、战斗 6 场、存档每轮可解析 |
| Git 元数据 | 缺失 | `git init` → 提交 → 标签 `p0-accepted` |

## 冻结记录

| 项目 | 值 |
|---|---|
| 仓库根 | `C:\Users\Administrator\MX` |
| 分支 | `master` |
| 基线提交 | `fe7f148`（`chore: 建立项目基线（模拟服 + 协议资料 + 验收证据）`，2556 个文件） |
| 冻结提交 | 见 `git rev-parse p0-accepted`（标签指向的提交即冻结基线，包含本轮验收证据与文档） |
| 标签 | `p0-accepted`（附注标签） |
| 提交信息 | `test(p0): accept protocol session save diagnostics and regression baseline` |
| 入库范围 | `服务端源码/`（不含 `logs/`）、`协议资料/`（不含 7z 现场包）、`docs/`（含本轮全部验收证据）、根目录规划文档与脚本 |
| 排除项 | `客户端数据/`（1.1 GB 游戏原始资源）、`客户端源码/`（70 MB 反编译参考代码）、`__pycache__`、服务端运行日志、临时 `cmd/res` 指令流与网格调试图；详见 `.gitignore` 顶部注释 |

## 冻结后注意

- 任何 P1 改动请另开分支，基于 `p0-accepted` 提交，不要直接改基线。
- `P0-R006`（人物面板经验显示）与 `P0-R007`（启动器更新提示）建议作为 P1 首批修复项，修完重跑完整套件 + 一次 30 分钟稳定性。
- 复验环境依赖：`127.0.0.1:8080` 本地 HTTP（`server7.txt`/`blank`）、`update.ini` 指向本地、`mx.ini` 账号记忆已清空。
