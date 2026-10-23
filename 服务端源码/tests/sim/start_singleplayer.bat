@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0

rem 单机一键启动：游戏内核（守护线程）+ 客户端托管，一个进程全包
rem 用法：直接双击；或带参数，如
rem   start_singleplayer.bat --fresh
rem   start_singleplayer.bat --no-encounter --web
rem   start_singleplayer.bat --no-client
rem   start_singleplayer.bat --save-dir D:\mxsj2013\单机存档

set PY="C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist %PY% (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 找不到 Python：请安装 Python 3.8+ 或修改本批处理里的 PY 路径。
        pause
        exit /b 1
    )
    set PY=python
)

echo ==========================================================
echo  《梦想世界2013》本地单机
echo  内核线程 + 回环传输 + 客户端托管
echo ==========================================================

%PY% singleplayer.py %*
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo [失败] 单机宿主退出码 %RC%，常见原因见上方的 [配置]/[告警] 行。
)
pause
endlocal
exit /b %RC%
