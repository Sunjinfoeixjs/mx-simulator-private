# MX-13 / P6-B: 160 条 S2C 路由发射覆盖率

## 验收数据（P0 隔离基线）
- P0 标签 p0-accepted 保持不变，独立分支 feature/mx-13-s2c-emission。
- 源清单 protocol_s2c_full.txt 共 160 条，160 个 handler，客户端 NET_FUNCTION_NAME 对应 160 个唯一 opcode。
- 第一列 tag 是客户端处理分组标记，不是 opcode。
- 原始分母 **160**，有证据排除 **0**，适用分母 **160**。
- 已完成实际 TCP sendall 成功发射：**98 帧，26 条唯一路由**。
- **自动化发射覆盖率 = 26 / 160 = 16.25%**。
- 由 Python 自动化接收端逐帧解析到 **25 条不同路由**；不是原生 GUI 的解析成功证明。
- 原生 GUI 实际发射与成功解析：**NOT_TESTED**（本轮没有 P0 原生 GUI 路由级观测），不能推导出 GUI 实际覆盖率。
- 发射但尚未验证原生 GUI 解析：**26**；未在本轮自动化测试发射的路由：**134**（NOT_TESTED，而非不可达）。

## 实际发射边界和等价性门禁
- Test-only 观察器只在底层 socket.sendall 正常返回后生成 SENT 事件。
- 合帧 FrameBatcher 缓冲不计数；flush 成功后独立拆分每一帧，扩展长度/半帧跨调用按字节流重组。
- 真实 TCP 连接下确认路由；FakeConn / 内存发送不得记入发射分子。
- 帧 SHA-256、opcode、route ID、测试名、物理调用栈和结果落入 emission_socket_actual.jsonl，不记录原始私密负载。
- 边界测试 PASS：分片、多帧合并、构造但不 flush 不计数、底层写失败不计数且原异常传播；逐字节比较送达数据未改变。
- 实际发射链包括普通 login/world/move/NPC，以及 GM 战斗开始/结束的异步推送。测试服务器在独立存档目录和回环端口运行。
- 源码 m4_server.py 未编辑；业务锁、协议格式、参数和发送顺序未调整。

## 发射命中的路由
| Route | Opcode | Handler | Frames | Status |
|---|---:|---|---:|---|
| R001 | 17 | nethero.S2CPropChange | 21 | PASS |
| R002 | 33 | netitem.S2CItemAdd | 10 | PASS |
| R007 | 50 | netpet.S2CSetFight | 1 | PASS |
| R009 | 48 | netpet.S2CSummonInfo | 3 | PASS |
| R010 | 53 | netpet.S2CSummonKey | 3 | PASS |
| R011 | 49 | netpet.S2CSummonSkill | 3 | PASS |
| R014 | 152 | netnpc.S2CNpcChatExt | 1 | PASS |
| R018 | 112 | netwar.S2CWarStart | 1 | PASS |
| R020 | 115 | netwar.S2CWarAdd | 3 | PASS |
| R025 | 116 | netwar.S2CWarDel | 1 | PASS |
| R026 | 113 | netwar.S2CWarEnd | 1 | PASS |
| R032 | 129 | netwar.S2CWarSummon | 1 | PASS |
| R034 | 114 | netwar.S2CWarTurn | 1 | PASS |
| R072 | 16 | nethero.S2CPropInit | 1 | PASS |
| R073 | 19 | nethero.S2CPropPower | 1 | PASS |
| R075 | 18 | nethero.S2CPropResist | 1 | PASS |
| R079 | 65 | nethero.S2CSkillLevel | 17 | PASS |
| R091 | 1 | netlogin.S2CHello | 1 | PASS |
| R092 | 2 | netlogin.S2CLoginAccountInfo | 2 | PASS |
| R097 | 6 | netlogin.S2CLoginPlayerAdd | 1 | PASS |
| R100 | 3 | netlogin.S2CLoginRoleInfo | 1 | PASS |
| R109 | 99 | netmap.S2CMapAddNpc | 16 | PASS |
| R114 | 97 | netmap.S2CMapGotoM | 2 | PASS |
| R117 | 96 | netmap.S2CMapSceneM | 2 | PASS |
| R119 | 105 | netmap.S2CMapSummon | 2 | PASS |
| R121 | 86 | netmap.S2CMapTrack | 1 | PASS |

其余 134 条路由逐项标记 NOT_TESTED，见 S2C_ROUTE_STATUS.csv。

## 静态总量与可达性
- 静态总路由 160；实测可达路由至少 26；其余 134 可达性未知。
- 未发现有证据可排除的别名/不可达项，故原始与适用分母均保持 160。客户端 net.py 额外映射不被隐式并入本次冻结清单。
- 客户端字典另有 8 项清单外 opcode（仅作审计，不调整 160 分母）：24 netgoodsserver.GS2CGetServerGoodsData, 41 netfindteam.GS2CGetData, 42 netfindfriend.GS2CGetFriendData, 44 netcontestbeauty.GS2CGetContestBeautyData, 55 netride.GS2CRideInfo, 78 netshop.GS2C_SHOP, 118 netwar.GS2CWarShape, 174 netunpackdialog.UnPackDialog。
- 发射事件不等于客户端成功解析；测试 Python 解码器与官方 GUI 是两个不同统计对象。

## 复现与限制
- route_inventory.py: 读取源清单和外部客户端 net.py，计算并存储 SHA-256；net.py 不在 P0 2593 文件冻结工作树中。
- emission_probe.py: 仅测试期间注入 socket sendall 观察器，正常服务端无该探针。
- run_emission_e2e.py: 真正 socket 集成及分片/合帧/发送异常验证。
- emission_socket_actual.jsonl / emission_boundary_tests.jsonl: 原始事件。
- mx13_execution_results.json: 真实客户端测试驱动及结果。
- summarize_emission.py: 从原始记录重算 coverage JSON、CSV 和本 Markdown。
- 需要进一步以更多真实 socket 场景覆盖任务、宠物、制作、交易等；不能把现有 26/160 当作游戏完整程度。
- 原版加密/LZSS、跨线程更多推送及实际 GUI 解析另需专项测试，不得凭构建函数存在补分。
