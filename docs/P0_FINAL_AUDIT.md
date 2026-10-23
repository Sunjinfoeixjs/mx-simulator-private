# 《梦想世界2013》模拟服 P0 最终审计

审计日期：2026-10-24（Asia/Shanghai）  
审计范围：协议分派、会话状态一致性、存档安全、日志诊断、回归测试、战斗全链路、异常恢复、原版客户端实机验收、隔离稳定性与基线冻结。  
最终结论：**PASS（可冻结）**。

第三轮审计曾判 **BLOCKED**，原因是原版客户端实机验收未执行、30 分钟稳定性为 NOT_RUN、工作区没有 Git 元数据。本轮把这三项补齐：A–E 五项实机通过（`P0_CLIENT_ACCEPTANCE.md`）、≥30 分钟隔离稳定性完成（`P0_STABILITY.md`）、Git 建仓提交并打标签（`P0_FREEZE_CHECKLIST.md`）。

## 1. 环境与基线

| 项目 | 本轮实测结果 |
|---|---|
| 工作区 | `C:\Users\Administrator\MX` |
| Git | 已 `git init`；分支 `main`；冻结提交与标签 `p0-accepted` 见 `P0_FREEZE_CHECKLIST.md` |
| Python | `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（3.13.12 venv） |
| 测试入口 | `服务端源码/tests/sim/tests/_run_all.py`（完整套件约 4.5 分钟） |
| 服务端 | `服务端源码/tests/sim/m4_server.py`，隔离存档 `docs/acceptance_20261023/run2/save`，端口 12239 |
| 原版客户端 | `D:\mxsj2013\client`（1.2.73，`mx.exe` 2,176,256 B；实机使用其 asInvoker 副本） |

## 2. 五模块最终状态

| 模块 | 状态 | 源码/测试位置 | 本轮证据 |
|---|---|---|---|
| P0-01 协议 | PASS | `服务端源码/tests/sim/tap.py:820,851,889,1244,1252`；`_test_tap.py:168-240` | 完整帧/半帧/多段/粘包/扩展长度/异常长度/空输入 + C2S/S2C 各 10,000 连续帧；GBK/NUL 与 build/unpack 字段；C2S 静态分派 134/134（`coverage_numerator=134 / denominator=134`） |
| P0-02 会话 | PASS | `m4_server.py:5718,11956-12020`；`_test_multisession.py:86-233` | 双账号同时在线、坐标/战斗/背包/存档互不影响、A 断开不影响 B、所有权释放后可重登、重复登录受控拒绝；本轮实机再现拒绝帧 |
| P0-03 存档 | PASS | `m4_server.py:356,409-487,5352,5508`；`_test_save_lock.py`、`_test_save_async.py`、`_test_p0_crash_recovery.py` | 存档锁 20/20、异步保存、冲突拒写、顺序接管、失败重试、断线保存、确认保存后异常退出、损坏档拒覆盖；本轮实机「关闭 → 已存档 → 重登逐字段一致」 |
| P0-04 诊断 | PASS | `_test_debugger.py`；`tap.py:1451` | 调试台端到端外层退出码 0；帧流解析与统计通过；无硬编码工作区路径 |
| P0-05 回归 | PASS | `tests/sim/tests/_run_all.py` | 冻结前完整套件：**65/65 步骤通过，失败 0，耗时 266.3 s，退出码 0**（`acceptance_20261023/run2/regression_freezerun.log`） |

## 3. 历史问题复核

| 历史问题/声称修复 | 本轮独立复核 |
|---|---|
| 半帧边界误判 | PASS：半帧不提前解析，补齐后只产生一帧；10,000 帧压力无丢失/重复 |
| 同账号多活跃会话 | PASS：第二连接在账号应答前被关闭，原连接继续；断线后所有权释放可重登（实机再现拒绝帧） |
| 存档冲突覆盖与重启接管 | PASS：冲突实例拒写、原文件保持；顺序重启合法接管；损坏档不被默认角色覆盖 |
| 调试器路径与分帧诊断 | PASS：端到端退出码 0 |
| fast 套件纳入多会话/调试器 | PASS：完整套件 65 步包含多会话与异常恢复 |
| 实机验收缺口 | **已关闭**：A–E 五项实机通过，含协议日志、截图与逐字段存档比对 |

## 4. 战斗全链路

胜利分支实际顺序：

`WarDel → WarEnd → _reset_war_state → _resync_scene → _quest_battle_won → _award_battle_exp → _roll_drops`

四组定向测试（`_test_battle_flow.py` 59、`_test_battle_core.py` 35、`_test_p3_03_combat.py` 106、`_test_war_chain.py` 41）断言 WarDel 在 WarEnd 前、场景恢复在 WarEnd 后、经验与掉落帧在 WarEnd 后；本轮完整套件亦全绿。

本轮实机再验一次完整链路（客户端真实操作，非超时默认）：

```
00:13:01 遇怪！WarStart + WarAdd(主角100 + 宠物250) + WarAdd(敌方×1) + WarTurn [天水平原 / 小兔子×1]
00:13:05 C2S opcode=160 body=01 00        ← 客户端点击敌方下指令
00:13:05 普攻 100 → 1（50 伤害）
00:13:35 敌方全灭 → WarEnd
00:13:35 → 场景 天水平原：SceneM + AddNpc×2 + GotoM(82,61) + 随行宠物
00:13:35 战斗经验 +60 → 900/1000
00:13:35 掉落：天水平原 → 罗汉果（item 21301，bag 9009）
```

## 5. 异常恢复与稳定性

正常退出、异常关闭、半帧后断开、战斗中断线、存档失败/重试、重复登录、快速登录退出重连、服务继续接受连接均通过隔离测试；确认保存后的 `os._exit(23)` 子进程测试验证 JSON 完整且新进程可接管。

本轮补齐 ≥30 分钟稳定性运行：隔离存档 + 独立端口，每 60s 采样服务端 RSS、在线会话、角色坐标/经验/背包、存档文件可解析性与日志异常行；结论与逐分钟曲线见 `P0_STABILITY.md`。

## 6. 原版客户端与最终判定

- A 启动与登录：PASS（握手 `16 ed 4b`、登录、选角进入、属性面板）。
- B 地图与 NPC：PASS（移动坐标逐帧一致、NPC 对话弹选项、切图 `SceneM/AddNpc/GotoM`）。
- C 战斗：PASS（自然遇敌 → 客户端指令 → 演出 → WarEnd → 场景恢复 → 经验/掉落各一次）。
- D 存档：PASS（关闭后落盘、重登后 exp/等级/金币/地图/坐标/宠物 HP/背包件数全等，`D_diff.json` ALL MATCH）。
- E 双开：PASS（两会话独立、存档分文件、独立断开、重复登录被拒）。

判定：

- 自动化验收：PASS（65/65）
- 原版客户端实机验收：PASS（A–E）
- 隔离稳定性（≥30 分钟）：PASS
- P0 最终验收：**PASS**
- Git 基线冻结：**允许**（提交 + 标签 `p0-accepted`）
- 进入 P1：允许；P1 首项建议修 `P0-R006`（人物面板经验显示）与 `P0-R007`（启动器更新提示）

非阻断遗留问题见 `P0_REMAINING_ISSUES.md`（R002、R003、R006、R007、R008、R009、R010）。
