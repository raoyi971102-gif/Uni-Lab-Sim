@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3.11 -m venv .venv
if errorlevel 1 exit /b %errorlevel%
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q .
if errorlevel 1 exit /b %errorlevel%
".venv\Scripts\modbus-sim.exe" gui %*
