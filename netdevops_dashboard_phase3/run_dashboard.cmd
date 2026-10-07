@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
streamlit run netdevops_dashboard_phase3\app\dashboard.py
