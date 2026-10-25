# MX-13 原生 GUI 路由级证据对账（历史 P1）

**结论：5 个 GUI 状态的截图及其哈希均匹配，但它们不是同一 P0 会话的 S2C 原始帧 + 客户端解析回调，因此本次可正式计入的 GUI_PARSE_VERIFIED = 0，覆盖率保持 NOT_TESTED。**

- 源证据：历史 P1 独立分支中的 GUI 截图、SHA256 manifest 和同会话服务器日志；只读取并验证 SHA256。
- 原始服务端日志包含未脱敏登录帧，所以本工具**只存原日志 SHA256**，不会将其内容复制到本分支或 Git。
- 下面按截图所对应的客户端可见功能与路由静态语义建立候选关联，不把它们改称 VERIFIED。

| Route ID | opcode | GUI 可见效果 | 截图文件 | 严格状态 |
|---|---:|---|---|---|
| R117 | 96 | world scene rendered | mx18_same_account_relogin_world.png | 功能佐证，未确认解析 |
| R072 | 16 | character grade and experience panel rendered | mx18_relogin_gui_lv2_exp20.png | 功能佐证，未确认解析 |
| R001 | 17 | native level-up button effect | mx18_gui_native_levelup_clicked.png | 功能佐证，未确认解析 |
| R018 | 112 | native GUI battle screen | mx18_gui_battle_started.png | 功能佐证，未确认解析 |
| R026 | 113 | battle turn UI; NOT proof this frame decoded | mx18_gui_battle_auto_action.png | 功能佐证，未确认解析 |

## 继续采集必须具备的证据
- 同一个原生 GUI 会话的 TCP 连接标识、服务器实际送出的 route ID/opcode/帧哈希/顺序。
- 客户端解析层成功返回的 opcode 与 dispatch handler、时间戳及会话/帧唯一编号。
- 帧发射与客户端解析事件可一一对应（加密会话需在解密/LZSS 后比较逻辑帧哈希）。
- 同会话对应的窗口截图/角色存档对照；只有完整链路才能升级为 GUI_PARSE_VERIFIED。
- 没有证据时保持 NOT_TESTED，不能靠现有 screenshot 或 net.py 的 handler 存在性提高 GUI 分子。
