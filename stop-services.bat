@echo off
setlocal

powershell -ExecutionPolicy Bypass -File "%~dp0scripts\stop-full-dev.ps1"

endlocal
