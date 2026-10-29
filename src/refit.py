"""Shared helpers for the official CLEANED REFIT data (Sections 2 and 3)."""
import numpy as np
import pandas as pd

from config import (CLEAN_DIR, INTERIM, LOCAL_TZ, WM_COLUMN, WM_ON_W, WM_MERGE_GAP_MIN,
                    WM_MIN_DURATION_MIN, WM_MIN_ENERGY_WH, WM_MAX_DURATION_MIN)


def load_clean_1min(house: int) -> pd.DataFrame:
    """Aggregate + washing-machine power for one house at 1-minute resolution.

    - The cleaned files' Unix column is treated as UTC (the official cleaning
      corrected UK daylight saving); local time is added for hour-of-day analysis.
    - 1-minute value = mean of the ~8-second readings in that minute.
    - Minutes with no reading at all stay NaN (= missing), they are NOT filled.
    - `issues` = share of readings in the minute where the official 'Issues' flag
      says the sub-meters add up to more than the aggregate.
    - `wm_stuck` = washing-machine sub-meter frozen at one non-zero value for >= 60 min;
      those minutes are set to NaN in `wm` (see stuck_wm).
    Result is cached as parquet so later runs are fast.
    """
    cache = INTERIM / f"clean_house{house}_1min.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    col = WM_COLUMN[house]
    raw = pd.read_csv(CLEAN_DIR / f"CLEAN_House{house}.csv",
                      usecols=["Unix", "Aggregate", col, "Issues"])
    raw.index = pd.to_datetime(raw["Unix"], unit="s", utc=True)
    m = (raw[["Aggregate", col, "Issues"]].resample("1min").mean()
         .rename(columns={col: "wm", "Aggregate": "agg", "Issues": "issues"}))
    m.index.name = "ts_utc"
    m["ts_local"] = m.index.tz_convert(LOCAL_TZ)
    # stuck sub-meter -> washing-machine value treated as missing (flag kept for reporting)
    m["wm_stuck"] = stuck_wm(m["wm"])
    m.loc[m["wm_stuck"], "wm"] = np.nan
    m.to_parquet(cache)
    return m


def detect_cycles(wm: pd.Series, return_too_long: bool = False) -> pd.DataFrame:
    """Find washing-machine cycles in a 1-minute power series.

    Plain-language rule:
      1. a minute is 'on' if the machine draws more than WM_ON_W (20 W);
      2. 'on' minutes separated by a pause of <= WM_MERGE_GAP_MIN (10 min) belong to
         the same cycle (machines pause while soaking, draining, re-filling);
      3. keep the group only if it lasts >= 10 min AND uses >= 50 Wh - this drops
         door-lock blips and one-off spikes that are not a wash;
      4. groups longer than WM_MAX_DURATION_MIN (6 h) are not counted as one wash: on
         inspection they are several back-to-back loads merged together (mostly House 5)
         or a sub-meter stuck at one value (e.g. exactly 2,046 W for 12 h).
         They are returned separately when return_too_long=True.
    Missing minutes count as 'off' for grouping: a gap of <= 10 min inside a wash is
    bridged like a pause, a longer gap splits the wash in two.
    """
    v = wm.fillna(0).to_numpy()
    on_idx = np.flatnonzero(v > WM_ON_W)
    cols = ["start", "end", "duration_min", "energy_wh", "peak_w", "mean_power_w"]
    if len(on_idx) == 0:
        return pd.DataFrame(columns=cols)
    breaks = np.flatnonzero(np.diff(on_idx) - 1 > WM_MERGE_GAP_MIN)
    st = np.r_[on_idx[0], on_idx[breaks + 1]]
    en = np.r_[on_idx[breaks], on_idx[-1]]
    csum = np.r_[0, np.cumsum(v)]
    out = pd.DataFrame({
        "start": wm.index[st], "end": wm.index[en],
        "duration_min": en - st + 1,
        "energy_wh": (csum[en + 1] - csum[st]) / 60.0,     # W x minutes / 60 = Wh
        "peak_w": [v[a:b + 1].max() for a, b in zip(st, en)],
    })
    out["mean_power_w"] = out["energy_wh"] * 60 / out["duration_min"]   # over whole cycle incl. pauses
    keep = (out["duration_min"] >= WM_MIN_DURATION_MIN) & (out["energy_wh"] >= WM_MIN_ENERGY_WH)
    too_long = out["duration_min"] > WM_MAX_DURATION_MIN
    if return_too_long:
        return out[keep & too_long].reset_index(drop=True)
    return out[keep & ~too_long].reset_index(drop=True)


def stuck_wm(wm: pd.Series, min_minutes: int = 60) -> pd.Series:
    """True where the washing-machine sub-meter repeats the IDENTICAL non-zero value
    for >= 60 consecutive minutes. A running machine never holds the exact same watts
    that long, so these minutes are a stuck sensor and are not used as model labels."""
    same = wm.diff().eq(0) & (wm > WM_ON_W)
    grp = (same != same.shift()).cumsum()
    run = same.groupby(grp).transform("size").where(same, 0)
    stuck = same & (run >= min_minutes - 1)
    return stuck | stuck.shift(-1, fill_value=False)


def runs_of_true(mask: pd.Series) -> pd.Series:
    """Lengths of consecutive True runs."""
    grp = (mask != mask.shift()).cumsum()
    r = mask.groupby(grp).agg(["first", "size"])
    return r.loc[r["first"], "size"]
