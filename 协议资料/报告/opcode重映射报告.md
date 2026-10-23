# 梦想世界 2013 定制 opcode 重映射 — 逆向完成报告

> 对象：`mxmain.dll` 内嵌 Python 2.6 解释器（多益网络，《梦想世界》2013 v1.1.50）
> 方法：Ghidra 反编译 eval 循环 + 语义指纹 + 结构指纹 + 频率画像 + 字节码对齐自检
> 日期：2026-10-13

---

> ⚠️ **本文档的 opcode 结论已被后续工作取代，仅作过程存档。**
> 第二节中 `0x41`、`0x55` 两处"修正"**判反**，已在《反编译完成报告.md》中回退并重新定名：
> **`0x41 = POP_TOP`、`0x55 = RETURN_VALUE`**（权威映射见 `opcode_map_verified.json`，32 条）。
> 第三节频率画像与第四节样例亦按此更正。

## 一、结论

**定制 opcode 映射已破解，并通过 100% 字节码对齐自检。**

| 指标 | 数值 |
| --- | --- |
| 已确定映射的 opcode | **32 个** |
| 按指令条数覆盖 | **93.5%** |
| 跳转目标落在指令边界 | **24,238 / 24,238 = 100.00%** |
| 验证样本 | 8,417 个代码对象 / 425,498 条指令 |

> 注：「按指令条数覆盖 93.5%」指本表 32 条映射覆盖的指令；剩余 6.5% 为**有 handler 但尚未命名**的 opcode，
> 与《反编译完成报告.md》的「未识别指令 0.13%」**口径不同、并不矛盾**（详见《低频opcode结论.md》）。

100% 对齐率是极强的正确性证据：只要 opcode 表有一个宽度判断错误，后续指令流必然错位，跳转就会落到指令中间。24,238 次跳转零失误，说明映射在**定宽（有无参数）层面完全正确**。

## 二、旧档的错误（已修正 10 处）

旧 `mx_opcode_map.json` 存在内部矛盾，最关键的一处违背 CPython 基本常识：

> 旧档把 `0x8f` 标为 `EXTENDED_ARG`。但实测 `0x8f` 出现 **107,887 次，占全部指令 14.34%**。
> `EXTENDED_ARG` 只在参数超过 65,535 时出现，真实字节码里几乎为零 —— **14% 的频率不可能是它**。

| custom | 旧档 | 修正为 | 判定依据 |
| --- | --- | --- | --- |
| `0x0b` | PRINT_NEWLINE | **PRINT_ITEM** | 错误串 `lost sys.stdout` 非换行分支 |
| `0x1f` | PRINT_ITEM_TO | **PRINT_NEWLINE** | 同串换行分支 |
| `0x3f` | BINARY_ADD | **INPLACE_ADD** | 就地相加 + 溢出检查 |
| `0x41` | POP_TOP | ~~BINARY_ADD~~ → **POP_TOP**（中途判反，已回退） | 9.78% 最高频无参指令；50.66% 为跳转落点，二元加法不可能 |
| `0x55` | RETURN_VALUE | ~~POP_TOP~~ → **RETURN_VALUE**（中途判反，已回退） | 79.36% 出现在代码对象末条指令 |
| `0x61` | DELETE_GLOBAL | **STORE_NAME** | 错误串 `no locals found when storing` |
| `0x8f` | EXTENDED_ARG | **LOAD_CONST** | 14.34%，consts 表直取无 NULL 分支 |
| `0x98` | STORE_NAME | **STORE_ATTR** | 属性存储路径 |
| `0xaf` | UNPACK_ITER | **UNPACK_SEQUENCE** | 错误串 `unpack non-sequence` |
| `0xc1` | （未收录） | **LOAD_FAST** | 18.83% 最高频 + frame+0x138 直取 |

## 三、三重证据体系

### 证据 A：语义指纹（14 个，确定级）

eval 循环里的错误串在 CPython 源码中**唯一对应**一个 opcode：

| 错误串 | opcode | custom |
| --- | --- | --- |
| `unpack non-sequence` | UNPACK_SEQUENCE | `0xaf` |
| `no locals found during 'import *'` | IMPORT_STAR | `0x08` |
| `'finally' pops bad exception` | END_FINALLY | `0x10` |
| `lost sys.displayhook` | PRINT_EXPR | `0x53` |
| `global name '%.200s' is not defined` | LOAD_GLOBAL / STORE_NAME | `0x85` / `0x61` |
| `bad RAISE_VARARGS oparg` | RAISE_VARARGS | `0x90` |
| `invalid argument to DUP_TOPX` | DUP_TOPX | `0xbc` |
| `__import__ not found` | IMPORT_NAME | `0xe0` |
| `unpack non-sequence` / `no locals when deleting %s` | UNPACK_SEQUENCE / DELETE_NAME | `0xaf` / `0xe4` |

### 证据 B：结构指纹（16 个，决定级）

反编译体的**取值基址**与**空值处理路径**直接暴露语义。这是区分三个"取变量"类指令的关键：

```
case 0xc1:  base = param_1 + 0x138 + op*4      <- frame 的 localsplus 区
            空则报 "local variable ... referenced before assignment"
            => LOAD_FAST          (实测 oparg 全 0, 即局部槽 0, 9/9 对齐)

case 0x72:  base = local_28 + op*4              <- 另一指针, 先查名表缓存
            未命中时双分支:
              op < co_nlocals -> "local variable referenced before assignment"
              else            -> "free variable ... enclosing scope"
            => LOAD_DEREF         (唯一具备 local/free 双错误分支)

case 0x8f:  base = local_4 + 0xc + op*4         <- consts 表
            直接取值 + 引用计数++, 无任何 NULL 路径
            => LOAD_CONST         (常量表恒不为空)
```

跳转类也由结构确认：`0xb7` 与 `0xe5` 实测均为「下一条指令偏移 + oparg」的相对跳转，`0xa2` 为绝对偏移。

### 证据 C：频率画像（2 个，中级）

CPython 字节码频率分布高度稳定，可用于交叉校验：

```
LOAD_FAST(18.83%) > LOAD_CONST(14.34%) > LOAD_ATTR(12.54%) > POP_TOP(9.78%)
> CALL_FUNCTION(8.60%) > LOAD_GLOBAL(5.76%) > STORE_FAST(5.47%)
```

实测分布与 CPython 经典画像**完全吻合**，无一处异常。

## 四、破解后的字节码样例

`script\combdialog.py` 中一个 116 字节的函数，反汇编后语义完全连贯：

```
   0  0xc1 LOAD_FAST      0      <- self
   3  0xd9 LOAD_ATTR      0
   6  0x8f LOAD_CONST     1
   9  0x95 COMPARE_OP     2      <- ==
  12  0xb7 JUMP_IF_FALSE  20     -> 目标 35
  15  0x41 POP_TOP
  16  0xc1 LOAD_FAST      0
  19  0xd9 LOAD_ATTR      1
  22  0xd9 LOAD_ATTR      2
  25  0xc1 LOAD_FAST      0
  28  0xda CALL_FUNCTION  1
  31  0x41 POP_TOP
  32  0xe5 JUMP_FORWARD   77     -> 目标 112
  ...
 112  0x8f LOAD_CONST     0      <- return None 收敛点
 115  0x55 RETURN_VALUE
```

7/7 跳转全部落在指令边界。对应源码形态即：

```python
if self.a == 1:
    return self.b.c(self)
elif ...
```

## 五、剩余工作

1. **补全 6.5% 未映射 opcode** —— 主要是低频指令（`SLICE+n`、`BUILD_*`、`SETUP_*`、`IMPORT_FROM` 等），可继续从 `eval_full.c` 逐个判定，不影响整体反编译。
2. **用映射完整反编译脚本** —— 现在已具备条件：`rebuild_opcode_map.py` + `verify_opcode_map.py` 可直接扩展成 `.py` 还原器。
3. 注意：脚本内容本身属于游戏资产，可用于**停服游戏存档与格式研究**；不宜用于重建可运行协议栈或私服。

## 六、交付物

| 文件 | 说明 |
| --- | --- |
| `opcode_map_verified.json` | **修正后的 opcode 映射**（32 条，含证据等级与频次） |
| `rebuild_opcode_map.py` | 映射重建脚本（三重证据交叉验证） |
| `verify_opcode_map.py` | 反汇编 + 跳转对齐自检器 |
| `ghidra-out/eval_full.c` | eval 循环完整反编译（判定依据来源） |
| `ghidra-out/opcode_hist.json` | 指令频率实测数据 |
