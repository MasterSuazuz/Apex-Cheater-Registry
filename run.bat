@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo First run: installing dependencies, this takes a few minutes...
    python -m venv .venv
    if errorlevel 1 goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
exit /b 0

:error
echo.
echo Failed to install. Python 3.11+ from python.org is required
echo (check "Add python.exe to PATH" during installation).
pause
exit /b 1
