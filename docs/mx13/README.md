# MX-13 可复现 S2C 发射覆盖率

MX-13 仅在测试进程中注入 `BoundarySocket`，观察真正底层 `socket.sendall` 返回成功后的帧；**不在构造 `build_*` 或排队 `FrameBatcher.sendall` 时计数**。

## 环境

- Windows / Python 3.13.x。
- 从 `p0-accepted`（`15dd282`）建立的隔离 Git 工作树。
- 外部客户端反编译资料不在 P0 Git 仓库中，不纳入提交；路由字典 `net.py` 强制校验 SHA-256 `98929e57e440d40950a8b530c3ec006eeb5999b500eb732495e513534699aa31`。如路径不同，设置 `MX13_CLIENT_NET` 环境变量。
- 完整 P0 回归还依赖 `客户端源码/src` 和部分 `scripts` 文件。Windows 工作树需配置**仅本机使用、已被 Git 忽略**的 `客户端源码` 目录联接（junction）指向外部已授权源码目录，或提供等价的外部素材挂载，并设置 `MX_CLIENT_SRC`。在未提供素材时，文档统计可使用显式 `MX_CLIENT_SRC`，但部分场景测试仍因相对路径找不到素材而失败，不得算作全量 PASS。
- 内置本地临时账号 `mx13probe`，仅回环 TCP，不连接官方服务器；不读取、记录用户密码或改动原有存档。

## 运行

在此目录执行：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:MX13_CLIENT_NET = 'C:\Users\Administrator\MX\客户端源码\src\net.py'
python route_inventory.py
python run_emission_e2e.py
python summarize_emission.py
python run_domain_emissions.py
python run_crypto_emissions.py
python summarize_emission_expanded.py
python audit_gui_evidence.py
python gui_parse_acceptance_gate.py  # 当前缺原生解析事件，预期 NOT_TESTED、退出码 2
```

全量 P0 回归还需在工作树根目录提供外部客户端素材的本机目录联接。确认目标目录存在、链接位置尚不存在后，再在 PowerShell 执行（**不复制、不入库**）：

```powershell
$root = (Resolve-Path ../..).Path   # 在 docs/mx13 目录下
New-Item -ItemType Junction -Path (Join-Path $root '客户端源码') -Target 'C:\Users\Administrator\MX\客户端源码'
$env:MX_CLIENT_SRC = Join-Path $root '客户端源码\src'
python (Join-Path $root '服务端源码\tests\sim\tests\_run_all.py')
```

## 文件

- `route_inventory.json`：160 条冻结名单、唯一 opcode 映射及 8 条清单外客户端字典项目。
- `emission_probe.py`：测试注入观察器，仅观察真实 socket；不会把内存 FakeConn 算作真实发射。
- `emission_socket_actual.jsonl`：每条实际发射的测试名、调用路径、route ID、opcode、结果、长度、哈希。
- `emission_boundary_tests.jsonl`：分片、合帧及**故意失败的假 socket 测试**；此文件的帧**不计入**实际覆盖率。
- `S2C_EMISSION_COVERAGE.json`、`S2C_ROUTE_STATUS.csv` 和 `S2C_EMISSION_COVERAGE.md`：机器可读及人工报告，未发射标 NOT_TESTED。
- `regression_subset.json`、`regression_full_p0.log`、`regression_full_expanded_20261024.log`：回归证据。
- `run_domain_emissions.py`：真实 C2S 发起任务、宠物、商店、制作完成、交易中心提示等；仅隔离测试角色和存档。
- `run_crypto_emissions.py`：以真实 TCP 发送密文，按独立客户端 RX 流/LZSS 解压逻辑还原 opcode；记录已发帧的逻辑 payload SHA256。它**不**等于原版 DLL 客户端 GUI 的差分验证。
- `summarize_emission_expanded.py`、`S2C_EMISSION_COVERAGE_EXPANDED.json/.md`、`S2C_ROUTE_STATUS_EXPANDED.csv`：对三个实际发射场景取 route ID 并集去重；保留旧 26/160 基线文件。
- `GUI_ROUTE_EVIDENCE_CORROBORATION.md/.json`：历史 P1 的 5 张截图 SHA256 和路由功能关联，仅为 GUI 功能佐证。
- `GUI_ROUTE_CAPTURE_PLAN.md`、`gui_parse_acceptance_gate.py`：约束将来同会话的原生 GUI 解析证明；本次因没有 native hook 实际事件，保持 NOT_TESTED。
- `audit_expanded_artifacts.py`、`MX13_EXPANDED_ACCEPTANCE_AUDIT.json`：核实发射帧事件、SHA256、制作产物、加密解码和 P0 全量回归。

## 范围

目前覆盖 P0 明文回环登录/战斗、任务/宠物/制作/交易提示，以及 XOR+LZSS 回环加密场景的物理发送边界，**不代表真实原版游戏 GUI 成功解析**。原生 GUI 解析回调、全部异步场景、剩余路由及原版 DLL 差分验证仍为后续独立验收项。不要用本工具更改原始 160 分母或游戏参数。
