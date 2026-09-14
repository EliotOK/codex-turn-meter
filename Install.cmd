@echo off
python "%~dp0install.py"
if errorlevel 1 exit /b 1
pause
