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
REM cmd /k mantiene la ventana abierta: aqui debes responder los prompts
REM (1) plataforma MT4/MT5 y (2) seleccion de agente; si MT5 no conecta,
REM el error queda visible en vez de cerrarse la ventana.
echo.
echo Iniciando bot...
echo   IMPORTANTE: en la ventana "MT5 Bot" responde los DOS prompts
echo   (plataforma y agente). El dashboard no tendra datos hasta que
echo   el bot conecte a MT4/MT5.
start "MT5 Bot" cmd /k python main.py

REM Esperar a que el API server este listo (tras conectar a la plataforma)
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
