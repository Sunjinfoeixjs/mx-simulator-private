# 可观测性：不改内核的旁听

> 本册是《模拟服全链路代码解析》的拆分分册：**tap.py / debugger.py 如何零侵入拦截、解码、可视化全部上下行字节**。
> 完整版（唯一现状依据）：[`../模拟服全链路代码解析.md`](../模拟服全链路代码解析.md)。
> 拆分只为聚焦阅读，内容与主文档逐字一致；**改内容请改主文档**，然后重跑
> `python 协议资料/报告/_split_docs.py` 重新生成本册。
>
> 文档双向体检：`python 协议资料/报告/_check_anchors.py`（已挂进 `tests/_run_all.py` 第 ④ 层）。

---
## 11. 可观测性：不改内核的旁听

### 11.1 三个锚点

| 组件 | 位置 | 作用 |
|---|---|---|
| `TappedConn` | `tap.py` | 鸭子类型的 conn 替身：只转发 `sendall/recv/settimeout/close`，其余 `__getattr__` 透传 |
| `FrameTap` | `tap.py` | 会话抓包器：分帧 → 解码 → 环形缓冲 → 订阅推送 → 世界/战斗模型 |
| `tap.install` | `tap.py` | 只替换 `M4Server.handle` 与 `M4Server.log` 两个方法，把 `conn` 换成 `TappedConn` |

内核有约 180 处裸 `sendall`，**一行都不用改**。重复调用 `install` 幂等。

```python

class TappedConn:
    ...
    def sendall(self, data):
        ...
```

`recv`不是简单转发：它按 0.1s 分片读底层 socket，片间回到顶部检查
**注入队列**（`drain_c2s_inject`）——否则裸阻塞 recv 无法被外部唤醒，注入的伪造帧
只能等客户端下次说话才生效。对外语义保持不变：给满 n 字节 / 抛 `socket.timeout` / EOF 返回 `b''`。

### 11.2 分帧与解码

| 环节 | 位置 | 说明 |
|---|---|---|
| 唯一分帧实现 | `split_frames` | 按方向切帧 |
| 有状态外壳 | `Reassembler` | 带失步自愈 `MAX_HOLD/MAX_SKIP` |
| 字段解码 | `decode_fields`/ `decode_best` | 覆盖表逐字段展开 |
| 静态规格 | `_OV_S2C` | 逐字段对齐 `build_*` |
| 语义提示 | `_OV_NOTE` | 循环/哨兵语义说明 |
| 内容相关布局 | `_OV_DYN` | op17/48/49/65/85/96/101/102/164 |
| 覆盖统计 | `stats` | 已实现/未实现/未出现/字典未登记 |

解码三层来源：

1. 本地 `OVERLAY` 精确布局；
2. `协议资料/字典/protocol_functions.json` 的 C2S 字段表（同 opcode 多形态逐种试配）；
3. 客户端 `net.py` 的 `NET_FUNCTION_NAME` 把 S2C opcode 桥接到模块函数。

布局对不上时**不隐藏**：多出的字节进 `tail`、越界写进 `note`，而不是给一个漂亮但错的解析。

> **2026-10-19 修**：上面这句原来有个漏洞。解码器越界时会把游标**强推到 payload 末尾**
> （为了如实展示已读部分），于是越界帧的 `consumed` 恰好等于总长、`tail` 为空——
> 而 `fit` 当时只看 `consumed == total`，把「吃到一半撞墙」报成了「完美吃满」。
> 唯一的证据是 `note`，但调用方（调试台标色、巡检判定）看的是 `fit`。
> 单字节翻转 fuzz 实测：940 次变异里 22 次落到这个自相矛盾状态
> （字符串缺 `\0`、去 desc 标签越界、长度前缀越界）。
> 现在 `fit = (consumed == total) and not note and not tail`，三者语义必须一致。
> 回归见 `tests/_test_tap.py` 的「字节翻转 fuzz」段落。

### 11.2.1 轮次标记：`_unknown_op` 的增量查询

`tap` 旁听内核日志里「未识别 op=N」时，最初只有一个**只增不减**的集合
`_unknown_op`。问题是：调用方（巡检探针）想问的是「**我刚发的那批**里有没有新的
未识别」，拿全量集是问不出来的——重试成功了旧标记也还在。

因此加了轮次维度：

```python
r = tap.mark_round()          # 开一轮，拿到递增的轮次号
... 发一批帧 ...
newly = tap.unknown_since(r)  # 只返回「恰好在这一轮首次出现」的 opcode
tap.forget_unknown(op)        # 已证伪的误报：从历史集与轮次表里双双撤销
```

语义要点（都有回归断言守着）：

| 行为 | 规则 | 为什么 |
|---|---|---|
| 首次记录 | `setdefault`，重复报错**不覆盖**首轮 | 否则「我这轮又发了一次还是失败」会把首次证据冲掉 |
| `clear()` | 清历史集与轮次表，但**不重置** `_round` | 轮次是「第几批帧」，缓冲清空不代表历史失效 |
| `unknown_since(r, cumulative=True)` | 返回 r 及之后各轮的并集 | 需要「从这里开始的整段」时用 |
| `stats()` | 多返回 `round` 与 `unknown_c2s_round` | 界面/报告可直接展示「哪个 op 在哪一轮露出来的」 |

> 在这之前，`coverage_smoke` 只能靠**日志文件的字节偏移**猜增量——偏移取早取晚都会漏
> （op173 的兜底发生在战斗轮次里、早于探测段，按偏移根本抓不到）。现在换成轮次查询，
> 不再依赖任何时序猜测。

### 11.2.2 循环构造：`L{}` 结构化展开（2026-10-20）

`decode_fields` 原先只有 `T`（吃剩余字节、只给 hex）。像 op147 的 `items`
这种「循环到哨兵为止」的列表，界面上只能看到一坨字节，看不出有几条、每条什么值。

新增 `L{}` 类型，两种控制方式：

```python
# 哨兵终止：循环直到 shape 字段 == 0
147: ('S2CNpcBuyList', 'mode:1 ticketid:4 ext:1 items:L{sentinel:shape:shape:4 num:2 price:4}')

# 计数终止：循环 n 次（n 来自前面已解出的字段）
'... entries:L{count:n:style:1 shape:2}'
```

解码结果从 `'20B'` 变成结构化列表：

```
op147 items   = [{'shape': 101, 'num': 5, 'price': 20}]
op148 items   = [{'shape': 101, 'amount': 5, 'price': 50}]
op149 entries = [{'skid': 1104, 'npcLimit': 0, 'validLearn': 255, 'freeLevel': 0}]
op105 entries = [{'style': 0, 'shape': 4005, 'desc': None, 'name': '梦想企鹅', 'title': ''}]
```

#### 展示层也做了适配（2026-10-20）

结构化之后如果界面还显示 `{'style': 0, ...}` 或摘要只有 `1, 1, 1`，等于白做。两处调整：

| 位置 | 改前 | 改后 |
|---|---|---|
| `disp`（详情面板逐字段） | `[1]{{'style': 0, 'shape': 4005, ...}}` | `[1] {style=0 shape=4005 name=梦想企鹅}` |
| `_summary`（列表行摘要） | `1, 1, 1`（只看 header 整数） | `梦想企鹅`（取条目里的字符串） |

> `_summary` 原先只认顶层 `S`/`V` 字段；`L` 的 value 是 list，被整段跳过。
> 现在会下钻到条目里找字符串（name/title），找不到才退回 `×N` 计数。
> 这条对 op105 尤其重要——它的 header 只有 `owner_id/flag/n` 三个数，摘要必然是 `1, 1, 1`。

> **op105 一度被跳过**，理由是「循环体含 D/V/P，依赖同条内字段值」。
> 实测这个判断是错的：`V` 自带 1B 长度前缀、`D` 自带标签联合，都是**自包含**结构，
> 不需要跨字段引用；每次循环迭代 `decode_fields` 拿到独立 `seen`，互不污染。
> 教训：**「以为不支持」和「实际不支持」要分开验证**——先跑一次再下结论。

#### 三个实现要点（都是踩出来的）

1. **`L{}` 内含空格，不能被 `split()` 拆开**。`parse_spec` 先用正则
   `(\w+:L\{[^}]*\})` 把它整体提取成单个 token，再处理其余字段。
   且必须在 `rsplit(':')` **之前**特判——`L{}` 内部有冒号，`rsplit` 会把
   `items:L{sentinel:shape:shape:4 ...}` 拆成 `items:L{sentinel` 和 `shape:4}`。

2. **哨兵不是完整循环体**。`build_npc_buylist` 末尾只加 `shape=0`（4B），
   不是完整的 `shape:4 num:2 price:4`（10B）。若先解完整循环体再判断，
   剩余字节不够 → 误报「未吃满」。所以哨兵检测必须在解循环体**之前**，
   只 peek 哨兵字段的宽度。

3. **循环体解完有 tail 是正常的**。剩余字节可能就是哨兵或下一条，
   不能因为 `sub_tail` 非空就 break。

> 审计器注意：`_audit_build_overlay.py` 的 `_LIST_SHAPES` 用 `(0,0,0)` 作哑参，
> 会产生 `shape=0` 的条目 —— 对 `L{}` 来说那**直接就是哨兵**，循环瞬间结束、
> 剩余字节全成 tail。所以 147/148/149 在 `MANUAL_DUMMY` 里用非零 shape。

### 11.2.3 丢帧计数：让「看到的不是全部」显式化

调试台有两处会**静默丢帧**，此前都不留痕：

| 位置 | 触发 | 后果 |
|---|---|---|
| `tap.FrameTap._push` | 环形缓冲超过 `ring_max`（6000） | 最老的帧被挤掉（长期历史） |
| `debugger.Broadcaster._on_frame` | 批推暂存超过 2000 条 | 两次 SSE 推送之间的积压被丢（瞬时） |

不计数的话，界面看着正常，实际漏了帧没人知道。现在两者独立计数：

```python
_evicted      # 环形缓冲被动淘汰（tap.stats()['evicted']）
_sse_dropped  # 批推暂存被动淘汰（随 frames / stats 事件推送）
```

> **别和 `_dropped` 混**：那是调试台**主动**丢帧（`drop_next` 指令，用于测客户端
> 缺帧容错），语义完全不同。回归 `tests/_test_drop_count.py` 专门断言三者互不覆盖。

UI 上表现为 header 的一个徽标，**只在丢帧时出现**：

```
丢帧 7        ← hover：「环形淘汰 7 / 批推溢出 42 —— 界面上的帧不是全部历史」
```

清空会话时归零（与服务端 `clear()` 同步），代数变化（别的标签清空）也归零。

### 11.3 调试台

`debugger.py` 在 `singleplayer` 的 `on_ready` 挂载点接线：

```python

tap = T.FrameTap()
installed = tap.install(m)
```

必须早于客户端接入，否则第一条连接抓不到。HTTP + SSE 提供五个页签：

| 页签 | 数据来源 |
|---|---|
| 帧流 | 实时上下行帧，点开看逐字段 + hex；字典无编码/未实现/布局不符标红橙黄 |
| 地图 | `SceneM` / `AddNpc` / `MapTrack` / `GotoM` 翻译成地图 |
| 战斗 | `War*` 翻译成单位卡 + 语义化动作时间线；`WarWState` 位域原样摊开 |
| 协议覆盖 | 本会话出现过的编码 vs 全表 |
| 状态·GM | 实时状态 + GM 热操作 + 帧注入 + 丢帧（进程内直调） |

**真实链路标红**（§7.5.1）：GM 开战/胜利发出的整段帧在帧流里左缘描红 + 「真实链路·GM 开战 #59-73（15 帧）」标签；
顶部「只看链路」可单独看这几段。用的是事后按 seq 区间回填（`tap.mark_range`），接口是 `/api/marks`。

其他能力：`inject`、`drop_next`、
`export_jsonl`、心跳 2s（战斗自动提到 0.3s）、帧流 0.12s 批推。

### 11.4 GM HTTP 面板

`_GMHandler`只绑 `127.0.0.1`（旧实现 `0.0.0.0` 会暴露到局域网）。

页面（`gm_dashboard.html`）分卡片：角色 / 宠物 / 背包 / **特性状态** / 遇怪设置。「特性状态」把 `FEATURE_STATUS` 直接画出来，缺口排最前（见 §15.1.1）。

接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | `gm_dashboard.html` |
| GET | `/api/state` | `gm_state_dict()` |
| GET | `/api/features` | `FEATURE_STATUS` 全表 + 成品行 `rows` + 图例（面板「特性状态」卡片的数据源，见 §15.1.1） |
| POST | `/api/hp` `/api/resource` `/api/coins` | 改血量/资源/金钱 |
| POST | `/api/item/add` `/api/item/del` | 加/删道具 |
| POST | `/api/pos` `/api/map` | 改坐标/传送 |
| POST | `/api/war/start` `/api/war/win` | 起战斗/直接胜利（走真实链路，见 §7.5.1） |
| POST | `/api/pet` `/api/encounter` `/api/heal` `/api/refresh` `/api/save` | 宠物/遇怪/治疗/刷新/落档 |

调试台（`debugger.py`，非 GM 面板）另有一套等价入口，**共用同一批 `gm_*` 方法**：

| 入口 | 路径 | 链路标红 |
|---|---|---|
| 状态/GM 页签按钮 | `POST /api/action {action:'war_start'\|'war_win'}` | 回包带 `chain:{label,lo,hi,frames}`，前端据此重绘 |
| 控制台 | `POST /api/gm/cmd {line:'war start'}` | 同上（`war end` 等同 `war win`） |
| 段落实时查 | `GET /api/marks` → `{marks:[…], kinds:{war_start:N,war_win:N}}` | 开机拉一次 + SSE 心跳推 |

三个入口共用一套 `_run_action` / `_console_chain_kind` 判定，不各写一份——
漏标一个入口就等于「帧流里有一半链路没标」，而界面不会报错，
那是最坏的那种失败。

### 11.5 客户端「不回也没事」的误解

客户端 `net.OnServerCmd`遇到未注册 opcode 会打印
`OnServerCmd: 0xNN` 与 `!!!! func==None, OnServerCmd: 0xNN` 两行，然后 `return`——
不崩、不弹框，只是这一帧没人处理。

所以「客户端无路由的 S2C」不是故障，准确含义是「服务端在自嗨」。
实例：`op 250` 是客户端定义了却从不引用的死常量，服务端拿它当 PING 回显，
能验证链路字节到达，但别指望客户端有反应。

### 11.6 业务事件总线：把「为什么」和「发了什么帧」分开（2026-10-22）

抓包能回答「发了哪些帧」，回答不了「为什么发」。玩家问「我的钱怎么少了 100」，
帧流里只有一个 `PropChange(金币)`，看不出是买药、修装备还是任务扣款。

`bus.py` 补的就是这一层：内核在**业务判定点**发一条事件，与帧流并行但**不驱动任何逻辑**。

```
内核业务点 → self._emit('econ.coins', delta=-100, after=900, reason='NPC 购买 药 ×1')
           → bus.emit(kind, msg=…, **fields)
                 ├ 环形缓冲（2000 条，满了覆盖最旧）
                 ├ 分类计数（6 类：quest / econ / item / war / craft / world）
                 └ 订阅者回调（**在锁外**调用，异常逐条吞掉）
           → debugger /api/bus?since=N → 「任务链」「经济」页签
```

| 设计点 | 做法 | 为什么 |
|---|---|---|
| 只观测不驱动 | 事件发出后没有任何代码依赖它的返回值 | 观测设施坏掉不能让玩法跟着坏 |
| 异常隔离 | 订阅者异常逐条 `try/except` 吞掉，记进 `unregistered` 桶 | 一个页签写崩不该影响其他订阅者 |
| 订阅回调在锁外 | 先拷快照、出锁、再回调 | 回调里若再 `emit`，持锁调用会自死锁 |
| 环形缓冲 | 定长 2000，满了覆盖最旧 | 调试设施不许吃内存涨到 OOM |
| 分类注册表 | `KIND_CAT` 前缀→类别，未登记的进 `unregistered` | 新增事件忘了登记时**看得见**，不是静默归零 |

埋点入口本身还包了一层 `try/except`（`_emit`）：总线已经吞掉订阅者异常，
但**埋点参数**可能取到意外类型（`item name` 是 `bytes`、`qty` 是 `None`）。
事件是纯观测设施，它出任何问题都不能影响玩法——这里吞掉，日志留不下就算了，
绝不能让客户端掉线。

**金额类事件统一走 `_emit_econ(kind, delta, after, reason)`**：经济页签的流水就靠
`delta/after/reason` 三元组，散着发会漏字段，页签上就会显示「变化 undefined」。

> 回归 `tests/_test_bus.py` 四段：A 总线语义（环形覆盖/分类/订阅锁外/异常隔离）、
> B 埋点覆盖（买卖、战斗、任务、制作各发一条）、C 帧批合并、D **真 socket 字节一致**
> （开合批与不开合批逐字节相同）。第 D 段是这条优化「零风险」的实证。

---
