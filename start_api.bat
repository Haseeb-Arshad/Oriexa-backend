@echo off
cd /d F:\Oriexa\oriexa-api
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
