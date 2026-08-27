@echo off
REM ============================================================
REM OPC UA Simulation - launch Server + SZLab Handshake Agent
REM (opens two console windows, one for each process)
REM ============================================================
setlocal
chcp 65001 > nul

echo Starting OPC UA Server (new window) ...
start "PLC-Sim-Server" /D "%~dp0" cmd /k call start.bat %*

echo Waiting 3 seconds for server to be ready ...
timeout /t 3 /nobreak > nul

echo Starting SZLab Handshake Agent (new window) ...
start "SZLab-HandshakeAgent" /D "%~dp0" cmd /k call start_szlab_handshake.bat %*

endlocal
