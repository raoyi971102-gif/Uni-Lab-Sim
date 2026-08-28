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

set "OPCUA_URL=opc.tcp://127.0.0.1:4855/xuse_sim/"
if not "%~1"=="" set "OPCUA_URL=%~1"

echo ========================================================================
echo  SZLab Poly Studio Handshake Simulator
echo  Python   : %PY%
echo  Endpoint : %OPCUA_URL%
echo ========================================================================
echo.
"%PY%" "%~dp0szlab_handshake_agent.py" --url "%OPCUA_URL%" --config "%~dp0config\szlab_handshake.yaml"
echo.
echo Simulator exited. Press any key to close.
pause >nul
endlocal
