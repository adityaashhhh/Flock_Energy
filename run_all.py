"""Run the whole pipeline end-to-end: download -> Section 1 -> Section 2 -> checks -> Section 3.

Each step runs as its own Python process, so memory is fully released between steps
(the pipeline then fits in ~8 GB RAM). Stops at the first step that fails.
"""
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
STEPS = ["download.py", "s1_clean_raw.py", "s2_wm_eda.py", "check_clock.py", "s3_model.py"]

for step in STEPS:
    print(f"\n========== {step} ==========", flush=True)
    result = subprocess.run([sys.executable, str(SRC / step)], cwd=SRC)
    if result.returncode != 0:
        sys.exit(f"Step {step} failed (exit code {result.returncode}). See the messages above.")
print("\nAll steps finished. Figures in figures/, tables in outputs/.")
