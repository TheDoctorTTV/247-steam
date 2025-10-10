@echo off
echo ================================================
echo Stream247 - Simple Build and Sign Tool
echo ================================================
echo.

:: Change to script directory
cd /d "%~dp0"

:: Check prerequisites
if not exist "Stream247_GUI.py" (
    echo ERROR: Stream247_GUI.py not found!
    pause
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found!
    pause
    exit /b 1
)

echo [1/4] Activating virtual environment...
call ".venv\Scripts\activate.bat"

echo.
echo [2/4] Cleaning previous build...
if exist "build" rmdir /s /q "build"
if exist "dist\Stream247.exe" del "dist\Stream247.exe"

echo.
echo [3/4] Building executable...
python -m PyInstaller --onefile --noconsole --icon=icon.ico --add-data "icon.ico;." --name=Stream247 --optimize=2 Stream247_GUI.py

if not exist "dist\Stream247.exe" (
    echo ERROR: Build failed!
    pause
    exit /b 1
)

echo.
echo [4/4] Signing executable...
signtool sign /sha1 391F331C52755E694350429C0E24186ECF02AB71 /fd sha256 /td sha256 /d "Stream247 - YouTube 24/7 VOD Streamer" /du "https://github.com/TheDoctorTTV/247-steam" /tr http://timestamp.digicert.com "dist\Stream247.exe"

if errorlevel 1 (
    echo WARNING: Signing failed, but executable was created.
) else (
    echo SUCCESS: Executable signed successfully!
)

echo.
echo ================================================
echo BUILD COMPLETE
echo ================================================
dir "dist\Stream247.exe"
echo.
echo Press any key to exit...
pause >nul