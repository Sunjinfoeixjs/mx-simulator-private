# 《梦想世界2013》客户端逆向工程

> 多益网络《梦想世界2013》客户端 v1.1.50（已停服）本地逆向工程成果库。
> 本目录为该项目正式项目目录（2026-10-14 建立），所有新产出统一收敛于此。
>
> **目录整理（2026-10-16）**：源码、文档与工具链已按类别迁出本目录，归入 `MX/` 顶层：
> - `src/`、`scripts/`、`decoded/`、`dll/export/`、`tools/` → `MX/客户端源码/`
> - `tools/sim/`（自研模拟服）→ `MX/服务端源码/sim/`
> - `docs/` → `MX/协议资料/规格/`；`reports/` → `MX/协议资料/报告/`；`data/protocol/` → `MX/协议资料/字典/`
>
> 本目录（`Claw/`）现仅保留：`data/`（中间数据）、`dll/`（ghidra 脚本与日志）、`logs/`、`_refs/`、本 README。
> 下文出现的旧路径，请按上表对应到新位置。

## 合规声明

本项目为**本地已安装客户端**的互操作性与兼容性研究，目的限于：

1. 理解自有计算机上游戏客户端的数据格式与脚本机制；
2. 恢复因格式封闭而无法读取的自有数据（互操作）。

严格约束：

- 不用于破坏游戏平衡、不制作/使用外挂、不影响其他玩家或服务器；
- 提取的脚本与资源可能受软件许可协议约束，仅限本地研究使用；
- 不公开分发、不发布、不上传任何提取内容；
- 所有操作均在本地副本上进行，不连接、不修改任何在线服务。

## 成果现状（2026-10-14）

| 模块 | 状态 |
|---|---|
| script.fls 脚本解密 | ✅ 484/485（四层加密链：0SLF 容器 → zlib → PRNG 解混淆 → Py2.6 marshal + 条目 XOR + 分段密钥轮换 + 重映射 opcode，完全还原；仅 #165 未解出） |
| opcode 映射 / 反汇编 | ✅ **100.0000% 验证**（104→85 映射，17,017 代码对象 / 1,536,528 指令，0 未映射；V1–V6 结构不变量 0 违例） |
| marshal 读取端 STRINGREF bug | ✅ 已修复（2026-10-14，详见 `docs/工作区清理分析.md` 第二节；修复前 394 文件 / 18,265 处索引漂移 → 0/0） |
| 协议分析（server.ini / 登录 / 战斗 / 物品宠物） | ✅ 已合并自 04-22-42 工作区：opcode 字典 + 字段规格 + 6 区 205 服清单 + 联调闭环 72/72 |
| mxmain.dll 反编译 | ✅ 7,416 函数 / 292,560 行 C |
| 其余 14 个 .fls 资源归档 | ✅ 全部完成（15/15 归档、33,534 条目、0 失败；容器层 + 条目格式全探明，tpl 解码 2,986 个） |
| uncompyle6 源码还原 | ✅ **484/484（100%）**（B3 265 → B5 295 → **B6 465** → B7 **483** → **B9 484**）。B7 靠 P4（全局赋 None 前置 LOAD_GLOBAL，4 文件）+ P5（循环赋常量前置 LOAD_FAST，3 文件）+ **融合切片指令展开**（0x28-2B/0x48-4B 实为 `del x[a:b]`/`x[a:b]=v` 单指令，5 文件）共 **+18**；B9 以 **P7** 补齐「if 分支以循环回边结尾 + else 分支」（`cf_ja_cf_pop`，2.6 的 `JUMP_ABSOLUTE` 版缺产生式）解锁最后的 `dialog/dlgsysopt`（103/103 code object 通过，详见 `reports/B9_dlgsysopt解锁报告.md`）。源码落盘 `src/*.py`，清单 `data/manifest/uncompyle6_ok_b9.json` |
| 宠物面板 + 启动流程修复 | ✅ 2026-10-16：模拟服新增**宠物三帧**（S2C 48 SummonInfo / 49 SummonSkill / 51 PropView + C2S 129/131），离线端可打开宠物面板并显示属性/资质/抗性/技能（协议 7/7 + 真机实测，见 `reports/宠物面板实现报告.md`）；同时**消除启动器「脚本错误」模态框**（update.ini HTML 页指向本地空白页 + local_http 未匹配路径回空白 HTML），启动流程全自动；另识别 **C2S 192 = C2S_COMMON**（点击统计上报，战斗中也发，原会误触战斗保底） |
| 陈旧产物刷新（B9 附） | ✅ 2026-10-16：定位「磁盘产物早于管线修复」问题（`_n_yield_bare` 缩进修复晚于部分文件落盘），以 `tools/_refresh_targeted.py` 定向重解编译刷新 **78 个**文件；`src/*.py` 的 lib2to3 解析失败 **82 → 4（↓95%）**，颠倒赋值 0、裸 yield 缺陷 236/73 → 5/1。详见 `reports/B9_陈旧产物刷新报告.md` |
| **语法有效性 484/484（B9b）** | ✅ 2026-10-16：末 4 个文件按字节码**逐处修复**（`tools/_bctokens.py` 按函数转储 token/指令 + `_fixiter.py` 迭代定位）：`sre_compile`（丢缩进 1 处 + 嵌套 if/else 拆坏 2 块）、`dlgriderefine`（丢缩进 4 处）、`dlgtradecenter`（空 if 体补 `return`）、`warnings`（四段 AND 条件被截断粘连 + 丢缩进 1 处）。**lib2to3 解析失败 4 → 0**，裸 yield F1/F2 1/5 → **0/0**；清单刷新为 484 模块 / 330,684 行。详见 `reports/B9b_四处字节码级修复报告.md` |
| **物品系统（模拟服，2026-10-16）** | ✅ 新增物品协议 7 帧（S2C 32-38 / C2S 32-38，模块 `netitem`）+ 8 件初始道具（背包 pos 20.. / 战斗栏 pos 100..）：进世界逐件 `S2CItemAdd(33)`；悬停 `C2SItemString(35)` → `S2CItemString(38)`；拖动 `C2SItemDrag(33)` → `S2CItemPos(36)`；使用 `C2SItemUse(34)` → 数量递减 `S2CItemQuantity(35)` → 用尽 `S2CItemDel(34)`（回血类附 `PropChange(17)`）。协议级 **7/7** + 真机实测（逐件悬停 8/8、使用幻灵珠 3→2 并回血）。详见 `reports/物品系统实现报告.md` |
| **战斗深化（模拟服，2026-10-16）** | ✅ 补齐施法消耗扣减 + 战斗道具 + 保护/特技/召唤族/捕捉/子女回合推进（逆向 `netwar`/`nethero`/`hero.ITEM_ALL`）：`m_MP=18/m_FP=20/m_At=72/m_Df=73/m_Dp=74`；`S2CPropChange(17)` 按字宽（2B/1B）回资源帧；招式 1101 流水行云扣 fp15/df4；`C2S_WAR_ITEM(165)`/`167保护`/`173捕捉`/`162特技`/`166召唤`/`168-174召唤族`/`177子女` 均按速度序推进回合；`175队频`/`176查看` 不消耗行动权。协议级 **8/8**（`_test_war2.py`）。详见 `reports/战斗深化实现报告.md` |
| **资料库招式集成（2026-10-16）** | ✅ 投送《梦想世界2013资料全库.docx》→ 抽取 **385 招式**（11 流派×35），与 `src/data/magicdata.py` 按名联立 **382/385**（`tools/_parse_skills_docx.py` → `data/skills_joined.tsv`）；`tools/sim/gen_magic_cost.py` 生成 `magic_cost_data.py`：**MAGIC_COST 504 条**（全招式真实 mana/fp/at/df/dp）+ **MAGIC_INFO 504 条**（school/kind/genre/pow/4抗性/主修等级）。模拟服改教 6 个真实低阶招式（剑/佛/刀/拳/巫/阴阳），战斗对任意招式按 MAGIC_COST 扣资源。详见 `reports/资料库招式集成报告.md` |
| **持久化（模拟服，2026-10-16）** | ✅ 角色状态落盘 `data/save/role.json`，跨进程重启留存：位置(px,py)/HP/战斗资源(hero_res)/背包(items，键还原为 int)/宠物 HP/移动计数/金钱/物品id计数。触发点：使用道具、`_resync_scene`(战斗结束)、每 10 步移动、连接断开；`__init__` 载入存档（有存档则沿用上次位置，不再回出生点）。新增 `--fresh` 启动参数清空存档。修掉「有存档时启动即崩」(`_logf` 未初始化) 的真实 bug。冒烟 **PASS**（`_test_persist.py`） |
| **NPC 商店（模拟服，2026-10-16）** | ✅ 村长处开通购买：`C2S_NPC_LOOK(96)` 直接下发 `S2CNpcBuyList(147)` mode2（6 件真实造型道具 + 模拟价）；「我想买点东西」选项(choice2) 亦开商店；`C2S_NPC_BUY(99)=index(2B)+amount(2B)` 校验金钱→扣钱→逐件 `S2CItemAdd(33)` 入背包→确认聊天；金钱不足保护。新增金钱系统 `coins`(初始 5万) + 物品 id 分配器(自 9001 起)，均持久化。线格式逆向自 `src/netnpc.py`/`netshop.py`。冒烟 **PASS**（`_test_shop.py`） |
| **宠物出战（模拟服，2026-10-16）** | ✅ 宠物(梦想企鹅)作为己方战斗单位接入 `WarAdd(PET_WAR_ID=250, allied)`；按 `PET_SPEED` 落位速度序、AI 自动普攻（`build_war_attack_normal`）；敌方随机瞄主角/宠物 → 宠物可被击，HP 归零则 `WarDel` 本场退场。回合结算 `_resolve_hero_round` 改写为遍历全单位（主角/宠物/敌方）按速度序行动。`build_war_add` 的 war_id 为 1 字节（`HERO=0`/`ENEMY=1..N`/`PET=250`），曾因 `PET_WAR_ID=1002` 溢出 1 字节崩溃，已修正。战斗深化协议级 **8/8** 仍通过（`_test_war2.py`，宠物出现在行动序列）。新增 `--no-encounter` 启动参数关闭遇怪（纯探索地图） |
| **GM 调试面板（Web，2026-10-16）** | ✅ 浏览器控制面板 `tools/sim/gm_dashboard.html` + 模拟服 `--web`/`--web-port`（默认 8080）：打开 `http://127.0.0.1:8080/` 实时改角色状态——HP/法力/愤怒/攻防资/金钱/坐标/宠物 HP/遇怪频率、背包增删，**每次改动即时向已连接的游戏客户端下发对应协议帧**（改 HP→`PropChange(17)`、加物品→`ItemAdd(33)`、传送→`GotoM(97)`、战斗中宠物掉血→`WarHPPercent(125)`），未连客户端时仅改服务端状态（安全跳过）。`GET /api/state` 取状态、`POST /api/<action>` 下发；服务端线程锁保护 socket 并发。协议级帧下发 **4/4** 验证通过（`_test_gm.py`：连客户端改 HP/加物品/传送/断线不崩）。生产服 3134 已带 `--web` 运行 |
| **任务闭环（模拟服，2026-10-16）** | ✅ 把「只下 `S2CTaskAdd(72)`」的占位任务升级为**可完成闭环**：接任务(NPC 选项1) → **战斗击杀计数**（毛草团 3 只，每杀重发 `S2CTaskAdd(72)` 刷新任务栏进度）→ 达成 → 回村长交任务 → **`S2CTaskRemove(73)`** + 奖励（金钱 +500 / 经验 +200 / 幻灵珠×1）。奖励同步：金钱走 `S2CPropChange(m_Gold=43)`、经验走 `m_Exp(8)/m_NextExp(9)`（均 TDWORD 4B，`build_prop_change` 新增 width=4）。任务状态与经验落盘 `data/save/role.json`（`load_role` 增 tasks 键 int 还原）。新 NPC 首页随任务状态变化（未接/进行中/可交）。协议级冒烟 **PASS**（`tests/_test_task.py`）；`_test_battle_speed.py` 期望补入出战宠物单位（250），与 `_test_war2.py` 口径对齐 |
| **多地图 / 传送（模拟服 M7，2026-10-16）** | ✅ 5 张**真实连通**场景：永泽城(1001) → 新月平原(1006) → 谷雨镇(1003) → 清溪平原(1004) → 清溪城(1002)。**场景名非臆造**：由 `minimap_data[mapid]` 出口点名集合 × `findpathdata` 邻接表 **Jaccard 联立求解**（`sim/mapdata_resolve.py` 可复现；4/5 为 1.0，永泽城 0.25 另由资料全库坐标佐证「师道殿(241,149)」≈ 出口标记 (245,151)）；spawn/NPC 坐标取自 `npcposdata` 真实站位，出口坐标取自 `minimap_data` 真实标记。**两条传送路径**：①传送使 NPC（各图统一 id=1001，选项=`MAP_LINKS` 邻接场景）；②**走到出口坐标自动切图**（半径 4 格）。切图 = `S2CMapSceneM(96)`（含场景名/小地图）+ 该图全部 `AddNpc(99)` + `GotoM(97)`（对齐客户端 `S2CMapSceneM` 的 `world.ClearAllObject()` 契约）；战斗场景同步为当前图（`WarStart.scene = mapid-1001`）；当前地图随存档落盘。冒烟 **PASS**（`tests/_test_map.py`） |
| **真机复核修复×2（模拟服，2026-10-16）** | ✅ 真实客户端联调发现并修复两个**必现**缺陷：①**网络错误 007**——`mxgw` 时钟漂移检查 `cdt=ΔGetTime/100 > sdt=Δt+500`（超 500ms 即 `Disconnect(007)`），客户端 `C_sys.GetTime` 为 **10000Hz** 计数（`dlgcalendar.py:303` `/10000` 佐证），20s 间隔 cdt≈2000；旧回 **Unix 秒** → sdt≈520 → 2e3>520 每次第二查询必断。修复：op15（mxgw 通道）回**同源 10000Hz 滴答**（`build_server_time(mxgw_ticks=True)`，cdt=Δ/100 ≪ sdt=Δ+500 恒过；op192 日历通道保持 Unix 秒）。②**角色回弹**——`S2CMapTrack` 语义是「从 (x,y) 走到 (x+dx,y+dy)」，dx/dy 为**有符号增量**（`UnpackSignedChar`），旧实现恒发 dx=dy=0 → 角色每次被拉回原地；且旧实现把 C2S 起点当终点（日志永远 `移动 (45,40)`）。修复：按 `nethero._offset` 方向表解析 trace 求终点，回「起点 + 增量」。真机验证：角色跨图自由移动（`移动 (48,76)→(55,77)…`），长会话无 007。详见 `M4_协议规格.md` §1 / `build_server_time` 注释 |
| **对战录像复刻（敌方放招式，2026-10-16）** | ✅ 依据对战录像（野狼放「乱舞冲击」→ 技能弹字+紫色特效 → 命中宠物伤害 14）复刻敌方战斗逻辑：敌方以 `ENEMY_SKILL_CHANCE=0.4` 概率**用招式替代普攻**——发 `S2CWarPerform(122)`（客户端自动播技能弹字+特效）+ `WState(123)` 命中飘字 + 血条/收尾帧（招式库取客户端 magicdata 确认存在的 1103 落英缤纷，剑法群攻）。普攻路径不变；回合仍按速度序结算并交还操作权。新增 `--no-enemy-skill` 关闭开关（协议级战斗测试确定性用）。冒烟 **PASS**（`tests/_test_battle_skill.py`：强制 100% 放招 → 122 无 117、伤害/血条/收尾齐全；0% → 纯普攻；回合末 WarTurn）。`_test_shop/_test_persist` 改临时存档目录隔离真实会话存档；`_test_singleplayer` 关闭敌方招式保证行动序列断言 |
| **裸 yield → break 全量改写（B9c）** | ✅ 2026-10-16：四判据确认「本 VM 用 `YIELD_VALUE` 编码 `break`」——①全语料无生成器消费代码（`next()`/`send()`/`StopIteration` 命中 0）；②上游 CPython 2.6 标准库副本逐行比对（`lib/sre_parse.py` 12 处、`warnings.py` 1 处、`sre_compile.py` 2 块位置与上下文完全一致）；③字节码指纹聚类（757 个 `YIELD_VALUE`：循环内+紧随跳转 **733**，循环外或带值仅 24）；④语义自查（`netgroup.py` ×30 的 `if gid == 0: break` 协议终止符、`dlgmakenew` 的 for/else 查找）。据此把 **736 处裸 `yield`（179 文件）全量改写为 `break`**（备份 `logs/_bak_yield2break/`）；`break` 语句 6 → **742**、裸 `yield` 736 → **0**、lib2to3 仍 **0 失败**、行数/字节不变。详见 `reports/B9c_裸yield全量甄别与改写报告.md` |
| opcode 映射修正 B5 | ✅ **`0xDB/0xDC/0xDD` 并非 CALL_FUNCTION**：实测 `prop.py::AddRefreshCallback`（`self.AddRefreshCallbackWithID(0, cb, *arg)`）用 0xDB arg=2、栈上 4 项（函数+2 实参+`*args`），是 **CALL_FUNCTION_VAR** 语义；普通调用用 0xDA。原映射把三者都当 CALL_FUNCTION(131)，导致栈深错位、语句边界（POP_TOP 等）无法归约——已修正为 140/141/142，单次修正解锁 30 文件（详见 `docs/B5_失败聚类与opcode修正.md`） |
| 裸 yield 修正 B6 | ✅ 本 VM 的裸 `yield` 不发 `LOAD_CONST None`、作语句也不发 `POP_TOP`（标准 CPython 为 `LOAD_CONST None; YIELD_VALUE` + 尾部 `POP_TOP`）。uncompyle6 只有 `yield ::= expr YIELD_VALUE`，导致 `for ...: if cond: ...; yield` 的循环回边 `JUMP_BACK` 无法归约——这正是 JUMP 族（占剩余失败 89%）的主因。补 `yield ::= YIELD_VALUE` + `stmt ::= yield` + `n_yield` 覆盖后，9/9 样本转 OK（详见 `docs/B6_裸yield与JUMP族修正.md`） |
| 三角定位工具缺陷修复 | ✅ `_b4_triage.py` / `_diag_firstfail.py` 原有 `if cust:` 守卫会在 cust 为空 dict 时**跳过全部动态文法注入**，产生假失败结论（`uncompyle6.parser.parse()` 实为无条件调用）。已改为无条件调用 |
| 工作区清理（5 目录） | ✅ 已完成：148 个死代码文件全量备份后删除，协议套件合并入本目录（详见 `docs/工作区清理分析.md`） |
| 本地模拟服（M1–M4） | ✅ M1 登录接通 → M2 进世界 → M4 可玩闭环（移动 / NPC 对话 / 回合制战斗 / 任务）已跑通；2026-10-15 依**客户端录像**复核，修掉「放招式后永久卡『请等待』」等 4 处帧契约缺口（见 `reports/M4_客户端录像分析.md`、`docs/M4_协议规格.md` §6.2）。规格见 `docs/M1~M4_协议规格.md` |
| M5 帧契约修正 + 敌方回合 | ✅ 2026-10-15：**三处位域/符号语义**依客户端源码定死并修正（`ward` 必须=1(HIT) 否则演闪避；`hp_chg` **正数=扣血**；击杀 `life` 必须=2(DEAD) 才播死亡动画）；定位并修复「无法使用这个招式」根因（缺 `PropResist(18)`/`PropPower(19)`）；装备招式改走 `S2CSkillLevel(65) sktype=7`；新增**敌方回合**（`WarTurn` 开场 → 逐个敌方攻击 → 主角掉血走 `PropChange(17)` → 交还操作权）与**战败路径**；多单位战斗（`--enemies N`，对齐录像 6+ 单位）。自测 `tools/sim/_e2e_m4.py` 12 步 + `tools/sim/_e2e_m5.py` 2 项全过。规格见 `docs/M4_协议规格.md` §3.1/§7 |
| 后备项：server.ini 第二层 | ✅ **已核实为 RESOLVED**（2026-10-15）：计划里的「字段级编码」是 2026-10-13「ip_probe 17 轮」留下的**已被证伪的旧假设**；报告解出的「模256加法 cipher」才是正确算法，三层结构完整、`serverini_encode.py` 对 pristine 原件（4414B）与现盘文件（4396B）**均逐字节复现通过**。无遗留工作 |
| 后备项：resegment 303 条目 | ✅ **已核实为 RESOLVED**（2026-10-15）：303 条目已被正式管线完全覆盖（`scripts/*.py` **484/484** 解析成功、字节零残余；485 raw ↔ 484 scripts 双向闭环）；「分段密钥轮换」实现在 `tools/deobf26.py`（346/346 具名条目 100% 多段）。`resegment_xor.py` 盲恢复精度不足（6 样本仅 1 正确），**建议不再补跑** |

## 目录布局

```
Claw/                          # 逆向分析工作区（源码 / 文档 / 工具链已于 2026-10-16 迁出，见顶部说明）
├── README.md                  # 本文件
├── data/                      # 中间数据
│   ├── manifest/              # 容器清单 json + raw/ 485 条目 + pyc/
│   ├── samples/               # mxmain.dll 样本（md5 b50406bc…，含补丁件 mxmain_nocrypto.dll）
│   ├── fls_probes/            # .fls 格式探针输出
│   ├── fls_decrypted/         # .fls 批量解密输出
│   ├── dis_fixed/             # 关键脚本反汇编 11 个（名称已修复，登录/主循环/网络等）
│   ├── m4_calib/ save/        # 战斗标定 / 角色存档
│   └── skills_*.tsv           # 资料库招式联立表
├── dll/                       # ghidra_scripts/ + analyze.log / export.log
├── logs/                      # 各阶段运行日志
└── _refs/                     # 历史资产引用登记（不复制，只登记）

MX/                            # 整理后的顶层视图（2026-10-16）
├── 客户端源码/
│   ├── src/                   # 484 个还原 .py 源码（330,684 行）+ 484 个标准 .pyc
│   ├── scripts/               # 484 个已解密脚本（.py 后缀实为 marshal 数据）
│   ├── decoded/               # 485 个原始条目 *.bin
│   ├── dll_export/            # mxmain.dll 反编译（decompiled_all.c 7.2MB + 符号/导入导出表）
│   └── tools/                 # 逆向分析工具链：opmap/、protocol/ + 各阶段脚本（171 件）
├── 服务端源码/
│   ├── sim/                   # 自研模拟服（m4/m2/m1）+ 本地接管三件套 + GM 面板 + 联调工具
│   └── tests/                 # 服务端测试脚本 _test_*.py
└── 协议资料/
    ├── 规格/                  # 原 docs/：M1/M2/M4 协议规格、fls 格式规范、单机运行距离评估等
    ├── 报告/                  # 原 reports/：协议参考、逆向报告、源码还原清单等
    └── 字典/                  # 原 data/protocol/：protocol_map.json、C2S/S2C 全表、服务器列表
```

## 工具链速查

> 工具链已迁至 `MX/客户端源码/tools/`；模拟服与联调件在 `MX/服务端源码/sim/`。下表路径相对各自目录。

| 工具 | 用途 |
|---|---|
| `fls.py` | 0SLF(.fls) 容器解析 + 索引解密（`Fls` 类 / `decrypt_entry`） |
| `extract_all.py` | script.fls 一键解密管线（容器 → zlib → PRNG → marshal） |
| `deobf26.py` / `pymarshal26.py` | 2.6 marshal 读取端（结构解析；**STRINGREF 索引漂移已修复**，refs 仅在字符串中按序解析） |
| `marshal26_writer.py` | 2.6 marshal writer（观测结构 → 标准 pyc，`src/` 484 个即其产物） |
| `opmap_unfuse.py` | 融合指令消解（FOR_ITER / LIST_APPEND / CALL_FUNCTION） |
| `opmap/opmap.py` | 观测 opcode → 标准 CPython 2.6 opcode 映射（权威版本） |
| `opmap/opmap_disasm.py` | 脚本反汇编器（100% 覆盖，保底可读产物） |
| `fls_probe.py` / `fls_dump.py` | 资源 .fls 探针 / 批量解密（带断点续跑） |

opmap P0 验证四件套（`opmap/`）：`opmap_coverage.py`（覆盖率）、`opmap_validate2.py`（V1–V6 结构不变量）、`opmap_jumpstat.py`（跳转边界）、`opmap_e2e_cf.py`（调用帧栈模拟）。

协议分析链（`protocol/`，2026-10-14 合并自 04-22-42，路径已自包含）：

| 工具 | 用途 |
|---|---|
| `extract_protocol.py` → `enrich_c2s_module.py` | 协议抽取 + C2S opcode 模块归属补全（**须先 extract 再 enrich**） |
| `dissect_combat.py` / `dissect_itempet.py` | 战斗 / 物品宠物协议字段拆解 |
| `gen_combat_report.py` / `gen_itempet_report.py` | 字段拆解报告生成 |
| `gen_listings.py` / `scan_missing.py` | 清单生成 / 缺失扫描 |
| `analyze_tags.py` / `opcode_prefix_count.py` | tag 分桶语义 / opcode 前缀统计 |
| `extract_protocol_functions.py` → `gen_protocol_reference.py` | **协议参考生成链**：提取全部协议函数（`--scope all` = `net*.py` + 非 net 模块）→ 生成 `MX/协议资料/报告/协议参考.md` |
| `net_consts.py` / `net_routes.py` | `MX/客户端源码/src/net.py` 解析：opcode 常量表（C2S/C2GS/GS2C 三族 141 条）/ S2C 路由表 `NET_FUNCTION_NAME`（168 条） |
| `audit_protocol_map.py` | 常量表·路由表·提取结果三方对账 → `MX/协议资料/报告/协议参考_对账.md` |

协议参考现状（2026-10-15 扩面）：覆盖 **112 个模块**（`net*.py` 44 + 非 net 68）、**1504 个协议函数**
（C2S/GS2C 938 + S2C 566），含 opcode 双向索引与共享 opcode（子命令分发）分组；
对账修正了两处既有缺陷（`C2GS_*` 常量族缺失致 117 个函数 opcode 误判为 2；模块自带常量解析失败）。

离线模拟与联调（`MX/服务端源码/sim/`）：`serverini_sim.py`（server.ini 解码模拟）、`protocol_crypto.py`（协议加解密）、`entry_protocol_sim.py`（入口协议模拟）、`game_loop_demo.py`（全链路闭环：帐号 100860001）、`live_testbed.py`（联调测试台 72/72）；**本地接管三件套**：`serverini_encode.py`（server.ini 编解码，逐字节可复现）、`prepare_local_client.py`（local.ini + mx.ini 一键配置/还原）、`mock_server_min.py`（最小模拟服：Hello/25B/登录包解析，含自测）。

外部环境：uncompyle6 链路用 `C:\Tools\python38\python.exe`；Ghidra 12.1.4 + JDK 21 在 `C:\Tools\`。

## 执行路线（2026-10-14 批准）

1. ~~Phase D~~ ✅ 已完成（2026-10-14）：全部 15 个 .fls 归档批量解密完毕（33,534 条目 / 0 失败）
2. **Phase B**：uncompyle6 源码级还原 484 个脚本 —— ✅ **484/484（100%）**（B3 265 → B5 295 → B6 465 → B7 483 → **B9 484**；B9 以 P7 文法补丁解锁最后的 `dialog/dlgsysopt`，见 `MX/协议资料/报告/B9_dlgsysopt解锁报告.md`）
3. 后备项（另行指示再启动）：~~server.ini 第二层~~（✅ 已核实完成，见上表）、~~resegment 303 条目~~（✅ 已核实完成，见上表）、协议参考.md（✅ 已扩面至 112 模块 / 1504 函数）、#165 反查（⏳ 仍开放：缺模块名，属**命名**问题而非解密问题）
4. ~~工作区清理~~ ✅ 已完成（2026-10-14，详见 `MX/协议资料/规格/工作区清理分析.md`；本轮 2026-10-16 目录整理见顶部说明）

详细计划见 `C:\Users\Administrator\.workbuddy\plans\toasty-beacon-newton-cnHxck8_.md`。
