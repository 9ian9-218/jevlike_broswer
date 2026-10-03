@echo off
chcp 65001 >nul
title Laya Browser Model Service (GPU, Port 8791)

echo [INFO] 正在启动本地 Laya 浏览器微调模型服务 (自动优先 GPU)...
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 未找到虚拟环境 .venv！
    pause
    exit /b 1
)

.venv\Scripts\python.exe start_laya_server.py --port 8791

pause
