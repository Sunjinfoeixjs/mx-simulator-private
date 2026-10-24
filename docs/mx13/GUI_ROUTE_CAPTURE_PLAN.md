# MX-13：真实 GUI 路由解析剩余验收方案

## 当前状态

- 已读验证历史 P1 的五张原生 GUI 截图（登录世界、人物经验、原生升级、战斗画面、战斗操作），SHA256 与当次 manifest 完全一致。
- P1 GUI 服务器日志证明客户端正常握手、登录、进入场景以及显示过战斗与经验，但日志没有逐条的 S2C 物理发射事件；也没有 mxmain.dll 解析成功回调。因此这些只是 GUI_FUNCTIONAL_CORROBORATION_ONLY。
- **本轮 MX-13 / P0 路由级 GUI_PARSE_VERIFIED = NOT_TESTED**，不能填 0% 作为已经测出的 GUI 发射覆盖率，更不能以历史 GUI 照片代替逐路由解析。
- GUI_GATE 程序 `gui_parse_acceptance_gate.py` 在缺少真实同会话证据时返回状态 NOT_TESTED 和退出码 2（已实测）。

## 要补的同会话最小证据链

1. 启动独立 P0 测试服务端及真实原版 GUI 客户端，以测试账号、独立存档、127.0.0.1 网络环境运行。优先选择不影响 P1 其他会话的动态端口；原有客户端 DLL 不覆盖、不热补。
2. 在物理 S2C 实际发送边界采集 `SENT`、session ID、route ID、opcode、logical payload SHA256、递增帧序号、时间、收发端点。压缩加密帧由观察器从密文字节解密、LZSS 解压得到同一个逻辑 payload 哈希，但仅在 socket.sendall 成功后记账。
3. 从原生 GUI 客户端的解析/分派函数回调采集 `origin=native_GUI_client_parser_hook`、客户端 PID、DLL 模块哈希、同会话 ID、opcode、handler、解密解压后的逻辑 payload SHA256、解析结果 SUCCESS/ERROR 和解析顺序；**必须实际附着原生客户端**，不能由 Python 测试端模拟。优先使用外部调试器的只读跟踪或不改 DLL 的可审计 hook，未经安全审查不得修改用户客户端二进制。
4. 在原生 GUI 中逐一打开人物面板、地图/移动、战斗、任务、宠物、商店/制作和交易面板。同步保存窗口截图（文件 SHA256）及服务端角色存档/状态；不要上传账号密码、会话 cookie 或带原始私密 C2S body 的日志。
5. `native_gui_live/server_physical_sent.jsonl` 和 `native_gui_live/native_client_parser.jsonl` 均由真实观测工具产生后，才执行 `gui_parse_acceptance_gate.py` 计算严格的 GUI 分子。同一会话中 opcode、route ID、logical payload SHA256 匹配，且解析成功才算 verified。
6. 可测试 Batcher.flush、加密/LZSS、并发推送和重登；报告分别统计 160 原始分母、适用分母、实际发送、客户端成功解析、发送但无法证明解析及未触发条目。不能通过缺失证据时的默认 0 提高或伪造覆盖率。

## 证据隔离和现实限制

- `native_gui_live/` 已加入 `.gitignore`，原始客户端解析日志应仅存在经授权的本机目录，防止登录资料泄露。
- 原 P1 GUI 会话和此 MX-13 P0 的 Git 源码并非同一套基线。必须在逐帧证据中记录具体分支/commit，不能跨版本提升 verified。
- 本轮加密/LZSS 测试使用真实回环 TCP 和独立 Python RealCipher 对象，但收发算法仍来自同一份移植版 `mx_crypto.py/lzss.py`；**不是本轮对原版 DLL 的新一轮差分验证**。原版客户端解密和 GUI 解析须单独记录。
