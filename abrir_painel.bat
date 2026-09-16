@echo off
cd /d "%~dp0dashboard"
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual do painel pela primeira vez...
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
start "" http://127.0.0.1:5151
.venv\Scripts\python.exe server.py
