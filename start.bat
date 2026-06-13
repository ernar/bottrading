@echo off
title MT5 Ollama Bot Launcher

echo.
echo ========================================
echo   MT5 Ollama Bot - Iniciando...
echo ========================================
echo.

REM Verificar que Ollama esta corriendo
curl -s http://localhost:11434 >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Ollama no esta corriendo.
    echo         Ejecuta: ollama serve
    echo.
    pause
    exit /b 1
)
echo [OK] Ollama detectado.

REM Verificar que existe el .env
if not exist ".env" (
    echo [ERROR] No se encontro el archivo .env
    echo         Crea .env con MT5_LOGIN, MT5_PASSWORD y MT5_SERVER
    echo.
    pause
    exit /b 1
)
echo [OK] Archivo .env encontrado.

REM Iniciar bot + API server (proceso unico con estado compartido)
echo.
echo Iniciando bot...
start "MT5 Bot" python main.py

REM Esperar a que el API server este listo
timeout /t 4 /nobreak >nul

REM Iniciar dashboard React
echo Iniciando dashboard...
start "MT5 Dashboard" cmd /k "cd /d "%~dp0frontend" && npm run dev --strict-ssl=false"

echo.
echo ========================================
echo   Bot:       consola "MT5 Bot"
echo   API:       http://localhost:5000
echo   Dashboard: http://localhost:5173
echo ========================================
echo.
