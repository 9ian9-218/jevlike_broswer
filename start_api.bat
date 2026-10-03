@echo off
chcp 65001 >nul
title Dual-track Browser Agent Task Service (Port 8792)

echo [INFO] 正在启动双轨浏览器 Agent 任务服务（仅监听 127.0.0.1）...
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 未找到虚拟环境 .venv！请先运行 setup.bat
    pause
    exit /b 1
)

.venv\Scripts\python.exe api_server.py --port 8792

pause
