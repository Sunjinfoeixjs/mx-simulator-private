@echo off
chcp 65001 >nul
setlocal

rem 兼容旧入口：统一交给 singleplayer 宿主启动内核并托管客户端生命周期。
call "%~dp0start_singleplayer.bat" %*
exit /b %ERRORLEVEL%
