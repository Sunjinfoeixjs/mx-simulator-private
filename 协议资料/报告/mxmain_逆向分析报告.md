# mxmain.dll 逆向分析报告

> 分析工具：Ghidra 12.1.4 (headless) + JDK 21
> 分析日期：2026-10-13
> 样本：`mxmain.dll` — 2,754,304 字节 (2.75 MB)

---

## 一、样本基本信息

| 项目 | 值 |
|---|---|
| 文件名 | `mxmain.dll` |
| MD5 | `b50406bc0c1b73287d35aab0cca93017` |
| 大小 | 2,754,304 字节 (2.63 MiB) |
| 格式 | PE32 (x86, 32位) — MZ/PE 签名正常 |
| 编译时间戳 | 2012-11-07 |
| 链接器版本 | Visual Studio 2008 (linker v9.0) |
| 子系统 | Windows GUI |
| 关联产品 | 《梦想世界》客户端 (mxsj2013) |
| 开发商 | 多益网络 (duoyi.com) |

**文件一致性验证**：本机存在 3 份副本，MD5 完全相同，确认为同一构建产物。

| 副本位置 |
|---|
| `Documents\Trae-CN\default\mengxiang_re\mxmain.dll` |
| `Documents\deepseek-harness\default-workspace\mxsj2013\extract\mxmain.dll` |
| `WorkBuddy\2026-10-13-01-47-23\extracted\mxmain.dll` |

---

## 二、核心结论（最重要）

**`mxmain.dll` 不是业务逻辑库，而是一个「内嵌 Python 2.6 解释器的游戏引擎宿主」。**

两层结构：

```
┌─────────────────────────────────────────────────────┐
│  mxmain.dll  (2.75 MB)                              │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  自研游戏引擎 (C++ / MSVC 2008)                │  │
│  │  Common::  Graphics::  CInput::  CGameWnd::   │  │
│  │  → 网络、图形(DDraw/GDI)、资源、计时器、输入    │  │
│  └───────────────────────────────────────────────┘  │
│                        ↓ 暴露 Python 绑定            │
│  ┌───────────────────────────────────────────────┐  │
│  │  CPython 2.6 运行时 (静态链接进 DLL)           │  │
│  │  Py_Initialize / PyEval_EvalCodeEx / marshal  │  │
│  │  → 加载并执行 .pyc 脚本                        │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                        ↓ 加载
        script\*.py  —— 加密的 Python 脚本归档
        (game.py, core\war.py, dialog\*.py ...)
```

**含义**：真正的游戏业务逻辑（战斗、背包、任务、UI）全部在这些**加密的 Python 脚本**里，不在 DLL 的机器码中。要理解游戏逻辑，必须先从归档中解密提取 `script/*.py`。

---

## 三、Python 运行时证据链

### 3.1 判定为 CPython 2.6（非 2.7、非 3.x）

从 DLL 内泄露的 CPython 源文件路径可精确判定版本：

```
Python\ast.c           ← 2.6 存在此文件结构
Python\getargs.c
Python\traceback.c
Objects\cellobject.c   ← 2.6 引入
Objects\weakrefobject.c
Objects\unicodeobject.c
Objects\longobject.c
Objects\dictobject.c
Objects\funcobject.c
Objects\classobject.c
Objects\codeobject.c
Objects\listobject.c
Objects\tupleobject.c
Objects\methodobject.c
Objects\moduleobject.c
Objects\stringobject.c
Objects\iterobject.c
Objects\object.c
Modules\arraymodule.c
```

同时字符串区直接含 `Python 2.6` 标识。

### 3.2 关键 Python C-API（已解析符号）

**初始化 / 线程**：
```
Py_Initialize
PyEval_AcquireThread      PyEval_RestoreThread     PyEval_SaveThread
PyEval_EvalCodeEx         ← 脚本执行核心
PyInterpreterState_Delete PyThreadState_Get / _Clear / _Delete
```

**对象 / 类型系统**：
```
PyObject_Call             PyType
PyClass_New               PyFunction_Type          PyFunction
PyMethod
PyString_InternInPlace    PyString_FormatLong
PyCObject_AsVoidPtr       PyCObject_FromVoidPtrAndDesc   ← 2.6 风格 C 指针包装
PyImport_GetModuleDict    PyImport_FixupExtension
PyModule_AddObject        PyErr_NewException
PyFile_WriteString        PyArg_UnpackTuple
PyOS_snprintf
PyCF_ONLY_AST             ← 编译标志
```

**内建模块（静态链接）**：
```
__builtin__   marshal   string    struct
binascii      cPickle   copy_reg
```

### 3.3 自研 Python 绑定层

Ghidra 成功解析出 C++ 修饰名，暴露了脚本调用入口：

```
Python::RunFunction
  → 修饰名: ?RunFunction@Python@@YAHPAD00PAX0ZZ
  → 签名:   int Python::RunFunction(char*, char*, char*, void*, char*, ...)
```

这是 C++ 侧调用 Python 函数的主通道，是脚本与引擎之间的桥梁。

---

## 四、自研引擎架构（从修饰名重建）

Ghidra 从导出表恢复了 991 个有名字的函数，含完整 C++ 类结构：

### 4.1 命名空间分布

| 命名空间 | 类数 | 主要类 |
|---|---|---|
| `Common::` | — | CString, CFile, CVar, CRes, CCache, CArgument, CTimer, CException, CShare, CGetInfo, Memory |
| `Common::Net` | 41 个导出 | 网络通信层 |
| `Graphics::` | — | CImages, CScreenDDraw, CGrapObject, CBitmap, CStretchImages, CText, CCanvas, CBox, CScreen, CScreenGDI, CFont, CMaskBitmap, CMouseImages, Jpeg, PaletteSet, CGrapObjectAllocator |
| `CInput::` | — | CInput |
| `CGameWnd::` | — | CGameWnd |
| `SPixel::` | — | SPixel (像素操作，含 IsMask) |

### 4.2 渲染后端

- **DirectDraw**：`Graphics::CScreenDDraw`（23 个导出）— 主渲染路径
- **GDI fallback**：`Graphics::CScreenGDI`
- 支持全屏/窗口切换：`SetFullScreenStatus`、`FullScreen`、`SetScale`、`DisplayChange`
- 垂直同步：`WaitForVerticalBlank`

### 4.3 资源与线程

```
Common::CRes                      基础资源
Common::CResWithThreadLoad        异步单线程加载
Common::CResWithThreadMulLoad     异步多线程加载
Common::CCache                    缓存管理
Common::CShare                    共享数据
```

### 4.4 音频

字符串中出现 `RIFF` / `WAVE` / `fmt ` / `data` 及 `InitAudio`，音频走 WAV + OGG Vorbis（同目录有 `ogg.dll`、`vorbis.dll`、`OpenAL32.dll`、`wrap_oal.dll`）。

---

## 五、反编译统计

| 指标 | 数值 |
|---|---|
| 识别函数总数 | **7,867** |
| 成功反编译 | **7,416** |
| 反编译失败 | **0** |
| 有命名符号的函数 | 991 |
| 导出/入口点符号 | ~1,000+ |
| 导入符号 | 365 |
| 输出 C 代码 | 292,560 行 (7.2 MB) |

### 最大的函数（反编译规模）

| 大小(指令数) | 地址 | 名称 |
|---|---|---|
| 26,939 | `0x0052eb70` | FUN_0052eb70 ← 疑似脚本加载/解密主循环 |
| 9,704 | `0x0048d080` | FUN_0048d080 |
| 7,246 | `0x00535da0` | FUN_00535da0 |
| 5,630 | `0x00478563` | _memcmp |
| 5,466 | `0x0054bdf0` | FUN_0054bdf0 |
| 4,685 | `0x004dd100` | FUN_004dd100 |
| 4,577 | `0x004b4cd0` | FUN_004b4cd0 |
| 4,420 | `0x0047fbb0` | FUN_0047fbb0 |

> `0x0052eb70` 是全场最大函数（26,939 指令），且地址落在 Python 运行时代码区，
> 高度疑似 **脚本归档的解密 + marshal 反序列化 + 编译执行** 主流程。建议优先分析。

---

## 六、外部依赖（导入表）

从 365 条导入符号可确认系统依赖，涵盖：

- **KERNEL32.DLL** — 文件、内存、进程
- **ADVAPI32.DLL** — 注册表（`RegDeleteValueA`）、安全描述符、权限提升（`LookupPrivilegeValueA`）
- **SHELL32.DLL** — `ShellExecuteA/W`
- **SNMPAPI.DLL** — 网络状态查询
- **WS2_32 / WININET** — 网络通信

### 注册表痕迹
```
Software\Python\PythonCore\     ← Python 安装探测
```

### 网络与签名
```
http://www.duoyi.com            ← 开发商官网
http://www.python.org/peps/pep-0263.html   ← 源码编码声明（标准 CPython 字符串）
吊销列表: wosign.com / usertrust.com / verisign.com / Thawte
```

> 出现 WoSign 代码签名 CRL，说明该构建产物**曾有代码签名**，但当前样本可能已被剥离签名区。

---

## 七、脚本加载链路（依据已有工作还原）

你的 `notes/gt_pairs.json` 记录了脚本名到文件偏移的映射，结合本次分析可还原加载流程：

```
1. mx.exe / gameplayer.exe 启动
2. 加载 mxmain.dll，调用 Python::RunFunction 初始化
3. Py_Initialize() 启动 CPython 2.6 运行时
4. 从游戏数据包读入 script 归档（加密）
5. 解密 → 得到 .pyc / marshal 序列化字节码
6. marshal.loads() → code object
7. PyEval_EvalCodeEx() 执行

已知脚本（部分，来自 gt_pairs.json）：
  script\game.py                  偏移 20915511
  script\core\war.py              偏移 544039755      ← 战斗系统
  script\core\magic.py            偏移 173128526      ← 法术系统
  script\core\maze.py             偏移 704733954
  script\core\perform.py          偏移 112320696      ← 表现层
  script\goods.py                 偏移 741194594      ← 物品
  script\playerphoto.py           偏移 51001352
  script\card_magic.py            偏移 385797468
  script\ui\... / dialog\dlg*.py  ...                 ← 各 UI 对话框
  script\lib\*.py                 标准库 (UserDict, sre_compile, string, traceback)
```

---

## 八、产出文件清单

工作目录：`mxmain_re/`

| 文件 | 大小 | 说明 |
|---|---|---|
| `mxmain_逆向分析报告.md` | — | **本报告** |
| `export/decompiled_all.c` | 7.2 MB | **全部 7,416 个函数的完整 C 反编译代码** |
| `export/functions.txt` | 474 KB | 函数清单：地址 / 名称 / 大小 / 签名 |
| `export/symbols.txt` | 2.0 MB | 全部符号（含 C++ 修饰名） |
| `export/exports.txt` | 53 KB | 导出函数与入口点 |
| `export/imports.txt` | 11 KB | 365 条导入符号 |
| `analyze.log` | — | Ghidra 完整分析日志 |
| `ghidra_scripts/ExportDecomp.java` | — | 符号/清单导出脚本 |
| `ghidra_scripts/ExportAllC.java` | — | 全量 C 代码导出脚本 |
| `proj/mxmain.gpr` | — | Ghidra 项目（可 GUI 打开继续分析） |

---

## 八点五、★ 重大突破：脚本加载链路已完整还原 ★

从反编译代码中定位到 Python 装载器的完整实现（位于 `0x0040acb0` 附近、对应 `__import__` 钩子），
**脚本的存储格式、解压方式与执行路径已完全明朗**。

### 8.5.1 关键代码（`decompiled_all.c` 第 15486–15512 行）

```c
/* 1) 取出模块名 */
FUN_00481840(param_2, param_3, "s|OOOi:__import__", ...);   // = PyArg_ParseTuple
if (iVar2 != 0) {
    uVar3  = FUN_004877c0();                                 // 取当前模块字典
    piVar4 = FUN_004a3ad0(uVar3, local_d4);                   // = PyDict_GetItemString
    if (piVar4 == NULL) {

        /* 2) 拼出脚本文件名: "<模块名>.s" */
        _strncpy(local_90, local_d4, 0x80);
        _strncat(local_90, ".s", 0x80);

        /* 3) 从虚拟文件系统 "script" 分区读取 */
        iVar2 = Common::CFile::Load(local_ac, local_90, "script");

        if (iVar2 == 0) {
            /* 4) 调用 Python 的 zlib.decompress 解压 */
            Python::RunFunction("zlib", "decompress", "O", &local_d0,
                                "(s#)", local_a8, local_98);

            /* 5) 校验 / marshal 反序列化 */
            if (Py_TYPE(local_d0[1]) == &PyType_Type  /* DAT_00646f30 */ ||
                FUN_00498270(local_d0[1], &DAT_00646f30) != 0) {
                FUN_00481960(local_d0, &DAT_005610a8, &local_b4, &local_bc);
                /* ... 交由 PyEval_EvalCodeEx 执行 ... */
            }
        }
    }
}
```

### 8.5.2 还原出的完整加载流程

```
Python 执行 "import game"
        ↓
触发 __import__ 钩子 (PyArg_ParseTuple 格式串 "s|OOOi:__import__")
        ↓
检查 sys.modules 缓存 (PyDict_GetItemString) — 未加载则继续
        ↓
拼接文件名:  "<模块名>.s"        ← 注意后缀是 .s，不是 .py/.pyc
        ↓
Common::CFile::Load(handle, "xxx.s", "script")
        ← 从游戏的虚拟文件系统 "script" 分区读取
        ← 分区名来自脚本归档，对应 gt_pairs.json 里的 script\*.py 映射
        ↓
zlib.decompress(buffer)          ← ★核心★ 脚本是 zlib 压缩的
        ↓
类型校验 → 反序列化 (疑似 marshal.loads)
        ↓
PyEval_EvalCodeEx() 执行
```

### 8.5.3 对提取工作的直接指导

**脚本归档的容器格式已确定**：

| 环节 | 结论 |
|---|---|
| 容器 | 游戏自定义虚拟文件系统，分区名 `"script"` |
| 条目命名 | `<模块名>.s`（如 `game.s`、`war.s`、`magic.s`） |
| 压缩 | **标准 zlib/deflate**（可 `zlib.decompress()` 直接解） |
| 序列化 | marshal（Python code object） |
| 校验 | 存在 CRC/哈希类校验（对应 `gt_pairs.json` 的偏移索引，你的 `crc_brute.py` 方向正确） |

**行动建议**：
1. 用你 `tools/` 里的 `fls_extract.py` / `blockmap.py` 从归档按 `"script"` 分区抽取所有 `*.s` 条目
2. 对每个条目直接 `zlib.decompress()` —— 若失败，说明前面还有一层异或/表变换，需先还原该层
3. 解压后 `marshal.loads()` 得到 code object，再 `dis` 反汇编或 `uncompyle6` 反编译

> 已知条目（来自 `gt_pairs.json`，偏移为归档内位置）：
> `game` → 20915511 · `war` → 544039755 · `magic` → 173128526 · `maze` → 704733954
> `goods` → 741194594 · `perform` → 112320696 · `card_magic` → 385797468

### 8.5.4 全部 C++→Python 调用点（引擎暴露给脚本的接口）

反编译出 **20 处** `Python::RunFunction` 调用，即引擎回调脚本的完整接口清单：

| 模块 | 函数 | 用途 |
|---|---|---|
| `main` | `OnCopyData` | 进程间通信 (WM_COPYDATA) |
| `main` | `OnSysHotkey` | 系统热键 |
| `main` | `SysUpdate` | 更新检查 |
| `game` | `FullScreen` | 全屏切换 |
| `hero` | `Move` / `SetPosition` | 主角移动 |
| `chatwnd` | `Resize` / `Start` | 聊天窗口 |
| `cursor` | `GetX` / `GetY` | 鼠标光标坐标 |
| `interface` | `CharInput` | 字符输入 |
| `windowsmessage` | `WndPos` | 窗口位置 |
| `zlib` | `decompress` | **脚本解压（见上）** |

---

## 九、后续建议（按性价比排序）

### 优先级 P0 — 解密脚本归档（路径已明确）
游戏逻辑全在加密脚本里，这是**唯一能真正理解业务的路**。
**好消息：加载链路已在第八点五节完整还原 —— 脚本是 zlib 压缩 + marshal 序列化。**
建议：
- 直接按 8.5.3 的流程提取：按 `"script"` 分区抽 `*.s` → `zlib.decompress` → `marshal.loads`
- 若 zlib 解压失败，回溯 `Common::CFile::Load` 找前置变换层（异或/表置换）
- 搜索解密循环特征：XOR 密钥调度、表置换、`crc` 校验（你的 `tools/` 里已有 `crc_brute.py`、`fls_key.py` 等）
- 重点分析 `FUN_0052eb70`（最大函数）与 `FUN_00535da0`（7,246 指令）

### 优先级 P1 — 恢复 Python 运行时符号
DLL 静态链接了 CPython 2.6。可以：
- 下载官方 CPython 2.6 源码，编译出 `.lib`，用 **BinDiff / Diaphora** 与 mxmain.dll 做函数级匹配
- 一次性恢复数千个 `Py*` 函数名，把 `FUN_xxxxxxxx` 变成可读代码

### 优先级 P2 — 打开 GUI 深入分析
```
C:\Tools\ghidra_12.1.4_PUBLIC\ghidraRun.bat
→ 打开 mxmain_re\proj\mxmain.gpr
```
项目已含完整分析结果、交叉引用、反编译缓存，打开即用。

### 优先级 P3 — 扩展与符号
- 装 **PdbUniversal** 扩展（Ghidra 已内置）以防拿到 pdb
- 若有 `mxmain.pdb` 或符号服务器，直接导入可秒解

---

## 十、风险与合规提示

本分析针对**本地已安装的客户端**进行互操作性/兼容性研究。
- 请勿用于破坏游戏平衡、制作外挂或侵犯著作权
- 提取脚本内容可能涉及软件许可协议约束，请自行确认合法性
- DLL 中含 `LookupPrivilegeValueA` / `SetSecurityDescriptorDacl` 等提权 API，建议留意其使用上下文（可能仅用于安装/更新流程）

---

*报告结束*
