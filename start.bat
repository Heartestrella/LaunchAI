@echo off
 chcp 65001 >nul
 cd /d "%~dp0"
 set PYTHONUTF8=1
 set PYTHONIOENCODING=utf-8
 .\py311\python.exe app.py
 pause