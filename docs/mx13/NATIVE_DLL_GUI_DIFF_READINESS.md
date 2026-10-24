# MX-27 / MX-25 原版客户端差分与 GUI 捕获前置条件

## 本机只读来源检查（2026-10-24）

- 原版 mxmain.dll：2754304 字节；SHA256 `2cc810c2747c61597f76ca3d0f7bd824a1f9d88e07fcb54ef792828afcf50389`
- 原版 ResLib.dll：345344 字节；SHA256 `d06775199b4c359b253536e762283820ccfca151cc89381b60716eed5c29d53c`
- 客户端静态路由字典 `net.py` SHA256 `98929e57e440d40950a8b530c3ec006eeb5999b500eb732495e513534699aa31`，属于外部分析快照，不是 P0 Git 原始文件。
- 原始 DLL 均可在主 MX 原始素材归档中定位；本次仅读取文件 SHA256，未覆盖、修补或加载到游戏进程。

## 本轮实际可证明的范围

- 独立真实 loopback TCP 中使用移植版 `mx_crypto.py` 与 `lzss.py`，XOR 加密帧 66 个经过 Python 独立 RX 解密和 LZSS 往返一致性检查，外加初始明文 Hello，总计该场景 67 帧。
- 这证明的是 **模拟服与 Python 测试接收器的一致性**，不是原版 `mxmain.dll` 客户端解密函数、原始 DLL LZSS 的执行等价性。
- 本次检查 Windows 运行进程时，没有发现已运行的原生游戏 GUI 或 x64dbg 调试会话，也没有新产生的客户端 handler 回调日志。
- `native_gui_live/server_physical_sent.jsonl` 和 `native_gui_live/native_client_parser.jsonl` 均不存在，当前 GUI 路由级计数保持 **NOT_TESTED**。

## 下一轮真实差分 / GUI 测试步骤

1. 在同一个经授权的原生客户端进程收集原版模块实际加载的 SHA256；如果模块版本不同，拒绝跨版本的差分比较。
2. 使用与原版 DLL 一致的 session seed、密钥状态、分帧边界与固定原始 payload，分别采集加密前的逻辑帧、加密后实际 wire 帧和原生客户端解密后的 payload 的 SHA256、长度及顺序。不在 Git 中保存明文账号口令。
3. 原版 DLL 加密函数和解压函数必须以**真实函数执行**为依据；不能用独立 Python 端的一致性冒充 DLL 侧差分。
4. 仅在底层 sendall 成功返回后记 SENT；需要有原生客户端解析成功回调（session id、opcode、handler、逻辑帧 SHA256），与服务器事件逐帧一一对应。
5. 在 `gui_parse_acceptance_gate.py` 严格门禁中独立计算原生解析分子；只提供截图或测试脚本接收记录的仍为 NOT_TESTED。
6. 通过后在各自独立 worktree 运行 P0 65 项完整回归，再更新 Linear 的 MX-25 / MX-27 状态。

**结论：原版 DLL 文件已定位并冻结哈希，原版函数执行差分与同会话 GUI 解码仍未实施，不能标记 PASS。**
