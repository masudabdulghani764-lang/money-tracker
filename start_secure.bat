@echo off
title Money Tracker by Mas'ud - Secure Vault
echo ===================================================
echo   Starting Money Tracker by Mas'ud...
echo ===================================================
cd /d "%~dp0"

echo 1. Starting Backend Server...
start /b python app.py

timeout /t 3 >nul

echo 2. Launching Cloudflare Official Secure Tunnel...
bin\cloudflared.exe tunnel --protocol http2 --url http://127.0.0.1:5000
pause
