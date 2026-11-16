@echo off
rem Doble clic aqui para encender NEXORA-CONTROL (panel web + API de
rem NEXCAM + bot de Telegram). No cierres la ventana que se abre: eso
rem apaga el servidor.
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar-nexora.ps1"
echo.
pause
endlocal
