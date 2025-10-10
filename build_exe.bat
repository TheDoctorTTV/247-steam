@echo off
echo Building Stream247 executable...
echo.

REM Activate virtual environment
call ".venv\Scripts\activate.bat"

REM Clean previous build
if exist "build" rmdir /s /q "build"
if exist "dist\Stream247.exe" del "dist\Stream247.exe"

REM Build the executable
python -m PyInstaller --onefile --noconsole --icon=icon.ico --name=Stream247 --optimize=2 Stream247_GUI.py

echo.
if exist "dist\Stream247.exe" (
    echo ✓ Build completed successfully!
    echo ✓ Executable created: dist\Stream247.exe
    dir "dist\Stream247.exe"
) else (
    echo ✗ Build failed!
)

echo.
pause