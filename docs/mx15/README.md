# MX-15 | P7-A JSON lint、增量回归、全量测试门禁

## 冻结范围与可重复执行

本分支 `feature/mx15-quality-gates` 在独立工作树 `C:\Users\Administrator\MX-mx15` 中开发，起点为已验收 MX-13/MX-25 提交 `9c0c2d5`，共同的冻结 P0 tag 必须解引用到 commit `15dd282`。不修改 P0、游戏公式、协议字段或原始客户端 DLL。

```powershell
$env:MX_CLIENT_SRC = 'C:\Users\Administrator\MX-mx15\客户端源码\src'
python docs/mx15/mx15_quality_gates.py selftest
python docs/mx15/mx15_quality_gates.py lint
python docs/mx15/mx15_quality_gates.py incremental --changed 协议资料/字典/itempet_opcodes_fields.json
python docs/mx15/mx15_quality_gates.py incremental --changed 服务端源码/tests/sim/m4_server.py
python docs/mx15/mx15_quality_gates.py full
python docs/mx15/mx15_quality_gates.py release
```

**注意：** 外部客户端源码不是 P0 Git 的一部分。请提供经审核与冻结 SHA256 相匹配的 `net.py` 所在目录；完整 P0 测试中部分脚本还需要在本独立工作树下创建一个指向该只读外部素材的 `客户端源码` 目录链接。该素材及账号配置不允许提交到 Git，链接已经被根 `.gitignore` 排除。

## 四层门禁

1. **严格 JSON lint**：源协议字典、协议报告和 MX-13 已提交的 JSON 逐文件执行 `object_pairs_hook` 级重复键检测，禁止 NaN/Infinity、错误编码和语法损坏。直接复用 MX-12 的字段结构门禁及 **123 旧清单行 / 134 客户端引用唯一 opcode** 冻结对账（新增15、旧独有2、旧重复文本行2，差值11不是11个缺失 opcode），调用 MX-12 10 项正反测试。
2. **分派差分**：从 `p0-accepted` 读取 `M4Server._dispatch_locked` 的 AST，与当前工作树中该方法 AST 严格比较；允许注释/空白变化，禁止未经审查修改分派条件、优先级和业务语义。未来 MX-14 等价重构需要独立审核后引入新行为基线，不能直接放宽门禁。
3. **S2C 真正发送边界**：重新执行 MX-28 原始日志逐条裁定 `adjudicate_44_routes.py --check`，校验两个分支共 **44/160 = 27.5%**、10 份测试回执、P67 失败测试 67 帧隔离、opcode250 清单外排除与来源哈希。修复 Git for Windows `core.autocrlf` 导致的 CRLF/LF 合法文件误报，**只忽略换行差异**，其它字节和冻结哈希仍严格校验。
4. **回归与原生 GUI 分层**：
   - `lint`：只运行协议、差分、覆盖报告核验；报告原生 GUI `NOT_TESTED` 不误报 PASS。
   - `incremental`：先运行全部静态门禁，再按 **changed paths** 选测试。仅修改协议字典时运行 `_extract_wire`、`_c2s_coverage`、`_test_dispatch`；MX-13 发送证据目录用 `_test_tap`；门禁脚本自身用 `_stats`；单机测试文件用 `_test_dispatch` 加单机双重重跑。未知/混合改动保守运行原版 `--fast`（55项）；核心服务端还额外运行 `_e2e_m4`、`_test_war2`。只承诺本次选中的增量范围，不能代替全量。
   - `full`：先静态门禁，再运行**原始完整 65 项**，强制 `STATS: steps_total=65, steps_passed=65, steps_failed=0, mode=full` 与进程退出码为 0；再连续两次运行单机 25 项断言，均无未捕获异常。
   - `release`：在 `full` 的条件基础上，对真实原版 GUI 会话严格要求六个关键登录/进世界/战斗 handler 的 Unpack 成功证据；缺少真实 `native_gui_live` 同会话服务器与原生客户端日志时**非零退出**。目前此项必须 `NOT_TESTED`，不声明 P8 原服真实性通过。

每个命令输出机器可读 `MX15_<MODE>_RESULT.json`；子进程原始控制台日志写入 `docs/mx15/.runtime/logs/` 并被 `.gitignore` 排除；Git 仅提交退出码、通过项、测试耗时和 SHA256，不提交敏感登录内容。

## 失败必须关门

- 递归重复 JSON 键、损坏的 JSON、NaN 或字段校验失败，退出码非零。
- 核对外部客户端源码哈希失败、P0 tag 改变、缺 P67 原始证据、S2C 路由或 JSON/分派漂移，退出码非零。
- 任意子任务的非零退出码、超时、回归 STATS 不全、全量不是恰好 65 项或有失败项，退出码非零。
- 只有 `full` + `release` 的相应条件通过才可冻结/发布。原生 GUI `NOT_TESTED` 不会被误写成原生 GUI PASS。

## 本轮验收边界

MX-15 主要验收测试框架、JSON 门禁、回归严格性和证据质量；MX-25 原生 GUI 解析取证和 MX-27 原版 DLL XOR/LZSS 差分仍为独立任务。Git 分支提交本地，不自动合并到 P0 或其他工作树。
