@echo off
cd /d C:\lab-agent
call .venv\Scripts\activate.bat
python netdevops_flask_dashboard_v5\app.py
