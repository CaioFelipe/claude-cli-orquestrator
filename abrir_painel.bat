@echo off
cd /d "%~dp0dashboard"
start "" http://127.0.0.1:5151
python server.py
