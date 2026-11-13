@echo off
cd /d "%~dp0"
if exist .venv (
  .venv\Scripts\python.exe -c "import streamlit" >nul 2>&1 || (
    echo Existing .venv is not usable on this computer - rebuilding it.
    rmdir /s /q .venv
  )
)
if not exist .venv (
  echo Setting up (first run only, this takes a minute or two)...
  python -m venv .venv || (echo Python not found - install Python 3.10+ from python.org & pause & exit /b 1)
  .venv\Scripts\python.exe -m pip install -q --upgrade pip
)
if not defined HOOD_NO_UPDATE .venv\Scripts\python.exe update.py
fc /b requirements.txt .venv\requirements.installed >nul 2>&1 || (
  echo Installing/updating dependencies...
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt || (echo Dependency install failed & pause & exit /b 1)
  copy /y requirements.txt .venv\requirements.installed >nul
)
set /p VER=<VERSION
echo Starting Glaucoma Progression Report v%VER%
.venv\Scripts\python.exe -m streamlit run app.py
