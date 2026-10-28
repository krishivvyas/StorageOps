
@echo off
title StorageOps - AI Storage Optimizer

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Python virtual environment not found.
    echo Please check that this file is inside StorageOps_PBL_Audit.
    pause
    exit /b 1
)

echo Starting StorageOps...
".venv\Scripts\python.exe" -m storageops gui

if errorlevel 1 (
    echo.
    echo StorageOps could not start. Please check the error above.
    pause
)
