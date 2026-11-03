@echo off
setlocal
if "%~1"=="" (
    echo Usage: build.cmd "path\to\original.apk" [options]
    echo You can also drag an original APK onto this file.
    pause
    exit /b 1
)
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0build.py" %*
) else (
    python "%~dp0build.py" %*
)
set "buildExit=%errorlevel%"
echo.
if not "%buildExit%"=="0" echo Build failed. Read the message above.
pause
exit /b %buildExit%
