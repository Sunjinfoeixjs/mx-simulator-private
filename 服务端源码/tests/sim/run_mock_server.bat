@echo off
rem Local takeover launcher (Phase-1): HTTP hijack + mock server
cd /d "C:\Users\Administrator\MX\服务端源码\sim"
set PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe

echo [1/2] HTTP hijack 8080 ...
start "HttpHijack-8080" "%PY%" local_http.py
timeout /t 2 /nobreak >nul

echo [2/2] Mock server 3134 ...
"%PY%" mock_server_min.py %*
pause
