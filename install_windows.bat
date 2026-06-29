@echo off
REM ============================================================
REM PictureViewer Server - Windows Installer
REM ============================================================

echo.
echo ============================================================
echo   PictureViewer Server - Windows Installer
echo ============================================================
echo.

SET SCRIPT_DIR=%~dp0
SET VENV_DIR=%SCRIPT_DIR%venv

REM Check Python
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python 3 is required but not installed.
    echo Download it from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

python --version
echo.

REM Create virtual environment
echo Creating virtual environment...
python -m venv "%VENV_DIR%"

REM Install dependencies
echo Installing dependencies...
call "%VENV_DIR%\Scripts\activate.bat"
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip -q
REM --no-cache-dir avoids pip reusing a corrupt cached Pillow wheel that
REM ships without the _imaging C extension and crashes the server at import.
"%VENV_DIR%\Scripts\python.exe" -m pip install --no-cache-dir -r "%SCRIPT_DIR%requirements.txt" -q

REM Verify Pillow imports correctly. If not, force a clean reinstall.
"%VENV_DIR%\Scripts\python.exe" -c "from PIL import Image, ImageOps" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Pillow install was incomplete, repairing...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --force-reinstall --no-cache-dir Pillow -q
    "%VENV_DIR%\Scripts\python.exe" -c "from PIL import Image, ImageOps" >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        echo ERROR: Pillow still cannot be imported. Install the Microsoft Visual
        echo C++ Redistributable from https://aka.ms/vs/17/release/vc_redist.x64.exe
        echo and re-run this installer.
        pause
        exit /b 1
    )
)
echo Dependencies installed successfully.

echo.
echo --- Configuration ---
echo.

REM Ask for media folder
set /p MEDIA_FOLDER="Media folder path [%USERPROFILE%\Pictures]: "
if "%MEDIA_FOLDER%"=="" set "MEDIA_FOLDER=%USERPROFILE%\Pictures"

REM Ask for port
set /p PORT="Server port [8500]: "
if "%PORT%"=="" set "PORT=8500"

REM Ask for a strong, non-default access code. A short or well-known code is
REM trivially brute-forced on a LAN, and the server refuses the default.
:get_access_code
set "ACCESS_CODE="
set /p ACCESS_CODE="Choose an access code (12+ chars: lowercase, UPPERCASE, number, special): "
set "CODE_OK="
for /f %%v in ('powershell -NoProfile -Command "if ($env:ACCESS_CODE.Length -ge 12 -and $env:ACCESS_CODE -cmatch '[a-z]' -and $env:ACCESS_CODE -cmatch '[A-Z]' -and $env:ACCESS_CODE -match '\d' -and $env:ACCESS_CODE -match '[^a-zA-Z0-9]') { 'ok' }"') do set "CODE_OK=%%v"
if not "%CODE_OK%"=="ok" (
    echo   -^> Must be 12+ characters with a lowercase letter, an uppercase letter, a number, and a special character. Try again.
    goto get_access_code
)

REM Generate secret key
for /f %%i in ('python -c "import secrets; print(secrets.token_hex(32))"') do set SECRET_KEY=%%i

REM Put the values in the environment first (quoted SET keeps special characters
REM like # $ ! & intact), then write .env with Python — NOT with `echo`, because
REM echo would let characters such as & < > | corrupt the file or run as
REM commands, leaving a broken access code that makes the server refuse to start.
set "PICTUREVIEWER_MEDIA_FOLDER=%MEDIA_FOLDER%"
set "PICTUREVIEWER_ACCESS_CODE=%ACCESS_CODE%"
set "PICTUREVIEWER_SECRET_KEY=%SECRET_KEY%"
set "PV_ENV_FILE=%SCRIPT_DIR%.env"
"%VENV_DIR%\Scripts\python.exe" -c "import os; open(os.environ['PV_ENV_FILE'], 'w', encoding='utf-8').write(''.join('%%s=%%s\n' %% (k, os.environ[k]) for k in ('PICTUREVIEWER_MEDIA_FOLDER','PICTUREVIEWER_ACCESS_CODE','PICTUREVIEWER_SECRET_KEY')))"
if not exist "%PV_ENV_FILE%" (
    echo ERROR: could not write the settings file ^(.env^). Please re-run this installer.
    pause
)

REM Restrict the .env (contains SECRET_KEY + ACCESS_CODE) to the current user only.
icacls "%SCRIPT_DIR%.env" /inheritance:r /grant:r "%USERNAME%:F" >nul

REM Create start script. The server reads .env itself (see config.py), so the
REM start script just needs to run it from this folder — no fragile batch
REM parsing of the .env values (which would choke on special characters).
(
echo @echo off
echo cd /d "%SCRIPT_DIR%"
echo call "%VENV_DIR%\Scripts\activate.bat"
echo python server.py
echo pause
) > "%SCRIPT_DIR%start_server.bat"

REM Create Windows Task Scheduler XML for auto-start (optional)
echo.
set /p AUTO_START="Start server automatically on login? [y/N]: "
if /i "%AUTO_START%"=="y" (
    schtasks /create /tn "PictureViewerServer" /tr "\"%VENV_DIR%\Scripts\python.exe\" \"%SCRIPT_DIR%server.py\"" /sc onlogon /f >nul 2>nul
    echo Server registered as startup task.
)

REM Get IP address
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do set IP_ADDRESS=%%b
)

echo.
echo ============================================================
echo   Installation Complete!
echo ============================================================
echo.
echo   Media Folder  : %MEDIA_FOLDER%
echo   Server URL    : http://%IP_ADDRESS%:%PORT%
echo   Access Code   : %ACCESS_CODE%
echo.
echo   To start the server:
echo     Double-click: start_server.bat
echo.
echo   Enter the URL and access code in the iOS app to connect.
echo ============================================================
echo.

set /p START_NOW="Start the server now? [Y/n]: "
if /i not "%START_NOW%"=="n" (
    call "%VENV_DIR%\Scripts\activate.bat"
    set "PICTUREVIEWER_MEDIA_FOLDER=%MEDIA_FOLDER%"
    set "PICTUREVIEWER_ACCESS_CODE=%ACCESS_CODE%"
    set "PICTUREVIEWER_SECRET_KEY=%SECRET_KEY%"
    python "%SCRIPT_DIR%server.py"
)

pause
