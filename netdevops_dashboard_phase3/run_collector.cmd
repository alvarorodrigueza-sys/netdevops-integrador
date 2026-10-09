@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python -m netdevops_dashboard_phase3.app.collector --interval 10
