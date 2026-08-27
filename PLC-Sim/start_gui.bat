@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0scripts\find_python.bat"
if "%PY%"=="" (
  echo [X] Python 3.11 not found. Run setup_venv.bat or install Python 3.11.
  pause
  exit /b 1
)

set "HOST=127.0.0.1"
set "PORT=18765"

echo ========================================================================
echo  PLC-Sim GUI
echo  Python : %PY%
echo  URL    : http://%HOST%:%PORT%/
echo ========================================================================
echo.
"%PY%" -m gui.backend --host %HOST% --port %PORT%
echo.
echo Server exited. Press any key to close.
pause >nul
endlocal
