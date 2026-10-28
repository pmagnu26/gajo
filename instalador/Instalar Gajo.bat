@echo off
chcp 65001 >nul
title Instalar Gajo
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
echo.
pause
