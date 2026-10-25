# MX-13：160 条 S2C 路由扩大测试

- 原始静态分母：160，已证实排除：0，调整适用分母：160。
- 真实 TCP 成功发射帧：251；自动化实际发射路由：36/160 = 22.50%。
- 未发射路由：124，标记 NOT_TESTED，并非已证明不可达。
- 原生 GUI 解析覆盖率：NOT_TESTED。不能用 Python 接收端解析或历史 P1 GUI 截图顶替。
- 原版客户端 NET_FUNCTION_NAME 的 8 个清单外映射仅单列，不擅自调整分母。

## 场景
| 场景 | 实际物理发射帧 | 唯一路由 |
|---|---:|---:|
| plain_login_move_GM | 98 | 26 |
| quest_pet_craft_trade | 86 | 29 |
| xor_lzss | 67 | 21 |

## 自动化实际发射的路由
| Route ID | opcode | 客户端 handler | 发射次数 | 场景 |
|---|---:|---|---:|---|
| R001 | 17 | nethero.S2CPropChange | 51 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R002 | 33 | netitem.S2CItemAdd | 32 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R003 | 34 | netitem.S2CItemDel | 1 | quest_pet_craft_trade |
| R007 | 50 | netpet.S2CSetFight | 3 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R009 | 48 | netpet.S2CSummonInfo | 5 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R010 | 53 | netpet.S2CSummonKey | 5 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R011 | 49 | netpet.S2CSummonSkill | 6 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R014 | 152 | netnpc.S2CNpcChatExt | 5 | plain_login_move_GM, quest_pet_craft_trade |
| R018 | 112 | netwar.S2CWarStart | 1 | plain_login_move_GM |
| R020 | 115 | netwar.S2CWarAdd | 3 | plain_login_move_GM |
| R025 | 116 | netwar.S2CWarDel | 1 | plain_login_move_GM |
| R026 | 113 | netwar.S2CWarEnd | 1 | plain_login_move_GM |
| R032 | 129 | netwar.S2CWarSummon | 1 | plain_login_move_GM |
| R034 | 114 | netwar.S2CWarTurn | 1 | plain_login_move_GM |
| R039 | 164 | nethero.S2CNotifyExt | 1 | quest_pet_craft_trade |
| R072 | 16 | nethero.S2CPropInit | 3 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R073 | 19 | nethero.S2CPropPower | 3 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R075 | 18 | nethero.S2CPropResist | 3 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R079 | 65 | nethero.S2CSkillLevel | 52 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R089 | 87 | netliving.S2CProduce | 2 | quest_pet_craft_trade, xor_lzss |
| R091 | 1 | netlogin.S2CHello | 3 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R092 | 2 | netlogin.S2CLoginAccountInfo | 6 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R097 | 6 | netlogin.S2CLoginPlayerAdd | 3 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R100 | 3 | netlogin.S2CLoginRoleInfo | 3 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R109 | 99 | netmap.S2CMapAddNpc | 32 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R114 | 97 | netmap.S2CMapGotoM | 4 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R117 | 96 | netmap.S2CMapSceneM | 4 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R119 | 105 | netmap.S2CMapSummon | 4 | plain_login_move_GM, quest_pet_craft_trade, xor_lzss |
| R121 | 86 | netmap.S2CMapTrack | 1 | plain_login_move_GM |
| R122 | 160 | netnpc.S2CMakeCompleteNotify | 1 | quest_pet_craft_trade |
| R123 | 147 | netnpc.S2CNpcBuyList | 1 | quest_pet_craft_trade |
| R126 | 151 | netnpc.S2CNpcDetails | 2 | quest_pet_craft_trade, xor_lzss |
| R134 | 155 | netnpc.S2CNpcMakeCost | 2 | quest_pet_craft_trade, xor_lzss |
| R140 | 51 | netpet.S2CPropView | 1 | quest_pet_craft_trade |
| R156 | 72 | nettask.S2CTaskAdd | 3 | quest_pet_craft_trade |
| R157 | 73 | nettask.S2CTaskRemove | 1 | quest_pet_craft_trade |

其余未命中路由及对应 NOT_TESTED 状态见 S2C_ROUTE_STATUS_EXPANDED.csv。

## 发送/客户端解析边界
- 只在底层 socket.sendall 正常返回后计算；FrameBatcher 未 flush 时不计数，发送失败不计数。
- XOR 加密场景以实际发出的密文字节为观察对象，使用独立 RX 流解密后还原 LZSS 压缩帧，再取真实 opcode。
- 场景涉及登录、GM 战斗、任务接取和查询、宠物查看、制作费用、商店、交易中心提示、矿点查询。
- 加密/LZSS 采用 P0 真实 loopback TCP、Python 模拟客户端和原算法；并非原生 GUI 解码通过。
- 每个场景 JSONL 和 SHA256 在机器报告 scenarios 中登记，可重算并集。未更改业务公式、协议字段、客户端 DLL 或 P0 标签。

## 剩余阻断
- GUI route-level 解析事件不存在；原生 GUI 功能截图只能作为关联佐证，不纳入 GUI_VERIFIED 分子。
- 剩余 124 条未触发路由需要继续扩充真实 C2S 与异步通知场景。
