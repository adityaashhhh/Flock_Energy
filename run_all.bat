@echo off
REM Windows launcher: creates a virtual environment, installs packages, runs the pipeline.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.12+ not found. Install it from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" || (echo Python 3.12 or newer is required. & python --version & pause & exit /b 1)
if not exist .venv ( python -m venv .venv || (echo Could not create virtual environment & pause & exit /b 1) )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || (echo Package install failed & pause & exit /b 1)
python -c "import lightgbm" 2>nul || (echo LightGBM could not load. Install the Microsoft Visual C++ Redistributable: https://aka.ms/vs/17/release/vc_redist.x64.exe & pause & exit /b 1)
python run_all.py || (echo Pipeline stopped with an error - see messages above. & pause & exit /b 1)
pause
