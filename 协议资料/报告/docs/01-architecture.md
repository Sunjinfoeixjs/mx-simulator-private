# 架构：启动链 / 字节层 / 登录 / 会话

> 本册是《模拟服全链路代码解析》的拆分分册：**进程怎么起来、帧怎么切、握手怎么过、一条报文的主循环与分派契约**。
> 完整版（唯一现状依据）：[`../模拟服全链路代码解析.md`](../模拟服全链路代码解析.md)。
> 拆分只为聚焦阅读，内容与主文档逐字一致；**改内容请改主文档**，然后重跑
> `python 协议资料/报告/_split_docs.py` 重新生成本册。
>
> 文档双向体检：`python 协议资料/报告/_check_anchors.py`（已挂进 `tests/_run_all.py` 第 ④ 层）。

---
## 0. 总览

### 0.1 一句话架构

客户端 `mx.exe`（内嵌 Python 2.6 脚本层）**不知道服务端是假的**——它只认 TCP 上的私有协议。
模拟服做的事只有一件：把「原版服务端该回的字节」按对端 `Unpack*` 的线序拼出来。

```
singleplayer.py（唯一进程）
 ├─ M4Server（守护线程，127.0.0.1:0 自动端口）      ← 游戏内核
 ├─ local_http（127.0.0.1:8080）                   ← 劫持 update.ini 的 ServerList / 内嵌页
 ├─ local.ini / mx.ini 改写（端口跟随内核实际端口）
 └─ mx.exe 子进程托管（客户端退出 → 落档 → 宿主退出）
```

**加密前提**：原版 `mxmain.dll` 的 `+0x72` 是「加密开关」，非 0 时收发都过 `0x43cc50`。
客户端被打了 **`+0x72 = 0` 一字节补丁**，走明文裸发——模拟服的运行时链路依赖这一字节。

> **2026-10-19 更正**：早先「`0x43cc50` 落在 VMProtect 区段、静态不可还原」的判断已作废。
> 那个结论只对**入口 thunk**成立；真正的密钥表生成器 `0x43CA70` 在 `.text` 里未被虚拟化。
> 现已在 `sim/mx_crypto.py` 纯 Python 还原（4096B 密钥表 + 线性游标 XOR 流，收发各一份状态），
> 并用原版 DLL + unicorn 逐字节交叉验证（15 种子 × 2 方向）。
> `protocol_crypto.RealCipher` 已从「占位」改为**薄适配层**（转发 `mx_crypto`），
> `make_ciphers(seed, real=True)` 可切真实算法。
> 但**运行时仍走明文**：加密能力是「可复现/可验证」，不是「已启用」——客户端补丁还在，
> 双方都没启用加密。

### 0.1.1 Tap/加密层次修正（2026-10-21）

早期实现中，`TappedConn` 包在 `CryptoConn` **外面**（调试台套 socket → 内核套加密），导致抓包器只能看到**加密后的字节**，无法解码 S2C LZSS 压缩载荷。

现已调整为**内核在 handle() 内部把 `CryptoConn` 包在 `TappedConn` 里面**：
```
原：socket → TappedConn → CryptoConn → 内核 handle()
改：socket → CryptoConn → TappedConn → 内核 handle()
```
（代码位置：`m4_server.py` `handle()` 约 3250 行 `if _CRYPTO_MODE:` 块内）

**后果与修正：**
| 问题 | 原因 | 修正 |
|---|---|---|
| Tap 看不见明文 opcode | `TappedConn` 在加密层外 | 内层 `TappedConn.feed()` 直接收明文 |
| S2C 注入帧客户端解不出 opcode | 注入走 `TappedConn.sendall()` 绕过 `CryptoConn.encrypt` | 注入时用**内核的** `make_s2c()`（会按 `_CRYPTO_MODE` 决定是否 LZSS 压缩） |
| S2C LZSS 解不开 | 压缩后字节被 XOR 了 | `FrameTap._maybe_lzss()`：对收到的 S2C payload 做**去压缩往返检验**（`lzss.compress(decompress(p)) == p`），只把确认为压缩的帧解开 |

> **设计原则**：不隐藏偏差。往返检验失败的帧直接原样返回，保证「明文帧与压缩帧混在一起时也不误判」。`tests/_test_tap.py` 与 `tests/_test_debugger.py` 端到端覆盖此链路。

### 0.2 模块职责

| 模块 | 行数 | 角色 |
|---|---:|---|
| `m4_server.py` | 11608 | **内核**：常量表、`build_*` 组包、连接/会话、C2S 分派、战斗状态机、GM HTTP |
| `tap.py` | 2174 | **解码 + 拦截层**：`TappedConn` 包 socket、`FrameTap` 抓包、`OVERLAY` 覆盖表、世界/战斗模型 |
| `frames.py` | 400 | **战斗帧字段的唯一来源**：`SPEC` 定义 → 生成收/发包 + 调试台解码规格（§2.5） |
| `bus.py` | 261 | **业务事件总线**：只观测不驱动，环形缓冲 2000 条，6 类事件（§11.6） |
| `debugger.py` | 629 | **可视化调试台**（HTTP + SSE）：建 `FrameTap`、挂内核、推浏览器、注入/丢弃/导出 |
| `singleplayer.py` | 591 | **宿主**：内核 + HTTP 劫持 + 客户端配置 + `mx.exe` 生命周期托管 |
| `transport.py` | 212 | 传输抽象：`LoopbackListener` / 进程内 `make_memory_pair`（测试用，运行时不用） |
| `world_config.py` | 767 | 世界数据外置层：地图/NPC/出口/连通/战斗调参（含物伤公式 18 个参数）的读写与校验 |
| `coverage_smoke.py` | 859 | 交互巡检：脚本化走全路径，产出**运行时**覆盖证据 |
| `editor_server.py` | 271 | 世界数据可视化编辑器（HTTP，写 `world_config.json`） |
| `quest_data.py` | 1697 | 主线剧情 1–16 的**数据与 NPC 摆位**（手写，非生成物，§6.5） |
| `juqing_skill_data.py` | 62 | 剧情技能表（生成物）：剧情点的唯一消费入口 |
| `encounter_data.py` | 113 | 场景遇怪表（生成物）：43 个野外/迷宫场景 × 怪物名 → 真实 shape（§5.5） |
| `battle_formulas.py` | 275 | 真值战斗公式：`petfightdata`/`petdata` 的成长公式 + 资料库规则（§7.5.3） |
| `progression_service.py` | 168 | **角色最终属性唯一入口**（P3-01）：基础+成长+加点+装备的纯函数，派生值只算不存（§6.6） |
| `equipment_rules.py` | 123 | 装备判定（P3-01）：槽位/等级门纯判定 + 三处缺口常量，零副作用（§6.6） |
| `equip_data.py` | 295 | 装备真值表（生成物）：252 条装备的名称/类型/槽位/等级门（§6.6） |
| `gen_equip_data.py` | 230 | 装备表生成器：从 `itemdata` 的 `EQUIP2POS`+`ITEMTYPE`+`【等级】` 提取，**生成即对账** |
| `pet_progression.py` | 270 | **宠物属性唯一入口**（P3-02）：面板层/战斗层/资源层三个纯函数 + 加点明细（§6.7） |
| `pet_skill_service.py` | 214 | 宠物技能表增删（P3-02）：学/遗忘/替换 + 去重 + 上限 + 绑定归一 + 白名单（§6.7） |
| `pet_skill_data.py` | 460 | 宠物技能真值表（生成物）：404 条，名称/类型/描述/FP-MP-HP 消耗原文（§6.7） |
| `gen_pet_skill_data.py` | 244 | 技能表生成器：GBK 字节串解码 + 已知技能断言，生成即对账 |
| `battle_rng.py` | 265 | **战斗随机源**（P3-03）：固定种子 + 11 条用途流 + 流间隔离 + `state()/restore()`（§6.8） |
| `combat_effects.py` | 506 | 状态效果机制（P3-03）：`EffectSet` 施加/叠加/到期/驱散 + 29 条数值转录 + DOT/强制防御（§6.8） |
| `buff_data.py` | 624 | 状态真值表（生成物）：284 条描述 + 249 条图标 + 34 条含量化（§6.8） |
| `gen_buff_data.py` | 271 | 状态表生成器：GBK 双路径解码（含 145 号 str 残骸）+ 关键条目断言 |
| `enemy_ai.py` | 178 | 敌方行动决策（P3-03）：`pick_target` / `decide_action` / `explain` 三个纯函数（§6.8） |
| `drop_data.py` | 334 | 掉落池**真值表**（生成物，勿手改） |
| `make_recipes.py` | 152 | 制作配方表（生成物）：48 个产物（真值）+ 12 种真实材料 + 配比（模拟） |
| `gen_make_recipes.py` | 205 | 配方表生成器：产物取 craftdata × itemdata，材料按名称材质词归类到真实 itemdata id |
| `gen_encounter_data.py` | 173 | 遇怪表生成器：场景表 × `monsterdata` 名字 → shape |
| `gen_juqing_skills.py` | 110 | 剧情技能生成器：`skilldata` × `juqingdata` |
| `damage_model.py` | 159 | 物伤公式纯函数（双模型：`reverse` 默认·反编译还原 / `linear` 可选·加减法；已接入战斗，§7.5.2） |
| `magic_cost_data.py` | 3127 | 招式消耗/属性表（生成物，勿手改；逐级表达式，§7.8） |
| `team.py` | 290 | 组队态（单机假人队友：只做表现，不进行动序） |
| `live_testbed.py` | 342 | 真机联调台：起内核 + 拉起真实客户端 + 端口一致性校验 |
| `entry_protocol_sim.py` | 213 | 离线登录重建（教学用：Connect/Hello/LoginIn 三步） |

**分工一句话**：`m4_server` 把协议跑起来，`tap` 把字节看懂，`debugger` 把字节画出来。
三者只通过 `tap.install(m4_server)` 一行耦合，且是零侵入的。

### 0.3 全链路数据流

```
【启动链】
singleplayer.main
  ├─ M4Server.serve_background(port=0)      → 内核线程 bind/listen，回填真实端口
  ├─ rebuild_server7_bin(port)              → 改本地服务器列表编码件里的端口
  ├─ start_local_http(8080)                 → 客户端刷新列表/内嵌页不弹窗
  ├─ on_ready(srv, port, client_dir)        → 调试台挂 tap.install（必须早于客户端接入）
  ├─ rewrite_local_ini / update_mx_ini      → 客户端指向 127.0.0.1:真实端口
  ├─ check_prereqs                          → update.ini 版本 + mxmain.dll 补丁校验
  └─ launch_client                          → Popen；WinError 740 → ShellExecuteEx(runas)

【一条报文的生命周期（以移动为例）】
客户端 nethero.C2SMapMove
  │  PacketPrepare(54) / PacketAddI / PacketSend
  ▼
TCP 127.0.0.1:port
  ▼
① TappedConn.recv        tap.py        ← 上行拦截：字节喂 FrameTap.feed('C2S')
  ▼
m4_server.recv_c2s       m4_server.py  ← 拆 [op][len][body]
  ▼
M4Server._dispatch       m4_server.py  ← 顺序敏感的长 if/elif
  ▼
build_map_track          m4_server.py  ← 组 op86 帧体
  ▼
conn.sendall
  ▼
② TappedConn.sendall     tap.py        ← 下行拦截：喂 FrameTap.feed('S2C') → 真发
  ▼
客户端 net.OnServerCmd → GetFunc(op) → netmap.S2CMapTrack → 渲染

【旁路（不影响主链路）】
FrameTap.feed → split_frames → decode_fields/decode_best
  → 环形缓冲 → debugger subscribe → SSE → 浏览器

【业务旁路（2026-10-22）】
内核业务判定点 → bus.emit(kind, …) → 环形缓冲 2000 条
  → debugger /api/bus → 「任务链」「经济」页签
```

### 0.4 帧批合并：省的是写次数，不是帧

一次上行指令往往要回一大串帧（进世界 45 帧、切图整包、战斗演出链）。
每帧一次 `sendall` = 每帧一次系统调用 + 一次可能分片的 TCP 写，客户端收包节奏
被切成锯齿状。`FrameBatcher` 把连发的帧攒成一次写：

```
内核 sendall → FrameBatcher.sendall → 缓冲 → flush（一次真写）
                 ↑ 三条硬约束：① 任何阻塞读之前必须 flush（不跨等待合批）
                              ② 合批层在抓包器**下面**（TappedConn 仍逐帧回调）
                              ③ 不走分派路径的推送（GM/HTTP 线程）显式 flush
```

帧是自定界的（`[1B N]` 或 `[FF][2B N]`），所以「拼成一次写」与「分 N 次写」
在字节流上**完全等价**——这是该优化零风险的前提，也是回归里那条
「开合批与不开合批逐字节一致」断言的意义。开关 `FRAME_BATCH`，
阈值 `FRAME_BATCH_MAX_BYTES`（超了立即 flush，避免超长链把延迟顶上去）。
统计字段 `frames / writes / auto_flushes` 用来回答「到底省了多少次写」。

---

## 1. 进程与启动链（`singleplayer.py`）

### 1.1 启动顺序

`main`严格按下列顺序，顺序本身有依赖：

| 步 | 动作 | 失败后果 |
|---|---|---|
| ① | `M4Server(...)` + `serve_background(port=0)` | 5s 内未 bind 成功 → `RuntimeError` |
| ①½ | `rebuild_server7_bin(port)` | 列表刷新仍指向旧端口，刷新后连不上 |
| ①½ | `start_local_http(8080)` | 客户端弹「服务器列表需要刷新」+「脚本错误」模态框 |
| ①¾ | `on_ready(srv, port, client_dir)` | 调试台挂晚了 → 第一条连接抓不到 |
| ② | `rewrite_local_ini` / `update_mx_ini` | 仍连官方服 → 中止启动（防误连外网） |
| ② | `check_prereqs` | 只告警，不阻断 |
| ③ | `launch_client` | 找不到 exe / 提权失败 → 落档退出 |

**端口不再硬编码**：`port=0` 让 OS 挑空闲端口，再按真实端口回写客户端配置，
彻底消灭「3134 被占用 → 连不上」这类故障。

**生命周期合一**：客户端 `proc.wait()` 返回后 → `srv._persist()` → 关 HTTP → 宿主退出，
不会留下一个没人连的空转服务端。

### 1.2 客户端配置三件套

| 文件 | 谁写 | 关键点 |
|---|---|---|
| `local.ini` | `rewrite_local_ini` | **必须是明文 GBK**。客户端 `C_sys.RecordString` 是恒等函数，写成三层加密格式会弹「服务器列表需要更新」 |
| `mx.ini` | `update_mx_ini` | 只动 `[Game]` 的 `serverini=` / `refreshserver=`，其它键原样保留；`refreshserver` 写 `4102444800`（2100 年）跳过自动刷新 |
| `update.ini` | 人工 / `--fix-update` | `Version` 必须 `1.2.73`；官方补丁链已断，版本不符会卡在更新流程 |

补丁校验（`check_prereqs`）：

- `mxmain.dll` 的 md5 若等于 `b50406bc…`（**未打补丁原件**）→ 告警：加密+压缩仍开启，
  模拟服只发明文，客户端解不开帧。

**已知端口冲突**：`--web`（GM 面板）默认也用 8080，与 `--http-port` 默认值相同。
`serve_background` 先起 GM 面板，`start_local_http` 后起 → 后者绑定失败。
两者要同时用，必须显式 `--web-port <其它端口>`。

---

## 2. 字节层：帧格式与握手

### 2.1 C2S / S2C 帧头字节序相反

这是全链路第一个坑，也是协议本身的规定：

```python

def make_s2c(opcode, data=b''):
    """S2C 帧：[1B N][opcode 1B][data]，N 含 opcode。"""
    payload = bytes([opcode]) + data
    if len(payload) >= 0xFF:
        return b'\xFF' + struct.pack('<H', len(payload)) + payload
    return bytes([len(payload)]) + payload


def recv_c2s(conn, timeout=30):
    """C2S 帧：[opcode 1B][1B 长度 N][body N]，N==0xFF 扩展 0xFF+2B u16。"""
```

| 方向 | 布局 |
|---|---|
| C2S | `[op 1B][N 1B][body N]`，`N` **不含** opcode |
| S2C | `[N 1B][op 1B][data]`，`N` **含** opcode |
| 扩展 | `N == 0xFF` → 后跟 `u16` 小端长度 |

### 2.2 字符串两种线序，不能混

```python

def var_gbk(s):
    """变长字符串（UnpackString(0)）：GBK 字节 + \0 结尾（C 字符串，无长度前缀）。
    ★实测修正（2026-10-15 客户端截图）：此前误加 [1B len] 前缀——长度字节被渲染成
    名字前的「口」（如「口野猪」），且无 \0 时客户端越界读吞掉后续帧，战斗卡「请等待」。"""
    return s.encode('gbk', 'replace') + b'\x00'
```

| 类型 | 用法 | 例子 |
|---|---|---|
| `UnpackString(0)` | GBK + `\0`，**无长度前缀** | NPC 名、对话、desc、场景名 |
| `UnpackString(n)` | 定长 `n` 字节，补 `\x00` | 角色名 20 / 任务名 26 / NPC 名 20 |

### 2.3 Hello：唯一的裸区

| 步 | 方向 | 内容 |
|---|---|---|
| ① | S2C op1 | `[len][1][seed(4)][serverNum(2)]` |
| ② | C2S **裸 25B** | `[obf 3B][0×22]`，**没有帧头** |
| ③ | 服务端校验 | `obf = ((seed + 2^32) % 3227993 + 2562088) ^ 6636321` |

客户端侧（`login/netlogin.py` 的 `S2CHello`）：解 `seed`/`serverNum` → `SetSeed(seed)`
→ 算 3 字节 → `NakedSend(25B)`。服务端用 `hello_response`逐字节比对。

**这 25 字节必须从分帧器里排除**：首字节是混淆值，当长度会直接把 C2S 缓冲卡死、
吞掉后续所有上行帧。`tap.py` 专门标了「裸区」。

### 2.4 三角一致性

同一段字节有三处解释者，任一处漂移都会「读错位」而不报错：

```
build_*（服务端写, m4_server.py）
      │  ① 一致
      ▼
 OVERLAY（调试台解码, tap.py）  ◀──② 一致──  Unpack*（客户端读, nethero/netmap/netpet…）
```

- ① 由 `_audit_build_overlay.py` 审计（见 §13）；
- ② 由 `_extract_wire.py --audit` 对账（见 §12）。

### 2.5 战斗帧的第四方：`frames.py` 的 `SPEC`（2026-10-22）

战斗帧的字段原本有三份实现、靠人肉保持一致，并且**各自漂移过一次**：

| 份 | 位置 | 漂移史 |
|---|---|---|
| ① 发包 | `m4_server.build_war_*` 手写 `struct.pack` | —— |
| ② 收包 | `_dispatch` / `_finish_pet_turn` 手写 `body[0]` + `struct.unpack` | 与①**没有任何一处共用定义** |
| ③ 调试台 | `tap.OVERLAY` 字段规格串 | 第三份 |

踩过的三次：166 的条件字段、169 的缺 `id`、115/123 的位序。

现在收敛成 `frames.SPEC` 一份定义，收发与解码都由它生成：

```python
frames.pack_s2c('S2CWarWState', id=1, hurt=1, life=0, ward=1, hp_chg=50)
frames.unpack_c2s(169, body)        # → {'target': 2, 'perform': 415}
```

规格串语法与 `tap.OVERLAY` 同形（`name:N` 无符号、`name:Ns` 有符号、`name:s` C 字符串、
`name:fN` 定长串），只多两个构造。调试台的解码规格由 `tap_specs()` 从这里派生，
`tap.py` 直接合并——**第三份不再存在**。

> 这条改动的价值不在「少写代码」，而在「三份定义不可能同时错」。
> `tests/_test_battle_flow.py` 的每一帧都用 `frames.unpack_s2c()` 解，
> 等于在验行为的同时**反验**「服务端发出的字节与 `SPEC` 一致」；
> `_audit_build_overlay.py` 则继续守 ①↔③。

---

## 3. 登录 → 选角 → 进世界

### 3.1 上行帧

| op | 常量 | body | 服务端动作 |
|---|---|---|---|
| 1 | `C2S_LOGIN_ACCOUNT_ENTER` | `ver(4) + name(32) + pwd(20) + …` | 只取 `body[4:36]` 当名字；有角色 → 推列表，否则 `AccountInfo(count=0)` |
| 3 | `C2S_LOGIN_PLAYER_ADD` | `name(20) + shape(1) + color(2) + major(1)` | 建角 → `PlayerAdd(6)` + 角色列表 |
| 5 | `C2S_ACCOUNT_MANAGE` | `sub(1)` | `sub==16` 时补推角色列表，否则回 `op7` |
| 6 | `C2GS_PBZLIST` | — | 回 `BandWord(29)` 空表 |
| 2 | `C2S_LOGIN_PLAYER_ENTER` | `hero_id(4)` | **进世界**（见 3.2） |

### 3.2 进世界帧序（顺序即契约）

`_enter_world`固定发下列序列，少一帧或换序都会出现具体故障：

| 序 | 帧 | 为什么不能省 |
|---|---|---|
| 1 | `PropInit(16)` | 220B 定长属性块，`assert len(p)==220`；少一字节后面全错位 |
| 2 | `PropResist(18)` | 5 项抗性；不发则 `hero.CreateProp` 把末 8 项留 0 |
| 3 | `PropPower(19)` | `m_At/m_Df/m_Dp`；不发 → **所有 df>0 的招式被判「无法使用」** |
| 4 | `PropChange×N` | 覆盖写死的 1 级 / 0 经验 / 1000 金，否则重登回档 |
| 5 | `SkillLevel(65) sktype=2` + `sktype=7` | 前者「学会」，后者「进战斗招式菜单」；只发一个 = 学会但打不出 |
| 6 | `SceneM(96)` | 客户端会 `ClearAllObject()` |
| 7 | `AddNpc(99) ×N` | 承接上一帧的 Clear，否则地图上没有任何对象 |
| 8 | `GotoM(97)` | 落点 |
| 9 | `SummonInfo(48)` / `SummonSkill(49)` / `SetFight(50)` / `SummonKey(53)` | 建宠、技能格、出战、变异角标；资质 `op51` 由面板主动请求 |
| 10 | `ItemAdd(33) ×N` | 没有「整包背包」帧，只能逐件推 |

### 3.3 `PropInit` 的 220B

`build_prop_init`按 `hero.ITEM_ALL` 顺序逐项 `pack`，最后
`assert len(p) == 220`。这是全项目唯一带 `assert` 的组包函数——因为它一旦错位，
后面所有 `S2CPropChange(type)` 都会按错误下标 `setattr`，表现为「属性面板数字乱跳」。

关键下标（`hero.ITEM_ALL`）：

| idx | 字段 | 宽度 |
|---:|---|---|
| 8 | `m_Exp` | 4B |
| 9 | `m_NextExp` | 4B |
| 16 | `m_HP` | 2B |
| 18 | `m_MP` | 2B |
| 20 | `m_FP` | 2B |
| 32/33/34/35/36 | `m_Str/m_Mag/m_Cha/m_Dex/m_Con` | 2B |
| 37 | `m_Point` | 2B |
| 43 | `m_Gold` | 4B |
| 72/73/74 | `m_At/m_Df/m_Dp` | 1B |

---

## 4. 会话主循环与分派契约

### 4.1 `handle`

`M4Server.handle`：

```
_send Hello(1)  →  收 25B 裸包校验  →  while True:
    计算收包超时（战斗中按剩余倒计时，否则 300s）
    recv_c2s
    超时 → 宠物挂起则结算宠物，否则默认普攻
    心跳 op8 → 回 op250
    _dispatch(op, body)
```

三个隐含语义：

1. **单连接**：`_busy` 为真时第二个连接直接 `close`。单机一人一角色，
   `_conn / in_war / enemy_hp` 都是单例状态，被第二个客户端覆盖就是灾难。
2. **回合倒计时**：`await_cmd` 期间按 `cmd_deadline` 收包；超时行为对齐客户端
   `warui.DefaultAttack()`（默认普攻），而不是「什么都不做」。
3. **断线落档**：`ConnectionError` → `_persist()` → 清 `_conn` → break。

### 4.2 `_dispatch`：顺序即契约

`_dispatch`是一条**从上到下第一个命中即生效**的长 `if/elif`，
链尾 `elif self.in_war:` 是战斗保底（回一帧 `WarTurn` 推回合）。

> **插入位置比代码本身更重要**。客户端在战斗中**也会**发 OPENUI / COMMON / 用道具 /
> 上报点击统计，这些一旦落到保底，就会凭空多回一帧 `WarTurn`，把回合推给敌方
> （玩家表现为「我还没出手，敌方先动了」）。

因此有一张数据化的契约表：

```python

# ── 分派顺序契约：`_dispatch()` 里哪些 opcode 必须排在「战斗保底」之前 ────────
# 为什么单独列一张表：`_dispatch` 是一串顺序敏感的 if/elif，**插错位置不报错**，
# 只表现为「帧序错了」——最典型的是客户端在战斗中也发 OPENUI/COMMON/用道具，
# 这些一旦落到末尾的 `elif self.in_war:` 保底，就会凭空多回一帧 WarTurn，
# 把回合推给敌方。这张表把「谁必须在保底之前」写成数据，
# `tests/_test_dispatch.py` 会逐个在战斗态下跑一遍并断言「没有回 WarTurn」。
DISPATCH_PRE_WAR_OPS = (
```

`DISPATCH_PRE_WAR_OPS`现 **58 条**，覆盖：移动、养成三件套、
NPC 六类交互、任务查询/放弃、OPENUI/GROUP/COMMON、宠物管理、物品与详情面板、
两条时间通道、黑名单、生产、以及「无逻辑但必须接住」的忽略族。

### 4.3 已补齐的分派缺口（五批）

| 批次 | opcode | 缺口性质 |
|---|---|---|
| 一 | `100 C2S_NPC_TASK` / `101 C2S_NPC_GETDETAILS` | 对话任务链 / 商店悬停详情 |
| 二 | `49 C2S_GIVEUP_TASK` | **真功能缺口**：不处理则点「放弃」后任务栏不消失、击杀还暗涨进度 |
| 二 | `19/20/23/52/55/80/149` 显式忽略族 | 单机无对手方，但必须显式接住，否则战斗中落保底 |
| 三 | `144/145/156` 详情面板 | 背包悬停/右键看物品与宠物详情拿不到数据 |
| 四 | `104 C2S_NPC_GETCOST` | 制作/合成面板要费用，服务端不回 → 体力/金钱/技能等级三格空白 |
| 五 | `102 C2S_NPC_MAKE` | 费用有了但点「制作」无反应；补上「扣材料 → 入包 → 完成通知」 |

对应应答：`S2CTaskAdd(72)` / `S2CTaskRemove(73)` / `S2CNpcDetails(151)` /
`S2CNpcMakeCost(155)` / `S2CMakeCompleteNotify(160)` / `S2CItemInfo(162)` /
`S2CItemTCInfo(190)` / `S2CSummonInfo(48)`。

### 4.4 覆盖率两个口径，不能混用

| 工具 | 分母定义 | 结果（2026-10-19 实测） |
|---|---|---|
| `coverage_smoke.py` | 客户端 `net.py` 常量 **136** 条（C2S_* + C2GS_*） | 踩到 136 = 100%；报「未实现」13 个，其中 **11 个是探针误报**（见下） |
| `_c2s_coverage.py` | 「客户端真的会发」**134** 条（去重 opcode） | 已实现 **134 / 未实现 0（100%）**；A/B/C 三档全空。★ 2026-10-20 起挂进回归，扫描段有分支数守卫（分支体被搬走会红，见 §12.1.4） |

二者测的不是同一件事：前者是**运行时踩到的路径**，后者是 `_dispatch` 里**是否存在分支**。
分母与分子都不同，禁止直接比较，更不要拿其中一个去否定另一个。

#### 探针误报已修（2026-10-19）

旧版 `coverage_smoke` 一律发 5B 的 `PROBE_BODY`，而 `_dispatch` 的分支大多带
`len(body) >= N` 守卫。body 不够长 → 落到链尾 `（未识别 op=N）` → 被记成「未实现」。
当时 13 个「未实现」里 11 个是误报，且只有 7 个被交叉校验抓到，其余静默流入报告。

现在三道修复：

| 环节 | 旧 | 新 |
|---|---|---|
| 探针长度 | 恒 5B | **从 `_dispatch` 源码解析守卫**（`_dispatch_guard_lengths`），按需补长：op16→5B、op144/145→8B、op156→9B |
| 已知集合 | 纯手抄，漏项即静默 | 手抄基线 **∪** 源码解析结果（134 个），漏项会被解析补上 |
| 误报处理 | 只标注、不修 | **自动闭环**：非战斗态加长重试（32→512B），仍失败则进战斗、在挂起窗口内重发 |

修复后实测：

```
本次踩到  136 个 = 100.0%
服务端未实现（内核日志旁听，运行时证据）2 个：114 250
  交叉校验  已知已实现的 134 个里被误报为缺口的：无
```

剩下的 114/250 是**真情况不是缺口**：250 是服务端 PING 回显用的 S2C 编码、114 是 S2C
`WarTurn`，客户端根本不会把它们当 C2S 发——是探针拿 S2C 编码去当 C2S 试了。

解析器有三个坑，都踩过并写进注释：

1. **分支头可能折行**。`elif (op in (C2S_WAR_SPECIAL, …, C2S_WAR_CATCH)` 换行后接
   `or op == … and self.in_war:`，只取第一行会漏掉 op173。按括号配平拼成逻辑行。
2. **字面量 opcode 要认**。`op == 1`（登录/建角）不是标识符，首版正则漏了，只认出 4 个。
3. **n=0 也要记录**。`n==0` 表示「有分支、无长度守卫」，漏记会让该 opcode 既不进
   `KNOWN_HANDLED` 也不进闭环——op173 就是这么漏的。

> 复盘：这份报告里「服务端未实现」一栏**不能直接当缺口清单**，必须确认探针 body
> 满足了分支守卫。权威静态口径仍是 `_c2s_coverage.py`。

---
