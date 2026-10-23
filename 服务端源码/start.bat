@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title 梦想世界2013 本地单机 · 一键启动

REM ============================================================
REM  一键启动：内核 + HTTP劫持 + 客户端
REM
REM  用法：双击本文件即可。
REM  可选参数（本文件同目录命令行执行）：
REM      start.bat --web              启用 GM 面板
REM      start.bat --fresh            清空存档重开
REM      start.bat --no-encounter     关遇怪（纯探索）
REM      start.bat --enemies 6        每场 6 只敌
REM ============================================================

set "SCRIPT_DIR=%~dp0"
set "SIM_DIR=%SCRIPT_DIR%sim"

echo.
echo  ============================================================
echo   梦想世界2013 本地单机服
echo  ============================================================
echo.

REM ---------- 1. 找 Python（优先 py 启动器，避开 WindowsApps 存根）----------
REM  ★ 2026-10-20 实测：where python 会命中 WindowsApps 下的**存根**，
REM    那个存根在提权环境里跑不起来（会弹应用商店）。必须用 py 启动器
REM    或 sys.executable 拿到的真实路径。
set "PY="

REM 1a. 优先用 py 启动器解析真实解释器路径
where py >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('py -c "import sys;print(sys.executable)" 2^>nul') do (
        set "PY=%%i"
    )
)

REM 1b. 退回 python，但要排除 WindowsApps 存根
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        echo %%i | findstr /i "WindowsApps" >nul
        if errorlevel 1 if not defined PY set "PY=%%i"
    )
)

REM 1c. 都没找到 → 报错退出
if not defined PY (
    echo  [错误] 找不到可用的 Python。
    echo.
    echo  请任选其一：
    echo    · 安装 Python 3.9+ 并勾选 Add to PATH
    echo    · 安装 Python 启动器 py.exe（推荐，官方安装包自带）
    echo    · 在 %SCRIPT_DIR% 下建 .venv 虚拟环境
    echo.
    pause
    exit /b 1
)

echo  [1/5] Python: !PY!

REM ---------- 2. 检查客户端 ----------
set "CLIENT_DIR=D:\mxsj2013\client"
if not exist "%CLIENT_DIR%\mx.exe" (
    echo.
    echo  [错误] 客户端不完整：%CLIENT_DIR%\mx.exe 不存在
    echo.
    echo  如果你装在别处，请修改本文件里的 CLIENT_DIR 变量。
    echo.
    pause
    exit /b 1
)
echo  [2/5] 客户端: %CLIENT_DIR%

REM ---------- 3. 端口占用预检 ----------
echo  [3/5] 检查端口 3134 / 8080 ...
set "PORT_BUSY=0"
for %%P in (3134 8080) do (
    netstat -ano | findstr ":%%P " | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        echo        ⚠ 端口 %%P 已被占用
        set "PORT_BUSY=1"
    )
)
if "!PORT_BUSY!"=="1" (
    echo.
    echo  [提示] 有端口被占用。若占用者是上次残留的本服进程，继续即可
    echo         （内核会自动挑空闲端口）；若是别的程序，可能影响客户端连接。
    echo.
    choice /c YN /n /m "  继续启动？[Y/N] "
    if errorlevel 2 exit /b 0
) else (
    echo        端口空闲
)

REM ---------- 4. 提权检查 ----------
echo  [4/5] 检查管理员权限 ...
net session >nul 2>&1
if not errorlevel 1 (
    echo        已提权
    set "NEED_UAC=0"
) else (
    echo        未提权 —— 客户端 mx.exe 要求管理员权限，会弹 UAC
    set "NEED_UAC=1"
)

REM ---------- 5. 启动 ----------
echo  [5/5] 启动 ...
echo.
echo  ------------------------------------------------------------
echo   客户端起来后：
echo     · 选服界面第一行「本地测试服」就是本服
echo     · 帐号纯数字、密码 6 位
echo     · 关闭游戏窗口 = 服务端自动落档退出
echo  ------------------------------------------------------------
echo.

cd /d "%SIM_DIR%"
if "%NEED_UAC%"=="1" (
    echo  [提示] 即将请求 UAC 提权，请在弹窗中点「是」...
    echo.
    REM ★ 2026-10-20 实测：Start-Process -Verb RunAs 的 -WorkingDirectory
    REM   在提权场景下**不生效**，相对路径 singleplayer.py 会被解析到
    REM   C:\Windows\system32\ 下，报「can't open file」。
    REM   必须传**绝对路径**，并在 PowerShell 里先 Set-Location。
    set "SP_PY=%SIM_DIR%\singleplayer.py"
    powershell -NoProfile -Command "Start-Process -FilePath '!PY!' -ArgumentList '\"!SP_PY!\"' -Verb RunAs -WorkingDirectory '%SIM_DIR%'"
    if errorlevel 1 (
        echo.
        echo  [错误] 提权失败或用户取消。
        echo         可手动「右键以管理员身份运行」本文件。
        echo.
        pause
        exit /b 1
    )
    echo  已在管理员窗口启动。本窗口可以关闭。
) else (
    "!PY!" "%SIM_DIR%\singleplayer.py" %*
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
        echo.
        echo  [退出码] !RC!
    )
)

timeout /t 3 >nul
endlocal
exit /b 0
