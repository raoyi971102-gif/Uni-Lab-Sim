@echo off
REM ============================================================
REM XUSE OPC UA Simulation Server - launcher
REM   Usage:
REM     start.bat                         (use default CSV)
REM     start.bat "D:\path\to\my.csv"     (use given CSV)
REM     start.bat "a.csv" "b.csv" ...     (merge multiple CSVs)
REM     (drag & drop CSV file(s) onto this bat also works)
REM   Any argument NOT ending with .csv is forwarded to server.py
REM   (e.g. --port 4860)
REM ============================================================
setlocal EnableDelayedExpansion
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
call "%~dp0scripts\find_python.bat"
if "%PY%"=="" (
    echo [X] Python 3.11 not found. Run setup_venv.bat or install Python 3.11.
    exit /b 1
)

set "CSV_ARGS="
set "EXTRA_ARGS="

:loop
if "%~1"=="" goto after_parse
set "arg=%~1"
REM Detect .csv (case-insensitive) by extension
if /i "%~x1"==".csv" (
    set CSV_ARGS=!CSV_ARGS! --csv "%~1"
) else (
    set EXTRA_ARGS=!EXTRA_ARGS! %1
)
shift
goto loop

:after_parse
echo.
echo ==============================================================
echo   XUSE OPC UA Simulation Server
echo   Endpoint : opc.tcp://0.0.0.0:4855/xuse_sim/
if not "%CSV_ARGS%"=="" (
    echo   CSV args : %CSV_ARGS%
) else (
    echo   CSV      : ^(default^) data\demo_variables.csv
)
if not "%EXTRA_ARGS%"=="" echo   Extra    : %EXTRA_ARGS%
echo   Ctrl+C to stop
echo ==============================================================
echo.

"%PY%" "%~dp0server.py" %CSV_ARGS% %EXTRA_ARGS%

endlocal
