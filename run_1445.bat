@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py --mode 1445
) else (
  py -3 main.py --mode 1445
)
exit /b %errorlevel%
