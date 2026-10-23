# P0 原版客户端实机验收

日期：2026-10-23（跨 22:39–01:20）  
本轮结论：**PASS（A–E 五项均有本轮原版客户端证据；发现 3 项非阻断缺陷，见 §5）**  
客户端：`D:\mxsj2013\client`（原版 1.2.73，`mx.exe` 2,176,256 B；实机使用其 asInvoker 副本，二进制同源）  
服务端：`服务端源码/tests/sim/m4_server.py`，隔离存档 `docs/acceptance_20261023/run2/save`，端口 12239，GM 面板 8099

## 0. 结论摘要

| 项 | 内容 | 结果 | 关键证据 |
|---|---|---|---|
| A 启动与登录 | 启动器 → 选服 → 账号登录 → 选角 → 进世界 → 属性面板 | PASS | `run2/shots/01_L1_login.png`、`06_L6_login2.png`、`08_L8_enter.png`、`17_L17_role_select.png`、`18_A1_ingame.png`、`01_A2_charpanel.png` |
| B 地图与 NPC | 场景移动、NPC 对话、切图 | PASS | `run2/shots/03_B2_moved.png`、`../shots/15_B8_label.png`、`04_B3_yongze.png` + `run2/server.log` |
| C 战斗 | 自然遇敌 → 客户端下指令 → 演出 → WarEnd → 回场景 → 经验/掉落 | PASS | `run2/shots/07_C1_battle.png`、`01_B_auto_attack.png`、`15_C9_now.png`、`23_D3_scene.png` + 协议日志 |
| D 存档 | 变更后退出 → 落盘 → 重登 → 前后值比对 | PASS | `run2/state_before_relogin.json`、`state_after_relogin.json`、`D_diff.json`、`run2/shots/01_relogin_15_world.png` |
| E 双开 | 两账号同时在线、各自移动/存档、断开互不影响 | PASS（本轮 22:11–22:25 取证） | `acceptance_20261023/server_console.log`（s1/s2 两会话）、`save_iso/role_abc123.json`、`role_abce123.json` |

## 1. 环境与隔离

| 前置项 | 结果 | 证据 |
|---|---|---|
| 客户端完整性 | PASS | `mx.exe` 2,176,256 B；`mxmain.dll`、`data`、`mx.ini`、`local.ini`、`update.ini` 齐备 |
| 本地指向 | PASS | `local.ini` 首行 `本地测试服 127.0.0.1 12239`；`update.ini` 的 `ServerList/MiniPage` 指向 `127.0.0.1:8080` |
| 隔离存档 | PASS | 服务端 `--save-dir docs/acceptance_20261023/run2/save --fresh`，未触碰正式存档 |
| 账号记忆清理 | PASS | `users/usr.lst` 清空、`mx.ini accountlist=` 清空（原文件备份在 `client_users_bak/`） |
| GM 面板 | PASS | `--web --web-port 8099`（仅回环），仅用于取样与切图，不参与战斗结算 |

## 2. 验收矩阵

| 测试 | 步骤 | 预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|---|
| A 启动与登录 | 启动服务端与客户端；选「电信3区 本地测试服」；输入账号 `abc123`；点登录；选角进入 | 握手成功、返回账号/角色信息、进入场景且属性正确 | 23:44:05 Hello PASS（16 ed 4b）；00:30:41 `>>> 登录: abc123` → `已载入角色存档`；00:31:10 `选角进入 id=1 hjsdhfsd` → `进世界：PropInit + 场景 天水平原 + 背包`；属性面板：1 级、气血 400/400、魔法 100/100、力道 100/100、攻/防/速 10 | PASS | 见 A 行证据 |
| B 地图与 NPC | 场景点击移动 2 次；点 NPC「战斗技能师」；切图 | 坐标变化与服务端一致；NPC 对话弹出；切图后场景/NPC 重建 | 移动 (111,64)→(112,63)→(114,57)（C2S 54，坐标逐帧一致）；点 NPC → `C2S opcode=96 id=1000` → 弹出带选项对话（「我还在打 / 我想卖点东西 / 没事，我随便看看」）；切图 `→ 场景 永泽城(1001)：SceneM + AddNpc×8 + GotoM(45,40)` 后画面与 NPC 列表一致 | PASS | `03_B2_moved.png`、`../shots/15_B8_label.png`、`04_B3_yongze.png` |
| C 战斗 | 野外走满 5 步触发遇敌；指令窗口内点击敌方；观察演出、结算、回场景 | WarStart 一次、指令上行、演出推进、WarEnd 后场景恢复、经验/掉落各一次 | 00:13:01 `遇怪！WarStart + WarAdd(主角100+宠物250) + WarAdd(敌方×1) + WarTurn`；00:13:05 `C2S opcode=160 body=01 00` → `普攻 100 → 1（50 伤害）`（**非超时默认**）；宠物默认普攻；00:13:35 `敌方全灭 → WarEnd` → `场景 天水平原：SceneM + AddNpc×2 + GotoM(82,61)` → `战斗经验 +60 → 900/1000` → `掉落：罗汉果 item 21301 bag 9009` | PASS | `07_C1_battle.png`、`01_B_auto_attack.png`、`15_C9_now.png`、`23_D3_scene.png`、`run2/server.log` |
| D 存档 | 记录经验/背包/坐标 → 关闭客户端 → 重开登录 → 比对 | 落盘成功且重登后一致 | 关闭时 `（连接结束）→ 已存档（位置 96,60，HP 400，背包 20 件，变更 2 键）→ 断开（剩余会话 0）`；重登 `已载入角色存档 role_abc123.json`；重登前后 **exp 1080、grade 1、coins 50000、地图天水平原、坐标 (96,60)、宠物 HP 330/780、背包 20 件 全部相等**（`D_diff.json` ALL MATCH True） | PASS | `state_before_relogin.json`、`state_after_relogin.json`、`D_diff.json`、`01_relogin_15_world.png` |
| E 双开 | 两客户端两账号分别进入；各自移动/存档；一端断开 | 互不串号、另一端继续、断开账号可恢复 | 本轮 22:11:19 起 `（会话数 2）`；22:16:27 登录 `abc123`、22:17:00 登录 `abce123`，两账号各自生成 `role_abc123.json` / `role_abce123.json` 并独立移动（第 1–36 次移动分属两个会话）；22:25:01 两会话各自独立断开，日志先记 `（剩余会话 1）` 再记 `（剩余会话 0）`，两份存档分别落盘，互不牵连；同账号在第二会话登录被明确拒绝（`⚠ 拒绝重复登录`）。注：本轮未做「一端崩溃注入、另一端继续战斗」的客户端侧实验，该路径由 `_test_multisession.py` 的 socket 用例覆盖 | PASS | `acceptance_20261023/server_console.log`、`save_iso/role_abc123.json`、`role_abce123.json` |

## 3. 证据清单

| 文件 | 内容 |
|---|---|
| `run2/server.log` | 本轮全部协议日志：Hello/登录/选角/进世界/移动/NPC/切图/WarStart/op160/伤害/WarEnd/场景重同步/经验/掉落/存档 |
| `run2/shots/01_L1_login.png` … `17_L17_role_select.png` | A 段界面：启动器公告、服务器选择、账号密码、角色选择 |
| `run2/shots/18_A1_ingame.png`、`01_A2_charpanel.png` | A 段进世界与人物属性面板 |
| `run2/shots/03_B2_moved.png`、`../shots/15_B8_label.png`、`04_B3_yongze.png` | B 段移动、NPC 对话、切图 |
| `run2/shots/07_C1_battle.png`、`01_B_auto_attack.png`、`15_C9_now.png`、`23_D3_scene.png` | C 段战斗开场/指令后/回合推进/战后回场景 |
| `run2/state_before_relogin.json`、`state_after_relogin.json`、`D_diff.json` | D 段重登前后逐字段比对 |
| `run2/shots/01_relogin_*.png` | D 段重登全过程 |
| `acceptance_20261023/server_console.log`、`save_iso/role_*.json` | E 段双开两会话与各自存档 |

## 4. 协议侧核对（日志原文摘录）

```
[23:44:05] ── 接入 ('127.0.0.1', 54819)（会话数 1）
[23:44:05] ① S2C Hello 已发
[23:44:05] ② Hello PASS ✓（16 ed 4b）
[00:30:41] >>> 登录: abc123
[00:30:41] （已载入角色存档 role_abc123.json）
[00:31:10] >>> 选角进入 id=1 name=hjsdhfsd
[00:31:10] 进世界：PropInit + Resist/Power + 养成同步 + 招式8 + 场景 天水平原(1005) + GotoM
[00:13:01] >>> 移动 (80,60)→(82,61) [第 1 次]
[00:13:01] 遇怪！WarStart + WarAdd(主角100 + 宠物250(WarAdd+召唤129)) + WarAdd(敌方×1 ids=[1]) + WarTurn
[00:13:05] ③ C2S opcode=160 body=2B: 01 00
[00:13:05] 普攻 100 → 1（50 伤害）
[00:13:35] 敌方全灭 → WarEnd（战斗结束）
[00:13:35] → 场景 天水平原(1005)：SceneM + AddNpc×2 + GotoM(82,61) + 随行宠物
[00:13:35] 战斗经验 +60 → 900/1000
[00:13:35] 掉落：天水平原 → 罗汉果（item id 21301，bag id 9009）
[00:20:58] （连接结束）
[00:20:58] 💾 已存档（位置 96,60，HP 400，背包 20 件，变更 2 键）
[00:20:58] ── 断开 ('127.0.0.1', 54819)（剩余会话 0）
```

## 5. 本轮发现的非阻断问题

| 编号 | 等级 | 现象 | 证据 | 影响 | 建议 |
|---|---|---|---|---|---|
| P0-R006 | P1/显示 | 人物面板「经验」恒显示 0/1000，服务端为 1080/1000 | `run2/shots/21_D1_panel_before.png` 面板 0/1000 vs GM `/api/state` exp=1080 | 仅显示，不影响结算/落盘 | 核对登录时是否下发经验字段（PropInit/养成同步），必要时补一帧 |
| P0-R007 | P2/启动流程 | 无外网时更新程序弹「获取更新列表失败」，需手点「进入游戏」；连续二次启动曾出现「复制自身出错」 | `run2/shots/X_updater.png`；启动器错误框「复制自身出错」 | 只影响启动体验，不影响已进入的游戏 | 本地 HTTP 增补 `newupdate.txt` 空清单；连续启动前先释放 `patch/mx.bin` |
| P0-R008 | P3/自动化观察 | 客户端窗口未真正激活时，登录/选角按钮不响应点击（点一下标题栏后正常） | 本轮 A 段排查记录 | 非游戏缺陷，属自动化驱动约束 | 驱动脚本先 `SetForegroundWindow` + 点标题栏 |

## 6. 复现步骤（本轮实际执行顺序）

1. 起隔离服务端：`m4_server.py --host 127.0.0.1 --port 12239 --save-dir run2/save --no-encrypt --fresh --web --web-port 8099`；同时起 `local_http.py --port 8080`。
2. 起客户端（asInvoker 副本）→ 关闭「刷新公告失败」→ 开始游戏 → 选「电信3区 本地测试服」→ 下一步 → 输入账号/密码 → 登录 → 选角「进入游戏」。
3. 场景内点击移动、点 NPC 触发对话；用 GM 面板切图验证 `SceneM + AddNpc + GotoM`。
4. 野外走满 5 步触发遇敌；在 30s 指令窗口内点击敌方提交普攻（服务端记 `opcode=160` 并立即结算，非超时）。
5. 记录 exp/背包/坐标 → 关闭窗口 → 重开登录 → 比对 `/api/state` 与存档 JSON。
6. 双开：两个客户端、两个账号，分别移动/存档，观察 `会话数 2 → 1 → 0` 与两份独立存档。

## 7. 残余风险

- 双开的「一端异常崩溃（非正常关闭）后另一端继续」只用自动化 socket 用例覆盖，未在客户端侧做崩溃注入。
- 客户端面板经验显示缺陷（P0-R006）会让玩家误判升级进度，建议下一轮修复后复验。
- 更新程序依赖本地 HTTP 的覆盖范围，换机器部署时需同步 hosts 与 8080 服务。
