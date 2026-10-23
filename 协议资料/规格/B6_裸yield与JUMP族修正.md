# B6 裸 yield 修正报告（JUMP 族主因）

> 日期：2026-10-15（B6 轮）
> 对象：`Claw/src/*.pyc`（484 个）
> 前置：B5 修正 `0xDB/0xDC/0xDD → CALL_FUNCTION_VAR/KW/VAR_KW` 后为 **295/484（61.0%）**
> 本轮结论：**裸 `yield` 是 JUMP 族（占剩余失败 89%）的压倒性主因**
> 合规：仅本地互操作研究，不公开分发

---

## 一、结论摘要

| 指标 | B3 | B5 | **B6** |
|---|---|---|---|
| 成功 | 265 | 295 | **465** |
| 成功率 | 54.8% | 61.0% | **96.1%** |
| 失败 | 219 | 189 | **19** |
| 相对 B5 | — | — | **+170 文件 / +35.1pp** |

全量复测（`tools/_count.py`）最终读数：

```
pyc 总数: 484  成功: 465  失败: 19  成功率: 96.1%
```

落地：`tools/u6_patch26.py` 新增 **P2 + P3** 两条补丁（详见下）。

---

## 二、根因：裸 `yield` 的两处非标准 codegen

### 2.1 最小复现

`friend.py::GetNewFriendGroup`（24 条指令，B5 轮最小 JUMP_BACK 失败案例）：

```python
def GetNewFriendGroup():
    result = 0
    for i in (11, 12):
        if g_FriendsGroup.get(i):
            result = i
            yield            # ← 裸 yield
    return result
```

观测字节码：

```
 16  STORE_FAST  i
 19  LOAD_GLOBAL g_FriendsGroup
 22  LOAD_ATTR   get
 25  LOAD_FAST   i
 28  CALL_FUNCTION_1
 31  JUMP_IF_TRUE  (to 45)
 34  POP_TOP
 35  LOAD_FAST   i
 38  STORE_FAST  result
 41  YIELD_VALUE                 ← 无前置 LOAD_CONST None
 42  JUMP_BACK   (to 13)         ← 解析在此崩溃
 45  POP_TOP
 46  JUMP_BACK   (to 13)
 49  POP_BLOCK
```

### 2.2 两处与标准 CPython 2.6 的差异

| 位置 | 标准 CPython 2.6 | 本 VM |
|---|---|---|
| 裸 `yield` 表达式 | `LOAD_CONST None; YIELD_VALUE` | **仅 `YIELD_VALUE`**（None 被优化掉） |
| `yield` 作语句 | 再补一个 `POP_TOP` | **无 `POP_TOP`** |

而 uncompyle6 的 2.6 语法只有：

```
yield ::= expr YIELD_VALUE          ← 要求一个 expr
```

且 `n_yield` 语义动作只特判 `SyntaxTree("yield", [NONE, Token("YIELD_VALUE")])`：

```python
def n_yield(self, node):
    if node != SyntaxTree("yield", [NONE, Token("YIELD_VALUE")]):
        self.template_engine(("yield %c", 0), node)
    elif self.version <= (2, 4):
        self.write("yield None")
    else:
        self.write("yield")          # ← 裸 yield 分支永远走不到
    self.prune()
```

**结果**：`YIELD_VALUE` 无法被 shift，其后的 `JUMP_BACK`（循环回边）随之无法归约 → 状态集归零 → 整个模块解析失败。

### 2.3 证据链（Earley 状态集插桩）

`tools/_trace_yield.py` 逐步复刻 parse 主循环（保留 spark_parser 覆盖掉的真实失败位置 `i`）：

```
*** sets[17] 空 -> 死亡（前一个 token [16] = 42 JUMP_BACK 13 'to 13'）
--- sets[16] 内容（共 8 项）---
   (state=75,parent=15) T=[] complete=['yield']
   (state=83,parent=15) T=[] complete=['expr']
   (state=5,parent=15)  T=[...]  ← 无 JUMP_BACK
   (state=6,parent=16)  T=[...]  ← 无 JUMP_BACK
   ...
没有任何状态的 T 含 JUMP_BACK
```

补入 `yield ::= YIELD_VALUE` 后：卡点从 token 15 **前移到 token 16**（证明 P2 生效）；
再补 `stmt ::= yield` 后：**sets 全程非空，解析走通**。

---

## 三、补丁内容（`tools/u6_patch26.py`）

```python
# [P2] 裸 yield 表达式
_BARE_YIELD_RULES = """
yield ::= YIELD_VALUE
stmt  ::= yield
"""

# 语义动作覆盖：处理单 token 节点
def _n_yield_bare(self, node):
    if len(node) == 1 and str(node[0]) == "YIELD_VALUE":
        self.write("yield")
        self.prune()
        return
    _orig_n_yield(self, node)
```

三条一起生效：`yield ::= YIELD_VALUE`（表达式）+ `stmt ::= yield`（语句）+ `n_yield` 覆盖（语义动作）。

---

## 四、验证

9 个 B5 轮失败样本（含最小案例与各目录代表）全部转 OK：

| 文件 | 结果 |
|---|---|
| `friend.pyc` | OK（8352 B） |
| `misc.pyc` | OK（8472 B） |
| `dialog/dlgservice.pyc` | OK（13530 B） |
| `dialog/dlgtitle.pyc` | OK（18247 B） |
| `pet.pyc` | OK（18129 B） |
| `card_magic.pyc` | OK（21706 B） |
| `core/war.pyc` | OK（25921 B） |
| `popmenu.pyc` | OK（26696 B） |
| `hero.pyc` | OK（28150 B） |

全量复测（`tools/_count.py`）中间读数：

```
进度 261/484  成功=256  失败=5   成功率=98.1%
进度 281/484  成功=273  失败=8   成功率=97.2%
```

---

## 四·补 追加修正：0x5E 是「带值 yield」，不是 BREAK_LOOP

裸 yield 修完后仍有 19 个失败。对其中最小的 `lib/types.py::_g`（5 条指令）做观测字节码核对：

源码 `def _g(): yield 1`，观测字节码：

```
   0  0x8F  LOAD_CONST  1
   3  0x5E  ?            ← 当前映射为 BREAK_LOOP(80)
   4  0x41  POP_TOP
   5  0x8F  LOAD_CONST  None
   8  0x55  RETURN_VALUE
```

标准 CPython 2.6 的 `yield 1` 形态为 `LOAD_CONST 1; YIELD_VALUE; POP_TOP; LOAD_CONST None; RETURN_VALUE` —— **逐条对应**，故 **0x5E = YIELD_VALUE(86)**。

对照本 VM 另有**无值 yield 用 0x47**（`friend.py::GetNewFriendGroup` 实测），说明 VM 层把 yield 拆成两个码（是否弹栈取值），语义同为 `YIELD_VALUE`。

修正：`0x5E: 80 → 86`。带值 yield 走标准规则 `yield ::= expr YIELD_VALUE`（无需额外补丁）。

效果（对 19 个失败重建 + 反编译）：

| 文件 | 结果 |
|---|---|
| `lib/types.pyc` | OK |
| `lib/UserDict.pyc` | OK |
| `lib/os.pyc` | OK |
| `lib/traceback.pyc` | OK |
| `dialog/dlgmakenew.pyc` | OK |
| `dialog/dlgsale.pyc` | OK |

**OK=6/19** → 465 + 6 = **471/484 ≈ 97.3%**。

---

## 五、方法回顾（本轮有效的三件工具）

| 工具 | 作用 |
|---|---|
| `tools/_real_stuck.py` | 复刻 parse 主循环，拿到**真实**卡住指令（绕开 spark_parser 的 offset-0 假象） |
| `tools/_smallest.py` | 从聚类结果提取按 token 数排序的最小失败案例，快速定位 |
| `tools/_trace_yield.py` | 逐步 dump Earley 状态集（T / complete / items），确认规则是否可达 |

关键判据：**「补一条规则后卡点是否前移」**——前移说明该规则生效但仍缺后续规则（如 P2 生效后卡点 15→16）；原地不动说明规则不可达或方向错误。

---

## 六、下一步

1. 全量复测收尾，统计最终成功率与剩余失败清单。
2. 对剩余失败重跑 `_real_stuck.py` 聚类。
3. 若仍有 `JUMP_BACK/JUMP_FORWARD`：优先查 `iflaststmtl` / `for_block` 变体（本轮已确认 `l_stmts ::= lastl_stmt ::= iflaststmtl` 可达，但 `for_block ::= l_stmts_opt _come_froms POP_TOP JUMP_BACK` 的 POP_TOP 位可能与 `jb_cf_pop` 冲突，待剩余样本验证）。
4. 收尾后跑 `tools/_write_all.py` + `tools/_verify_delivery.py` + `tools/_gen_manifest.py` 刷新交付物。

---

*所有既有工作区与游戏安装目录保持只读；本轮仅修改 `Claw/tools/` 内工具与文档。*
