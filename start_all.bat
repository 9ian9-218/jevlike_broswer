@echo off
chcp 65001 >nul
title Scenario 5 - 一键启动 (Laya GPU 服务 + 浏览器 Agent)

echo ============================================================
echo  场景 5 一键启动：本地 Laya GPU 推理服务 + 浏览器自动化 Agent
echo ============================================================
echo.
echo  [1/2] 正在启动 Laya 微调模型 GPU 推理服务 (新窗口, 端口 8791)...
start "Laya GPU Server" cmd /k "%~dp0start_laya_server.bat"

echo  [2/2] 等待模型加载至显存 (约 20 秒)...
timeout /t 20 /nobreak >nul

echo  正在启动浏览器 Agent 交互控制台 (新窗口)...
start "Browser Agent" cmd /k "%~dp0run.bat"

echo.
echo  两个窗口均已启动！
echo   - Laya GPU Server 窗口: 模型推理服务 (请保持开启)
echo   - Browser Agent 窗口:   输入网址与任务指令即可自动化操作
echo.
echo  本窗口可以关闭。
timeout /t 5 >nul
