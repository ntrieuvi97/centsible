@echo off
setlocal
set "BIN_DIR=%~dp0"
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python "%BIN_DIR%centsible" %*
) else (
    py -3 "%BIN_DIR%centsible" %*
)
