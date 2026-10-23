# P0 第三轮测试报告

日期：2026-10-23（Asia/Shanghai）  
Python：`C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe`（运行时 3.13.14）  
测试工作目录：`C:\Users\Administrator\MX\服务端源码`

## 1. 汇总

| 项目 | 本轮结果 | 上轮对照 | 退出码 | 日志 |
|---|---:|---:|---:|---|
| Fast | 55/55 PASS | 54/54 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_fast.log` |
| Slow | 7/7 PASS | 7/7 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_slow.log` |
| 文档一致性 | 3/3 PASS | 未给出 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_docs.log` |
| 存档锁 | 20/20 PASS | 20/20 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_save_lock.log` |
| C2S 静态分派覆盖 | 134/134，100% | 134/134 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_c2s_coverage.log` |
| 存档异步 | ALL PASS | PASS | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_save_async.log` |
| 多会话 | PASS | PASS | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_multisession.log` |
| 调试器端到端 | PASS | PASS | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_debugger.log` |
| 分帧/组包 | PASS | PASS | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_tap.log` |
| 制作链 | PASS | PASS | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_craft.log` |
| P0 异常恢复 | PASS | 新增 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_crash_recovery.log` |
| 战斗全链路 | 59/59 | 定向复核 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_battle_flow.log` |
| 战斗核心 | 35/35 | 定向复核 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_battle_core.log` |
| 高级战斗 | 106/106 | 定向复核 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_combat_advanced.log` |
| GM/War 链 | 41/41 | 定向复核 | 0 | `服务端源码/tests/p0_t3_logs/p0_t3_war_chain.log` |

Fast 从 54 增至 55，是因为新增 `P0 异常恢复 _test_p0_crash_recovery`。完整测试清单当前为 65 项：Fast 55、Slow 7、文档 3；三个模式本轮分别执行且全部通过。

## 2. 实际命令、时间与结果

下表中的 `python` 均指上述绝对 Python 路径。

| 测试 | 实际命令 | 开始—结束（+08:00） | 结果 |
|---|---|---|---|
| Fast | `python tests/_run_all.py --fast` | 20:27:53.076—20:29:23.420 | 55/55，exit 0，89.1 s |
| Slow | `python tests/_run_all.py --slow` | 20:24:15.053—20:26:26.549 | 7/7，exit 0，129.8 s |
| 文档 | `python tests/_run_all.py 分册 跨文件 文档三向` | 20:31:27.775—20:31:31.650 | 3/3，exit 0，2.6 s |
| 存档锁 | `python _test_save_lock.py` | 20:26:35.537—20:26:41.254 | 20/20，exit 0 |
| 存档异步 | `python _test_save_async.py` | 20:26:37.372—20:26:41.570 | ALL PASS，exit 0 |
| C2S | `python _c2s_coverage.py` | 20:26:38.343—20:26:41.232 | 134/134，exit 0 |
| 制作链 | `python _test_craft.py` | 20:26:38.594—20:26:39.774 | PASS，exit 0 |
| 分帧 | `python _test_tap.py` | 20:27:29.772—20:27:46.141 | PASS，exit 0 |
| 调试器 | `python _test_debugger.py` | 20:26:48.504—20:27:08.572 | PASS，exit 0 |
| 多会话 | `python _test_multisession.py` | 20:15:13.229—20:15:15.296 | PASS，exit 0 |
| 异常恢复 | `python _test_p0_crash_recovery.py` | 20:15:14.043—20:15:16.065 | PASS，exit 0 |
| 战斗链路 | `python _test_battle_flow.py` | 20:30:29.391—20:30:31.450 | 59/59，exit 0 |
| 战斗核心 | `python _test_battle_core.py` | 20:30:30.632—20:30:31.516 | 35/35，exit 0 |
| 高级战斗 | `python _test_p3_03_combat.py` | 20:30:31.666—20:30:32.436 | 106/106，exit 0 |
| War 链 | `python _test_war_chain.py` | 20:30:31.855—20:30:33.948 | 41/41，exit 0 |

语法编译另行执行：

`python -m py_compile tests/sim/m4_server.py tests/sim/tap.py tests/_test_debugger.py tests/_test_save_async.py tests/_test_multisession.py tests/_test_save_lock.py tests/_test_p0_crash_recovery.py tests/sim/tests/_run_all.py`

退出码 0；唯一输出是 `tests/sim/tap.py:85` 的既有 `SyntaxWarning: invalid escape sequence '\.'`。

## 3. 协议证据

`_test_tap.py` 本轮明确覆盖：

- C2S `[opcode][length][body]` 与 S2C `[length][opcode][body]`；
- 完整单帧、任意切分、半帧跨 feed、多段接收、多帧粘包；
- 扩展长度边界、异常/截断长度、S2C 长度 0 后继续找回下一合法帧；
- 空输入；
- C2S 10,000 连续帧和 S2C 10,000 连续帧，无丢失、重复或余量；
- GBK、NUL 结尾字符串以及多种 `build_*`/客户端字段定义的完整消费。

未发现具体字节不一致。

## 4. 会话、存档与异常证据

`_test_multisession.py` 使用同一 `MultiServer` 和两个临时账号，验证两账号同时进入、位置隔离、战斗隔离、背包隔离、独立存档、单方断线不影响另一方、断线后 A 重登并恢复坐标与背包。同账号第二连接被受控拒绝，原连接保持所有权，退出后释放。

`_test_p0_crash_recovery.py` 使用临时目录和隔离子进程验证：确认保存后 `os._exit(23)`、新进程接管、损坏 JSON 写保护且原始字节不变、半帧后断开清理、20 次快速连接/断开和最终会话归零。

正式存档没有被清空或覆盖。

## 5. 首次失败与修正记录

新增测试后，文档一致性首次运行出现 1/3 失败，原因是测试清单计数仍写 62 且锚点索引未同步。这不是业务缺陷。本轮只更新测试文档计数/生成索引，不改核心行为；随后文档回归 3/3 PASS。失败测试没有被删除或吞掉。

## 6. 未执行项

- 原版客户端实机：NOT_RUN，当前会话没有可用 Windows GUI 控制面。
- 至少 30 分钟隔离稳定性：NOT_RUN；只完成快速重连冒烟，不能外推长期稳定性。

