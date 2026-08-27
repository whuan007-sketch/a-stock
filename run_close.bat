@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py --mode close
) else (
  py -3 main.py --mode close
)
exit /b %errorlevel%
