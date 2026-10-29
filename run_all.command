#!/bin/bash
# macOS / Linux launcher: creates a virtual environment, installs packages, runs the pipeline.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python) || { echo "Python 3.12+ not found. Install it from python.org."; read -r -p "Press Enter"; exit 1; }
"$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" || { echo "Python 3.12 or newer is required (found $("$PY" --version))."; read -r -p "Press Enter"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv || { echo "Could not create virtual environment"; read -r -p "Press Enter"; exit 1; }
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt || { echo "Package install failed"; read -r -p "Press Enter"; exit 1; }
if ! python -c "import lightgbm" 2>/dev/null; then
  if [ "$(uname)" = "Darwin" ]; then
    echo "LightGBM needs the OpenMP library on macOS."
    if command -v brew >/dev/null; then brew install libomp; else echo "Install Homebrew (https://brew.sh), then run: brew install libomp"; read -r -p "Press Enter"; exit 1; fi
  fi
  python -c "import lightgbm" || { echo "LightGBM still cannot load."; read -r -p "Press Enter"; exit 1; }
fi
python run_all.py || { echo "Pipeline stopped with an error - see messages above."; read -r -p "Press Enter"; exit 1; }
read -r -p "Done. Press Enter to close."
