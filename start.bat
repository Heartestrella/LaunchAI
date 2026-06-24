@echo off
chcp 65001 >nul
cd /d "%~dp0"
.\py311\python.exe app.py
pause