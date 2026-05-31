@echo off
REM Tek tikla veya tek komut: eksik GA/Tabu benchmark + Excel birlestirme
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_missing_benchmark.ps1"
exit /b %ERRORLEVEL%
