@echo off
REM ============================================================
REM Interactive picker: opens a file dialog to choose CSV(s),
REM then launches the OPC UA server via start.bat
REM   - Ctrl-click for multi-select (all chosen CSVs get merged)
REM ============================================================
setlocal EnableDelayedExpansion
chcp 65001 > nul

REM Ask user to pick one or more CSV files (via PowerShell OpenFileDialog).
REM Each selected full path is echoed on its own line.
set "CSV_LIST_FILE=%TEMP%\_xuse_sim_csv_pick.txt"
if exist "%CSV_LIST_FILE%" del /q "%CSV_LIST_FILE%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Add-Type -AssemblyName System.Windows.Forms;" ^
  "$d = New-Object System.Windows.Forms.OpenFileDialog;" ^
  "$d.Filter = 'CSV variable table (*.csv)|*.csv|All files (*.*)|*.*';" ^
  "$d.Title  = 'Choose OPC UA variable CSV (Ctrl-click for multiple)';" ^
  "$d.Multiselect = $true;" ^
  "$d.InitialDirectory = (Resolve-Path '%~dp0data' -ErrorAction SilentlyContinue).Path;" ^
  "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $d.FileNames | Out-File -FilePath '%CSV_LIST_FILE%' -Encoding utf8 }"

if not exist "%CSV_LIST_FILE%" (
    echo Selection cancelled.
    exit /b 1
)

REM Build a quoted CSV arg list from the chosen files
set "CSV_ARGS="
for /f "usebackq delims=" %%L in ("%CSV_LIST_FILE%") do (
    if not "%%L"=="" set CSV_ARGS=!CSV_ARGS! "%%L"
)
del /q "%CSV_LIST_FILE%"

if "%CSV_ARGS%"=="" (
    echo No file selected.
    exit /b 1
)

echo Selected CSVs: %CSV_ARGS%
echo.

REM Delegate to start.bat (which knows how to parse .csv args)
call "%~dp0start.bat" %CSV_ARGS% %*

endlocal
