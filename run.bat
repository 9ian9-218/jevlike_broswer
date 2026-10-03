@echo off
chcp 65001 >nul
title Scenario 5 - Dual-track Browser Agent

echo [INFO] 正在启动场景 5 浏览器自动化 Agent...
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 未找到虚拟环境 .venv，正在使用 uv 快速构建...
    uv venv .venv
    uv pip install -r pyproject.toml
)

.venv\Scripts\python.exe runner.py %*

if "%1"=="" (
    pause
)
