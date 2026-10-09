@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python netdevops_flask_dashboard\app.py
