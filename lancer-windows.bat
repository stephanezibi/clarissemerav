@echo off
REM Lancement local sous Windows (test) - double-cliquer
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt
if not exist .env copy .env.example .env
start "" http://localhost:8000
.venv\Scripts\python run.py
