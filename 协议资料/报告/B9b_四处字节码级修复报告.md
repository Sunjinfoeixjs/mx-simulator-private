# B9b：末 4 个文件按字节码逐处修复（语法有效性 484/484）

> 日期：2026-10-16　工具：`tools/_bctokens.py`（按函数转储 token/指令）、`_fixiter.py`（迭代定位）
> 结果：`src/*.py` lib2to3（Py2 文法）解析失败 **4 → 0**，全量 **484/484**

## 1. 修复清单（按字节码逐处）

| 文件 | 缺陷点 | 字节码依据 | 处置 |
|---|---|---|---|
| `lib/sre_compile.py` | 344 `data[0:0] = header` 丢缩进 | 前/后语句均在 4 空格层级 | 复位为 4 空格 |
| | 374-376 嵌套 if/else 被拆成两个 `else:`+`yield` | `_compile_info`：内层 else → off=251 YIELD_VALUE；外层 else → off=255-256 POP_TOP+YIELD_VALUE | 重建为 `else: break` + `else: break` |
| | 403 / 407 同型（第二个 BRANCH 分支） | 同上（该分支在 stock 6 中重复出现两次） | 同型重建 |
| `dialog/dlgriderefine.py` | 121 `lst[1:3] = [0, 0]` 丢缩进 | `if bUpdate:`(12) 的体应为 16 | 复位 16 空格 |
| | 125 `lst[4:] = [0, 0]` 丢缩进 | 内层 `if bUpdate:`(16) 的体应为 20 | 复位 20 空格 |
| | 137 / 141 同型（第二处 ride 分支） | 同上 | 同型复位 |
| `dialog/dlgtradecenter.py` | 129-130 `if ...:` 空体 + 悬空 `else:` | `OnInterfaceEnter`：条件真 → off=148 `JUMP_ABSOLUTE 221`（函数尾 `LOAD_CONST None/RETURN_VALUE`） | 补 `return`，`RefreshAddon` 提升到 if 同级 |
| | 125 `yield` | 匹配分支 → off=80 `YIELD_VALUE` + off=81 跳回循环头；与 `for/else` 的 `price = 0` 构成「找到即停」语义 | 改为 `break` |
| `lib/warnings.py` | 71 四段条件被截断粘连（`yield issubclass(...) and     else:`） | `warn_explicit` off=222-318 四段 AND：`msg is None or msg.match(text)` / `issubclass(category, cat)` / `mod is None or mod.match(module)` / `ln == 0 or lineno == ln`；失败全部跳 326，命中落 off=321-323 | 还原四段条件；命中语句改 `break` |
| | 159 `filters[:] = []` 丢缩进 | `resetwarnings` 函数体 | 复位 4 空格 |

## 2. 关键发现：该 VM 用 `YIELD_VALUE` 编码 `break`

修复过程中三处独立位置都出现「语义必须是 `break`、字节码却是 `YIELD_VALUE; JUMP_ABSOLUTE <循环头>``」。

**全量统计（决定性）**：

```
for 语句（缩进型） = 4275
break 语句        = 0        ← 484 个文件里一个都没有
continue 语句      = 285
裸 yield 语句      = 736（179 文件）
```

**交叉验证**：`lib/warnings.py` 与 `lib/sre_compile.py` 是随客户端打包的 CPython 2.6 标准库副本，
其上游源码在这两处的语句确定是 `break`，而本地字节码为 `YIELD_VALUE` —— 可判定：
**该客户端 VM 把循环提前退出（break）编译为 YIELD_VALUE，反编译器据此渲染为 `yield`**。

据此本次修复对**可确证**的 3 处（warnings 1 处、sre_compile 2 处、dlgtradecenter 1 处）按源码级 `break` 重建。

> 遗留口径问题（建议后续统一评估，不属本次范围）：全量 736 处裸 `yield` 中，
> 相当一部分可能实为源码级 `break`；另有部分是客户端脚本框架的真实 `yield`（协程式步进）。
> 二者字节码形态相同，需按上下文（是否 `for` 循环 + 是否紧跟循环头跳转 + 是否与 `for/else` 语义耦合）逐处判定。

## 3. 复验（全量 484）

`tools/_post_refresh_verify.py` → `logs/_post_refresh_verify.txt`

| 指标 | 修复前 | 修复后 |
|---|---|---|
| lib2to3 解析失败 | **4** | **0** |
| 裸 yield F1（第 0 列） | 1 | **0** |
| 裸 yield F2（粘连） | 5 | **0** |
| 颠倒赋值 | 0 | 0 |
| 空文件 / <3 行 | 0 / 0 | 0 / 0 |
| 总行数 / 总字节 | 330,675 / 21,941,947 | **330,684 / 21,942,297** |

清单已刷新：`data/manifest/uncompyle6_ok_b9.json`（484 模块 / 330,684 行 / 21,942,297 字节）

## 4. 复现

```
python38 tools/_bctokens.py <pyc> --func <名> --instr    # 按函数转储 token/指令
python38 _fixiter.py <py>                                # 迭代定位解析失败点
python38 _check4.py                                      # 四处专项校验
python38 tools/_post_refresh_verify.py                   # 全量 484 校验
```
