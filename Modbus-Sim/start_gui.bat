@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

call "%~dp0find_python.bat"
if not defined PY (
  if defined MODBUSSIM_EXISTING_VENV_INVALID (
    echo [X] Existing .venv is not Python 3.11.
    echo     Rename or remove "%~dp0.venv", then run this script again.
  ) else (
    echo [X] Python 3.11 was not found.
    echo     Set MODBUSSIM_PYTHON to a Python 3.11 executable, or install Python 3.11.
  )
  goto :failed
)

echo ========================================================================
echo  Modbus-Sim GUI
echo  Python : %PY%
echo  Default URL : http://127.0.0.1:18865/
echo ========================================================================
echo.

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [1/2] Creating the project virtual environment...
  "%PY%" -m venv "%~dp0.venv"
  if errorlevel 1 goto :failed
)

"%~dp0.venv\Scripts\python.exe" -c "import modbus_sim, pymodbus, yaml, fastapi, uvicorn, pydantic" >nul 2>&1
if errorlevel 1 (
  echo [2/2] Installing Modbus-Sim and its dependencies...
  "%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e "%~dp0."
  if errorlevel 1 goto :failed
) else (
  echo [2/2] Refreshing the local Modbus-Sim package...
  "%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --no-build-isolation --no-deps -e "%~dp0."
  if errorlevel 1 goto :failed
)

echo [OK] Starting GUI...
echo.
"%~dp0.venv\Scripts\python.exe" -m modbus_sim gui %*
if errorlevel 1 goto :failed
exit /b 0

:failed
set "MODBUSSIM_EXIT_CODE=%ERRORLEVEL%"
if "%MODBUSSIM_EXIT_CODE%"=="0" set "MODBUSSIM_EXIT_CODE=1"
echo.
echo [X] Startup failed. Exit code: %MODBUSSIM_EXIT_CODE%
echo     Press any key to close this window.
pause >nul
exit /b %MODBUSSIM_EXIT_CODE%
