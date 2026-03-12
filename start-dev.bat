@echo off
REM DeerFlow Development Server Startup Script

echo.
echo ==========================================
echo   Starting DeerFlow Development Server
echo ==========================================
echo.

REM Kill any existing processes
echo Stopping existing services...
taskkill /F /IM node.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

REM Create logs directory
if not exist logs mkdir logs

echo.
echo Services starting up...
echo   - Backend: LangGraph Server (port 2024)
echo   - Backend: Gateway API (port 8001)
echo   - Frontend: Next.js (port 3000)
echo.

REM Start LangGraph Server
echo Starting LangGraph server...
start "LangGraph" cmd /k "cd /d %cd%\backend && uv run langgraph dev --no-browser --allow-blocking --no-reload"
timeout /t 3 /nobreak >nul
echo.

REM Start Gateway API
echo Starting Gateway API...
start "Gateway API" cmd /k "cd /d %cd%\backend && uv run uvicorn src.gateway.app:app --host 0.0.0.0 --port 8001"
timeout /t 3 /nobreak >nul
echo.

REM Start Frontend
echo Starting Frontend...
start "Frontend" cmd /k "cd /d %cd%\frontend && pnpm run dev"
timeout /t 3 /nobreak >nul
echo.

echo ==========================================
echo   DeerFlow is starting up!
echo ==========================================
echo.
echo   Application: http://localhost:2026
echo   API Gateway: http://localhost:2026/api/*
echo   LangGraph:   http://localhost:2026/api/langgraph/*
echo.
echo   Individual ports:
echo   - LangGraph: http://localhost:2024
echo   - Gateway:   http://localhost:8001
echo   - Frontend:  http://localhost:3000
echo.
echo Services are starting in separate windows.
echo Close the windows to stop services.
echo.
pause
