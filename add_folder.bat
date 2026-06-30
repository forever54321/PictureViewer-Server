@echo off
setlocal
SET "SCRIPT_DIR=%~dp0"
SET "VENV_DIR=%SCRIPT_DIR%venv"

echo.
echo ============================================================
echo   Add a backup folder
echo ============================================================
echo.
echo This adds another folder that a phone can choose to back up
echo to in the app. Both phones use the SAME access code; in the
echo app you just pick which folder to back up to.
echo.

set "FOLDER_NAME="
set /p FOLDER_NAME="Name to show in the app (e.g. Wife's iPhone): "
set "FOLDER_PATH="
set /p FOLDER_PATH="Full folder path (e.g. C:\Backups\Wife): "

if not defined FOLDER_NAME (
    echo   No name entered. Nothing was added.
    pause
    exit /b 1
)
if not defined FOLDER_PATH (
    echo   No path entered. Nothing was added.
    pause
    exit /b 1
)

REM Pass the values through the environment (quoted SET keeps special
REM characters intact) and let Python save them safely.
set "PV_FOLDER_NAME=%FOLDER_NAME%"
set "PV_FOLDER_PATH=%FOLDER_PATH%"

if exist "%VENV_DIR%\Scripts\python.exe" (
    "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%add_folder.py"
) else (
    python "%SCRIPT_DIR%add_folder.py"
)

echo.
pause
