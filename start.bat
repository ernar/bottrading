@echo off
title MT4 Ollama Bot Launcher

echo.
echo ========================================
echo   MT4 Ollama Bot - Iniciando...
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
    echo         Crea .env con MT4_LOGIN, MT4_PASSWORD y MT4_HOST
    echo.
    pause
    exit /b 1
)
echo [OK] Archivo .env encontrado.

REM Iniciar bot + API server (proceso unico con estado compartido)
REM cmd /k mantiene la ventana abierta: aqui debes responder los prompts
REM (seleccion de agente). El dashboard no tendra datos hasta que
REM el bot conecte a MT4.
echo.
echo Iniciando bot...
echo   IMPORTANTE: en la ventana "MT4 Bot" responde el prompt
echo   de seleccion de agentes. El dashboard no tendra datos hasta que
echo   el bot conecte a MT4.
start "MT4 Bot" cmd /k python main.py

REM Esperar a que el API server este listo (tras conectar a la plataforma)
timeout /t 4 /nobreak >nul

REM Iniciar dashboard React
echo Iniciando dashboard...
start "MT4 Dashboard" cmd /k "cd /d "%~dp0frontend" && npm run dev --strict-ssl=false"

echo.
echo ========================================
echo   Bot:       consola "MT4 Bot"
echo   API:       http://localhost:5000
echo   Dashboard: http://localhost:5173
echo ========================================
echo.
