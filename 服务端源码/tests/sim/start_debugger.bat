@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0

rem 可视化调试台一键启动：游戏内核 + 抓包 + 本地 Web 界面（默认 127.0.0.1:8090）
rem 用法：直接双击；或带参数，如
rem   start_debugger.bat --no-client            只起内核+调试台（脚本联调）
rem   start_debugger.bat --fresh               删档从零开始
rem   start_debugger.bat --dump 会话.jsonl      退出时导出会话
rem   start_debugger.bat --port 8091 --no-http  换端口 / 不起 HTTP 劫持
rem
rem 界面五大页签：帧流（实时收发帧 + 逐字段解码 + hex）/ 地图（玩家·NPC·出口）/
rem               战斗（双方单位·血条·伤害·语义化动作时间线）/
rem               协议覆盖（未实现·未登记编码清单）/ 状态·GM（实时状态 + 热操作 + 帧注入）

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
echo  《梦想世界2013》可视化调试台
echo  协议帧流 / 地图 / 协议覆盖 / 状态·GM
echo  启动后浏览器打开： http://127.0.0.1:8090/
echo ==========================================================

%PY% debugger.py %*
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo [失败] 调试台退出码 %RC%，常见原因见上方 [配置]/[告警] 行。
)
pause
endlocal
exit /b %RC%
