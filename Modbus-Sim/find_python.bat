@echo off
REM Locate an exact Python 3.11 interpreter for bootstrapping .venv.
REM Priority: project .venv, explicit environment variables, py launcher,
REM known Miniforge environments, Miniforge environment folders, then PATH.

for %%I in ("%~dp0.") do set "MODBUSSIM_ROOT=%%~fI"
set "PY="
set "MODBUSSIM_EXISTING_VENV_INVALID="

if exist "%MODBUSSIM_ROOT%\.venv\Scripts\python.exe" (
  call :accept_python "%MODBUSSIM_ROOT%\.venv\Scripts\python.exe"
  if not defined PY set "MODBUSSIM_EXISTING_VENV_INVALID=1"
  goto :eof
)

if defined MODBUSSIM_PYTHON (
  call :accept_python "%MODBUSSIM_PYTHON%"
  if defined PY goto :eof
)

if defined PYTHON (
  call :accept_python "%PYTHON%"
  if defined PY goto :eof
)

for /f "delims=" %%p in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do (
  call :accept_python "%%p"
  if defined PY goto :eof
)

for %%p in (
  "D:\miniforge3\envs\szlab-unilab\python.exe"
  "D:\miniforge3\envs\unilab\python.exe"
  "D:\miniforge3\python.exe"
) do (
  call :accept_python "%%~p"
  if defined PY goto :eof
)

for /d %%d in ("D:\miniforge3\envs\*") do (
  call :accept_python "%%~fd\python.exe"
  if defined PY goto :eof
)

for /f "delims=" %%p in ('where.exe python3.11 2^>nul ^| findstr /V /I "WindowsApps"') do (
  call :accept_python "%%p"
  if defined PY goto :eof
)

for /f "delims=" %%p in ('where.exe python 2^>nul ^| findstr /V /I "WindowsApps"') do (
  call :accept_python "%%p"
  if defined PY goto :eof
)
goto :eof

:accept_python
if not exist "%~1" exit /b 0
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0
