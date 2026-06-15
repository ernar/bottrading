@echo off
title MT4 Ollama Bot Launcher

echo.
echo ========================================
echo   MT4 Ollama Bot - Iniciando...
echo ========================================
echo.

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
REM (seleccion de agente). El dashboard (que corre en otro entorno y se
REM conecta por API) no tendra datos hasta que el bot conecte a MT4.
echo.
echo Iniciando bot...
echo   IMPORTANTE: en la ventana "MT4 Bot" responde el prompt
echo   de seleccion de agentes.
start "MT4 Bot" cmd /k python main.py

echo.
echo ========================================
echo   Bot: consola "MT4 Bot"
echo   API: http://localhost:5000
echo   El dashboard se ejecuta aparte (otro entorno) y se conecta por API.
echo ========================================
echo.
