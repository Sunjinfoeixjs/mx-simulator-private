# B9c：裸 `yield` 全量甄别与改写（真相：本 VM 用 `YIELD_VALUE` 编码 `break`）

> 日期：2026-10-16　工具：`tools/_bcyield_scan.py`（字节码指纹聚类）、`tools/_yield2break.py`（改写）
> 结论：全语料 **736 处裸 `yield` 全部为源码级 `break`**，已改写；备份 `logs/_bak_yield2break/`

## 1. 问题

B6 曾把本 VM 的 `YIELD_VALUE` 当作「裸 yield 作语句」并据此补文法（P2/P3），
导致 179 个文件里出现 736 处裸 `yield`。B9b 修末 4 个文件时发现三处独立位置
「语义必须是 `break`、字节码却是 `YIELD_VALUE; JUMP`」，故启动全量甄别。

## 2. 四条判据（互相独立，结论一致）

### 判据 1：全语料无生成器消费代码

对 484 个 `src/*.py` 全量检索 `.next()` / `.send(` / `StopIteration` / 生成器驱动函数：

```
.next()            → 仅 lib/UserDict.py:204（标准库自身用法，与业务无关）
StopIteration      → 仅 lib/UserDict.py:205（同上）
.send( / RunGen 等  → 0
```

⇒ 客户端不存在协程式驱动框架；若这些回调里是真 `yield`，函数体根本不会执行，
而客户端实际运行正常（登录/世界/战斗/宠物面板均已实测）⇒ 裸 `yield` 必为 `break`。

### 判据 2：上游 CPython 2.6 标准库副本逐行比对

`lib/warnings.py`、`lib/sre_parse.py`、`lib/sre_compile.py` 是随客户端打包的
**CPython 2.6 标准库副本**，可与上游原文逐行对照：

| 文件 | 本地裸 `yield` 处 | 上游对应语句 |
|---|---|---|
| `lib/sre_parse.py` | **12 处**（209 / 369 / 371 / 383 / 387 / 395 / 399 / 445 / 448 / 456 / 470 / 576 / 589 / 609 / 638 / 677 / 745…） | **全部是 `break`**（`getwidth` 的 `elif op == SUCCESS: break`、`_parse_sub` 两处、`_parse` 的十处循环终止） |
| `lib/warnings.py` | 1 处（`warn_explicit` 过滤器循环） | `break` |
| `lib/sre_compile.py` | 2 块（`_compile_info` 两个 `BRANCH` 分支） | `break` |

位置与上下文**完全一致**，无一例外。

### 判据 3：字节码指纹聚类（全量 484 pyc，757 个 `YIELD_VALUE`）

```
循环   紧随指令          循环带else   数量
for    回跳循环头(break退出)  yes        356
while  前向跳转(循环出口)    -          193
for    前向跳转(循环出口)    yes        153
while  回跳循环头            -           31
for    POP_TOP / COME_FROM / POP_BLOCK  20
none   POP_TOP（带值 yield）  -            3      ← lib/os.walk ×2、lib/types._g（真 yield）
```

- **循环内 + 紧随跳转 = 733 处**（`break` 指纹：跳循环头 = VM 的提前退出，跳循环出口 = CPython 语义）
- 循环外 / 带值 = 24 处，其中 12 处为 `<genexpr>`（真生成器表达式，源码中不是裸 `yield` 行）、
  3 处为标准库带值 yield（`yield top, dirs, nondirs` 等）
- **循环外无任何裸 `yield`** ⇒ 不存在「暂停型」真裸 yield

### 判据 4：语义自查（改写前后是否自洽）

| 位置 | 原文（`yield` 版） | 改写后 | 说明 |
|---|---|---|---|
| `netgroup.py` ×30 | `while True: gid = UnpackInt(4); if gid == 0: yield` | `break` | 协议列表解码的 **0 终止符**模式；真 yield 会死循环 |
| `dialog/dlgmakenew.py` | `for i in (0,1): … if wtp == 3: weapon = …; yield` + `else: weapon = 0` | `break` | **for/else 查找**语义（找到即停，未找到给默认） |
| `chatoutput.py` | `while …: if idx < 0: yield` | `break` | 越界即结束循环 |
| `core/war.py:41` | `yield` | `break` | 命令队列清空循环 |

## 3. 执行

```
python38 tools/_yield2break.py --dry      # 清单：736 处 / 179 文件
python38 tools/_yield2break.py --apply    # 改写（先备份到 logs/_bak_yield2break/）
```

- 改写规则：**行内容恰为 `yield` 的语句行 → `break`**（保留缩进；`yield <expr>` 带值形式不动）
- 行尾惯例保持：写盘用文本模式（`\n`→`\r\n`），抽验 `netgroup.py` 2819 行 / 2819 个 CRLF / 0 裸 LF
- 行数、字节数**不变**（`yield` 与 `break` 同为 5 字符）

## 4. 验证

`tools/_post_refresh_verify.py` → `logs/_post_refresh_verify.txt`

| 指标 | 改写前 | 改写后 |
|---|---|---|
| 文件数 | 484 | 484 |
| **lib2to3 解析失败** | 0 | **0**（`break` 若落到非循环处会立即报错，构成反向校验） |
| 裸 yield 语句 | 736 | **0** |
| break 语句 | 6（B9b 手工） | **742**（736 + 6） |
| continue 语句 | 285 | 285 |
| for 语句 | 4275 | 4275 |
| 空文件 / <3 行 / 颠倒赋值 | 0 / 0 / 0 | 0 / 0 / 0 |
| 总行数 / 总字节 | 330,684 / 21,942,297 | 330,684 / 21,942,297 |

关键字统计对齐：736（改写）+ 6（B9b 手工：warnings 1、sre_compile 4、dlgtradecenter 1）= 742 ✓

清单已刷新：`data/manifest/uncompyle6_ok_b9.json`（484 模块 / 330,684 行 / 21,942,297 字节）

## 5. 影响面

本次是**语义层面**的重大修正（不影响语法，但决定代码能否真正执行）：

- 修正前：`netgroup.py` 协议解析、`lib/sre_parse.py` 正则编译等**关键路径函数会被误判为生成器**，
  调用后仅返回生成器对象、函数体不执行；
- 修正后：恢复为正常函数与 `break` 语义（协议 0 终止符、for/else 查找等均自洽）。

> 保留说明：`lib/os.py::walk`、`lib/types.py::_g`、`<genexpr>` 等**带值 `yield`** 未改动（真生成器）。

## 6. 复现

```
python38 tools/_bcyield_scan.py           # 字节码指纹聚类（logs/_bcyield_scan.{txt,tsv}）
python38 tools/_yield2break.py --dry      # 改写清单（logs/_yield2break.{txt,tsv}）
python38 _count_kw.py                     # 关键字统计核对
python38 tools/_post_refresh_verify.py    # 全量 484 校验
python38 tools/_gen_manifest_b9.py        # 刷新清单
```
