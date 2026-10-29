"""Shared paths and settings used by every step of the pipeline.

Keeping the data-handling rules and the house split in one file means a reviewer can
see (and change) those decisions without hunting through the code. Model feature
thresholds (e.g. the 1.8 kW "heater" level) live next to the features in s3_model.py.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
CLEAN_DIR = DATA / "clean"
INTERIM = DATA / "interim"          # our own processed files (parquet)
FIG = ROOT / "figures"
OUT = ROOT / "outputs"              # tables / metrics written by the scripts

# Official download links (University of Strathclyde file server).
# The pureportal.strath.ac.uk links in the brief sit behind a browser check;
# the pure.strath.ac.uk/ws/portalfiles links below serve the same files directly.
URLS = {
    "REFIT_RAW_081116.7z": "https://pure.strath.ac.uk/ws/portalfiles/portal/62090177/REFIT_RAW_081116.7z",
    "CLEAN_REFIT_081116.7z": "https://pure.strath.ac.uk/ws/portalfiles/portal/62090184/CLEAN_REFIT_081116.7z",
    "CLEAN_READ_ME_081116.txt": "https://pure.strath.ac.uk/ws/portalfiles/portal/62090183/CLEAN_READ_ME_081116.txt",
}

# Links exactly as given in the assignment brief (tried first).
BRIEF_URLS = {
    "REFIT_RAW_081116.7z": "https://pureportal.strath.ac.uk/files/62090177/REFIT_RAW_081116.7z",
    "CLEAN_REFIT_081116.7z": "https://pureportal.strath.ac.uk/files/62090184/CLEAN_REFIT_081116.7z",
    "CLEAN_READ_ME_081116.txt": "https://pure.strath.ac.uk/ws/portalfiles/portal/62090183/CLEAN_READ_ME_081116.txt",
}
# Size (bytes) and SHA-256 of the files we used - download.py checks them, so anyone can
# confirm they have the identical official archives.
EXPECTED = {
    "REFIT_RAW_081116.7z": (698108465, "f294fe673532220dc527b5e7d7a25e99568bd24acf9fb5be0a0def14af64b2cf"),
    "CLEAN_REFIT_081116.7z": (514265481, "4dc4dde763da1c843e9d565fc68f1a63752d858980615635994d7f701fdfcc4b"),
    "CLEAN_READ_ME_081116.txt": (12020, "c5f5236fd5a7b29771913480fcef6e9be8a85d07511939c5a35160b7afc97bfb"),
}

LOCAL_TZ = "Europe/London"          # REFIT timestamps are UK wall-clock time

# ---------------- Section 1: raw cleaning rules ----------------
RAW_HOUSE = 1
MAX_FILL_MIN = 15        # gaps of <= 15 one-minute slots are forward-filled; longer = flagged outage (natural break in gap lengths, see outputs/s1_gap_sensitivity.csv)
IAM_MAX_W = 4000         # appliance plug limit (13 A socket ~3.1 kW; 4 kW = same cut-off as official REFIT cleaning)
AGG_MAX_W = 23000        # whole-house limit (~100 A main fuse x 230 V)
FROZEN_MIN = 30          # aggregate 1-min mean identical for >= 30 measured minutes = frozen logger

# House 1 channel names (from CLEAN_READ_ME_081116.txt)
H1_NAMES = {
    "Aggregate": "Aggregate", "Appliance1": "Fridge", "Appliance2": "Freezer1",
    "Appliance3": "Freezer2", "Appliance4": "WasherDryer", "Appliance5": "WashingMachine",
    "Appliance6": "Dishwasher", "Appliance7": "Computer", "Appliance8": "TV",
    "Appliance9": "ElectricHeater",
}

# ---------------- Sections 2-3: washing machine ----------------
# Washing-machine column per house (from CLEAN_READ_ME_081116.txt).
# 19 of 20 houses list a washing machine. We keep the 10 where the signal is unambiguous:
#   excluded 3, 11, 21  - aggregate affected by solar panels (REFIT paper)
#   excluded 1, 8, 9, 18 - also own a washer-dryer (second, near-identical load)
#   excluded 4          - two washing machines
#   excluded 13         - washing machine changed on 25 Mar 2015
#   (12 has no washing machine)
WM_COLUMN = {2: "Appliance2", 5: "Appliance3", 6: "Appliance2", 7: "Appliance5",
             10: "Appliance5", 15: "Appliance3", 16: "Appliance5", 17: "Appliance4",
             19: "Appliance2", 20: "Appliance4"}

# Active-cycle rule (explained in RESULTS.md)
WM_ON_W = 20             # minute counts as "drawing power" if WM mean power > 20 W (standby is < 5 W)
WM_MERGE_GAP_MIN = 10    # pauses (soak / drain) of up to 10 min stay inside the same cycle
WM_MIN_DURATION_MIN = 10 # a real wash lasts >= 10 min ...
WM_MIN_ENERGY_WH = 50    # ... and uses >= 50 Wh (drops door-lock blips, single spikes)
WM_MAX_DURATION_MIN = 360  # > 6 h is not one wash: a stuck sub-meter (flagged, excluded)

# Model split (by household -> no leakage between train and test)
TRAIN_HOUSES = [5, 6, 7, 10, 16, 19]
VAL_HOUSES = [17, 20]
TEST_HOUSES = [2, 15]

for p in (RAW_DIR, CLEAN_DIR, INTERIM, FIG, OUT):
    p.mkdir(parents=True, exist_ok=True)
