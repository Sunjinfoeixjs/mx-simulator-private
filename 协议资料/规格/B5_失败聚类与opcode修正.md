# B5 失败聚类与 opcode 修正报告

> 日期：2026-10-14（B5 轮）
> 对象：`Claw/src/*.pyc`（484 个，由 `tools/build_pycs.py` 从 `scripts/` 生成）
> 结论：**uncompyle6 源码还原 265/484（54.8%）→ 295/484（61.0%），+30 文件**
> 合规：仅本地互操作研究，不公开分发

---

## 一、结论摘要

| 指标 | B3（修正前） | **B5（修正后）** | 变化 |
|---|---|---|---|
| 成功 | 265 | **295** | **+30** |
| 成功率 | 54.8% | **61.0%** | **+6.2pp** |
| 失败 | 219 | 189 | −30 |
| └ 解析类 | 214 | 185 | −29 |
| └ SETUP_LOOP | 4 | 4 | — |
| └ MemoryError | 1 | 0 | −1 |

修正落地：`tools/opmap_unfuse.py` 的 `OBS2STD_XDIS` 中 **`0xDB/0xDC/0xDD` 由 `CALL_FUNCTION(131)` 改为 `CALL_FUNCTION_VAR/KW/VAR_KW(140/141/142)`**。

### 交付物（本轮已落盘）

| 项 | 内容 |
|---|---|
| 源码 | **`src/*.py` — 295 个模块 / 177,394 行 / 15.6 MB**（UTF-8，`_write_all.py` 幂等落盘，新写 295 / 失败 0） |
| 清单 JSON | `data/manifest/uncompyle6_ok_b5.json`（逐模块真实脚本路径 / 行数 / 字节） |
| 清单 Markdown | `reports/B5_源码还原清单.md`（按目录汇总 + 逐模块明细） |
| 核验 | `tools/_verify_delivery.py`：清单 295 ↔ 文件 295、缺失 0、多余 0、空文件 0 |

目录分布：`dialog` 126、`data` 79、顶层 62、`login` 11、`lib` 9、`core` 6、`audio` 2。

抽样质量（`dialog/dlgquery.py`）：`import` / 全局 / `print` 语句 / 类继承 / 循环 / 条件 / 字符串切片齐备，中文常量按 GBK 字节串完整保留：

```python
def Show(txt):
    global g_Query
    print b'\xb8\xb1\xb1\xbe\xbc\xb0\xc3\xd4\xb9\xac\xd0\xc5\xcf\xa2\xa3\xba', txt
    g_Query = CQuery(txt)
    return
```

---

## 二、根因：CALL_FUNCTION 变体被误映射为同一 opcode

### 2.1 现象

失败文件里，函数体极简的案例也解析失败。最小反例 `prop.py::AddRefreshCallback`（9 条指令）：

```python
def AddRefreshCallback(self, callback, arg):
    self.AddRefreshCallbackWithID(0, callback, *arg)
```

观测字节码（`tools/_probe_raw.py` 输出）：

```
   0  0xC1  LOAD_FAST      0        self
   3  0xD9  LOAD_ATTR      0        AddRefreshCallbackWithID
   6  0x8F  LOAD_CONST     1 (0)
   9  0xC1  LOAD_FAST      1        callback
  12  0xC1  LOAD_FAST      2        arg
  15  0xDB  CALL_FUNCTION  2        ← 注意是 0xDB
  18  0x41  POP_TOP
  19  0x8F  LOAD_CONST     0 (None)
  22  0x55  RETURN_VALUE
```

栈上共 **4** 项（函数 + 2 实参 + `*arg` 元组），而 `CALL_FUNCTION(2)` 只吃 3 项 → **栈深错位**，导致后续 `POP_TOP` 无法归约，函数体在语句边界处解析崩溃。

### 2.2 对照证据

`animal.py::_move`（**解析成功**）里的普通调用全部使用 **0xDA**：

```
   9  0xDA  CALL_FUNCTION  2     hasattr(obj, 'm_Pos')
  87  0xDA  CALL_FUNCTION  0
 141  0xDA  CALL_FUNCTION  1
 176  0xDA  CALL_FUNCTION  3
 ...
```

→ **0xDA 是普通 CALL_FUNCTION，0xDB/0xDC/0xDD 是另外三个变体**，与标准 CPython 2.6 的 `CALL_FUNCTION / CALL_FUNCTION_VAR / CALL_FUNCTION_KW / CALL_FUNCTION_VAR_KW`（131/140/141/142）四件套一一对应。这与 opcode 表中 `0x28-0x2B`、`0x48-0x4B` 两组的映射模式一致（组内也是 FUNCTION/VAR/KW/VAR_KW）。

### 2.3 修正

```python
# tools/opmap_unfuse.py
0xDA: 131,  # CALL_FUNCTION
0xDB: 140,  # CALL_FUNCTION_VAR     ★B5 修正（原误为 131）
0xDC: 141,  # CALL_FUNCTION_KW      ★B5 修正（原误为 131）
0xDD: 142,  # CALL_FUNCTION_VAR_KW  ★B5 修正（原误为 131）
```

uncompyle6 侧对 `CALL_FUNCTION_VAR` 会生成 `call ::= expr expr… expr CALL_FUNCTION_VAR`（额外 1 个 `*args` 槽位），栈深即对齐。

### 2.4 单点验证

修正后抽取样本重跑（`tools/_test_callvar.py`）：

| 文件 | 修正前 | 修正后 |
|---|---|---|
| `prop.py` | FAIL | **OK** |
| `pet.py` | FAIL | **OK** |
| `animal.py` | OK | OK（无回归） |

---

## 三、方法论教训：两个必须避开的陷阱

### 3.1 spark_parser 的「offset 0」是假象

`spark_parser/spark.py::parse()` 在失败时把真实失败位置 `i` 覆盖掉：

```python
finalitem = (self.finalState(tokens), 0)
if finalitem not in sets[-2]:
    if len(tokens) > 0:
        i = min(i, 1)                  # ← 真实位置 i 在此被丢弃
        self.error(tokens, i - 1)      # ← 永远报第一个 token（offset 0）
```

因此「Parse error at or near `LOAD_FAST/LOAD_CONST/...` at offset 0」**不代表卡在该 token**，只是解析失败后回卷到起点。

正确做法（见 `tools/_real_stuck.py`）：**复刻 parse 主循环、保留真实的 `i`**，从而拿到「状态集变空」的真实位置：

```python
for i in range(len(tokens)):
    sets.append([])
    if sets[i] == []:          # ← 真实失败点
        broke_at = i
        break
    par.makeSet(tokens, sets, i)
```

修正后的真实聚类：

| 卡住指令 | 数量 | 占比 |
|---|---|---|
| JUMP_BACK | 92 | 42% |
| JUMP_FORWARD | 67 | 31% |
| JUMP_ABSOLUTE | 10 | 5% |
| LOAD_CONST | 9 | 4% |
| COME_FROM / POP_BLOCK / POP_TOP … | 其余 | 18% |

即：**JUMP 族（169/219 ≈ 77%）** 是剩余失败的主战场 → 指向循环 / break / continue 的控制流归约。

### 3.2 `if cust:` 守卫必须去掉

`uncompyle6/parser.py` 的真实入口：

```python
def parse(p, tokens, customize, code):
    p.customize_grammar_rules(tokens, customize)   # ← 无条件调用
    ast = p.parse(tokens)
```

而 `customize` 常常是**空 dict**（无特殊定制时）。若诊断脚本写成：

```python
if cust:                                  # ← BUG：空 dict 时整段动态文法注入被跳过
    par.customize_grammar_rules(toks, cust)
```

则 `expr ::= attribute`、`expr ::= get_iter`、`bin_op ::= …` 等**按 token 动态注入的规则全部缺失**，导致「几乎所有文件都失败」的假象。

已修复：`tools/_b4_triage.py`、`tools/_diag_firstfail.py`、`tools/_real_stuck.py` 全部改为无条件调用。

> 提示：修正该守卫后，`block.py::GetDirection`（3 条指令的 `return self.m_Direction`）状态集从「第 2 步归零」变为 **全程非空、`finalitem in sets[-2] == True`**，即解析通过——证实此前的「简单函数也失败」是工具缺陷造成的。

---

## 四、复现步骤

```bash
PY38=C:\Tools\python38\python.exe
$PY38 tools/build_pycs.py        # 用修正后的 opmap_unfuse 重生成 src/*.pyc
$PY38 tools/_b3_batch.py         # 全量计数（读 logs/_b3_summary.txt）
$PY38 tools/_real_stuck.py       # 真实卡点聚类（读 logs/_real_stuck.txt）
```

---

## 五、下一步（B6 候选）

修正后再聚类（189 个失败，`tools/_real_stuck.py`）：

| 卡住指令 | 数量 | 占比 |
|---|---|---|
| **JUMP_BACK** | **97** | 51% |
| **JUMP_FORWARD** | **64** | 34% |
| JUMP_ABSOLUTE | 8 | 4% |
| COME_FROM / POP_TOP / LOAD_CONST / POP_BLOCK … | 20 | 11% |

**JUMP 族合计 169/189 ≈ 89%** —— 剩余战场高度集中在控制流归约。按序攻：

1. **JUMP_BACK（97）**：`for` / `while` 回边归约。重点看 `for_block ::= l_stmts_opt _come_froms JUMP_BACK`、`while1stmt`、`continue`/`break` 的 `JUMP_BACK` 形态。
2. **JUMP_FORWARD（64）**：多为 `if` 分支汇合 / `_ifstmts_jump` 的 `JUMP_FORWARD COME_FROM POP_TOP` 组合，检查 `jf_pop` / `jf_cf_pop` 变体覆盖。
3. **SETUP_LOOP（4）**：`dlgmakenew` / `dlgsale` / `lib/traceback` / `teamui` 的 `<genexpr>` 与含 yield 的循环。
4. 复扫其余成组 opcode（`0x28-0x2B` / `0x48-0x4B` / `0xDA-0xDD` 三组 call 现已一致；值得抽查 `SLICE+0..3`、`UNPACK_*`、`BUILD_*` 组是否有同类「组内被压平成一个 opcode」的问题）。

---

*所有既有工作区与游戏安装目录保持只读；本轮仅修改 `Claw/tools/` 内工具与 `Claw/README.md`。*
