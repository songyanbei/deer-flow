@echo off
setlocal

powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start-full-dev.ps1"

endlocal
