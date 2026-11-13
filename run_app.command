#!/bin/bash
# Double-click launcher (macOS). First run creates a virtual environment and installs dependencies.
# Each launch checks GitHub for a newer release and applies it (set HOOD_NO_UPDATE=1 to skip).
cd "$(dirname "$0")"

# A .venv copied from another computer points at that computer's Python; rebuild it here.
if [ -d .venv ] && ! .venv/bin/python3 -c "import streamlit" >/dev/null 2>&1; then
  echo "Existing .venv is not usable on this computer - rebuilding it."
  rm -rf .venv
fi
if [ ! -d .venv ]; then
  PY=$(command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
  if [ -z "$PY" ]; then
    echo "python3 not found - install Python 3.10+ from https://www.python.org/downloads/ and run this again."
    read -p "Press Enter to close"; exit 1
  fi
  echo "Setting up (first run only, this takes a minute or two)..."
  "$PY" -m venv .venv || { echo "Could not create the virtual environment."; read -p "Press Enter to close"; exit 1; }
  .venv/bin/python3 -m pip install -q --upgrade pip
fi

if [ -z "$HOOD_NO_UPDATE" ]; then
  .venv/bin/python3 update.py
fi

# (Re)install requirements whenever requirements.txt changed since the last install.
if [ ! -f .venv/.requirements.installed ] || ! cmp -s requirements.txt .venv/.requirements.installed; then
  echo "Installing/updating dependencies..."
  .venv/bin/python3 -m pip install -q -r requirements.txt || { echo "Dependency install failed (check your internet connection)."; read -p "Press Enter to close"; exit 1; }
  cp requirements.txt .venv/.requirements.installed
fi

echo "Starting Glaucoma Progression Report v$(cat VERSION)"
.venv/bin/python3 -m streamlit run app.py
