"""Section 1 - inspect and clean RAW REFIT data for House 1, resampled to 1 minute.

Run:  python src/s1_clean_raw.py
Writes:
  data/interim/house1_clean_1min.parquet   cleaned 1-minute table + quality flags
  outputs/s1_inspection.json               raw-data quality findings (before cleaning)
  outputs/s1_cleaning_log.json             what each cleaning step changed
  outputs/s1_gap_sensitivity.csv           how many minutes would be filled for other gap limits
  figures/s1_*.png                         before/after plots
"""
import json

import matplotlib
matplotlib.use("Agg")                     # draw to files, no screen needed
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import (RAW_DIR, INTERIM, FIG, OUT, RAW_HOUSE, H1_NAMES, LOCAL_TZ,
                    MAX_FILL_MIN, IAM_MAX_W, AGG_MAX_W, FROZEN_MIN)

CHANNELS = list(H1_NAMES.values())
PLOT_CH = ["Aggregate", "WashingMachine", "Fridge"]


# ----------------------------------------------------------------------------- helpers
def load_part(part: int) -> pd.DataFrame:
    df = pd.read_csv(RAW_DIR / f"RAW_House{RAW_HOUSE}_Part{part}.csv")
    df = df.rename(columns=H1_NAMES)
    df["part"] = part
    df["file_row"] = np.arange(len(df))     # remember original order
    return df


def run_lengths(mask: pd.Series) -> pd.Series:
    """Length of each consecutive run of True values in a boolean series."""
    grp = (mask != mask.shift()).cumsum()
    runs = mask.groupby(grp).agg(["first", "size"])
    return runs.loc[runs["first"], "size"]


def fill_short_gaps(s: pd.Series, max_len: int) -> tuple[pd.Series, pd.Series]:
    """Forward-fill ONLY gaps of <= max_len consecutive missing minutes.

    pandas' ffill(limit=n) would also fill the first n minutes of a long outage,
    which is exactly what we do not want, so we measure each gap first.
    Returns (filled series, boolean mask of minutes that were filled).
    """
    miss = s.isna()
    grp = (miss != miss.shift()).cumsum()
    gap_len = miss.groupby(grp).transform("size").where(miss, 0)
    short = miss & (gap_len <= max_len)
    filled = s.copy()
    filled[short] = s.ffill()[short]
    short &= filled.notna()                 # a gap at the very start has nothing to copy
    return filled, short


def inspect(df: pd.DataFrame, name: str) -> dict:
    """Quality checks on one raw file, in its original row order."""
    t_text = pd.to_datetime(df["Time"], errors="coerce")
    t_unix = pd.to_datetime(df["Unix"], unit="s")
    step = df["Unix"].diff()
    vals = df[CHANNELS]
    dup_ts = df["Unix"].duplicated(keep=False)
    dup_rows = df.duplicated(subset=["Unix"] + CHANNELS, keep=False)
    return {
        "file": name,
        "rows": int(len(df)),
        "first": str(t_unix.min()), "last": str(t_unix.max()),
        "time_text_unparseable": int(t_text.isna().sum()),
        "time_text_minus_unix_seconds": sorted(map(int, (t_text - t_unix).dt.total_seconds().unique())),
        "rows_out_of_order (time goes backwards)": int((step < 0).sum()),
        "largest_backward_jump_s": int(-step.min()) if (step < 0).any() else 0,
        "rows_sharing_a_timestamp": int(dup_ts.sum()),
        "exact_duplicate_rows (extra copies)": int(df.duplicated(subset=["Unix"] + CHANNELS).sum()),
        "same_timestamp_different_values_rows": int((dup_ts & ~dup_rows).sum()),
        "sampling_interval_s_quantiles": {f"p{int(q*100)}": float(step.quantile(q)) for q in (.01, .25, .5, .75, .99)},
        "longest_silence_hours": round(float(step.max()) / 3600, 1),
        "missing_NaN_per_channel": {c: int(v) for c, v in vals.isna().sum().items()},
        "zero_aggregate_rows": int((df["Aggregate"] == 0).sum()),
        "negative_values": int((vals < 0).sum().sum()),
        "max_per_channel_W": {c: float(v) for c, v in vals.max().items()},
        "aggregate_above_limit_rows": int((df["Aggregate"] > AGG_MAX_W).sum()),
        "appliance_above_limit_rows": {c: int((df[c] > IAM_MAX_W).sum()) for c in CHANNELS[1:]},
    }


# ----------------------------------------------------------------------------- pipeline
def main() -> None:
    p1, p2 = load_part(1), load_part(2)
    report = {"Part1": inspect(p1, "RAW_House1_Part1.csv"),
              "Part2": inspect(p2, "RAW_House1_Part2.csv")}
    log = {}

    # --- 1. Timestamps: the files store UK wall-clock time (clock jumps back 1 h in
    #        late October, skips 1 h in late March). Convert to UTC so time is continuous.
    for df in (p1, p2):
        local = pd.to_datetime(df["Unix"], unit="s")            # naive = wall clock
        df["ts"] = (local.dt.tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="NaT")
                         .dt.tz_convert("UTC"))
    log["rows_dropped_ambiguous_DST_hour"] = int(p1["ts"].isna().sum() + p2["ts"].isna().sum())
    p1, p2 = p1.dropna(subset=["ts"]), p2.dropna(subset=["ts"])

    # --- 2. Part1 writes 0 when the house meter was offline; 0 W for a whole house is
    #        impossible (fridge/standby never all off), so treat it as missing.
    log["part1_zero_aggregate_set_missing"] = int((p1["Aggregate"] == 0).sum())
    p1.loc[p1["Aggregate"] == 0, "Aggregate"] = np.nan

    # --- 3. Combine. Part1 and Part2 overlap (Jun-Oct 2014). Keep Part2 in the overlap,
    #        because it records missing sensors as NaN instead of a misleading 0.
    p2_start = p2["ts"].min()
    log["part1_rows_dropped_in_overlap"] = int((p1["ts"] >= p2_start).sum())
    df = pd.concat([p1[p1["ts"] < p2_start], p2], ignore_index=True)

    # --- 4. Impossible values -> missing (they are sensor error codes, e.g. 65535, 98301)
    bad_agg = (df["Aggregate"] > AGG_MAX_W) | (df["Aggregate"] < 0)
    log["aggregate_values_removed"] = int(bad_agg.sum())
    df.loc[bad_agg, "Aggregate"] = np.nan
    log["appliance_values_removed"] = {}
    for c in CHANNELS[1:]:
        bad = (df[c] > IAM_MAX_W) | (df[c] < 0)
        log["appliance_values_removed"][c] = int(bad.sum())
        df.loc[bad, c] = np.nan

    # --- 5. Sort + de-duplicate. Rule: rows with the same timestamp are merged; for each
    #        channel we keep the LAST non-missing value written (later poll = newer value).
    df = df.sort_values(["ts", "part", "file_row"], kind="stable")
    n_before = len(df)
    df = df.groupby("ts")[CHANNELS].last()        # .last() skips NaN per column
    log["rows_before_dedup"] = n_before
    log["rows_after_dedup"] = int(len(df))
    assert df.index.is_monotonic_increasing and not df.index.has_duplicates

    # --- 5b. Frozen logger: sometimes the logger keeps re-sending the SAME values for
    #         hours (e.g. 2015-02-08: aggregate stuck at exactly 12,505 W and washing
    #         machine at 2,256 W for ~2.7 h). A live whole-house meter always flickers by
    #         a few watts, so if consecutive aggregate readings are IDENTICAL for
    #         >= FROZEN_MIN minutes, that stretch is set missing. Detection uses the
    #         aggregate only; in the inspected episodes every channel froze together, so
    #         all channels are blanked. A run is broken by any silence > 2 min, so two
    #         equal readings either side of a gap are NOT treated as one frozen run.
    agg = df["Aggregate"].dropna()
    t = agg.index.to_series()
    new_run = agg.ne(agg.shift()) | (t.diff() > pd.Timedelta(minutes=2))
    run_id = new_run.cumsum()
    span = agg.index.to_series().groupby(run_id.values).agg(["min", "max"])
    frozen_runs = span[(span["max"] - span["min"]) >= pd.Timedelta(minutes=FROZEN_MIN)]
    frozen = np.zeros(len(df), dtype=bool)
    for a, b in frozen_runs.itertuples(index=False):
        frozen[df.index.searchsorted(a):df.index.searchsorted(b, side="right")] = True
    log["frozen_episodes"] = int(len(frozen_runs))
    log["frozen_hours_set_missing"] = round(float((frozen_runs["max"] - frozen_runs["min"]).sum()
                                                  / pd.Timedelta(hours=1)), 1)
    df.loc[frozen, CHANNELS] = np.nan

    # --- 6. Resample to 1 minute: mean power in each minute (mean W x 1 min = energy).
    m = df.resample("1min").mean()
    m.index.name = "ts_utc"
    log["minutes_in_period"] = int(len(m))
    log["minutes_without_any_reading_per_channel"] = {c: int(v) for c, v in m.isna().sum().items()}


    # gap-length sensitivity (aggregate) - lets the reader judge the 15-minute choice
    runs = run_lengths(m["Aggregate"].isna())
    pd.DataFrame([{"max_fill_min": k, "gaps_filled": int((runs <= k).sum()),
                   "minutes_filled": int(runs[runs <= k].sum()),
                   "gaps_left_as_outage": int((runs > k).sum()),
                   "minutes_left_as_outage": int(runs[runs > k].sum())}
                  for k in (2, 5, 10, 15, 30, 60)]).to_csv(OUT / "s1_gap_sensitivity.csv", index=False)

    # --- 7. Fill short gaps, flag long ones.  flag: 0 = measured, 1 = short gap filled,
    #        2 = long outage (left missing on purpose)
    clean = pd.DataFrame(index=m.index)
    log["minutes_filled_per_channel"] = {}
    for c in CHANNELS:
        filled, was_filled = fill_short_gaps(m[c], MAX_FILL_MIN)
        clean[c] = filled
        clean[f"{c}_flag"] = np.select([was_filled, filled.isna()], [1, 2], 0).astype("int8")
        log["minutes_filled_per_channel"][c] = int(was_filled.sum())
    clean["outage"] = clean["Aggregate_flag"] == 2
    clean["sub_meter_sum_gt_aggregate"] = clean[CHANNELS[1:]].sum(axis=1, min_count=1) > clean["Aggregate"]
    clean["ts_local"] = clean.index.tz_convert(LOCAL_TZ)

    out_runs = run_lengths(clean["outage"])
    log["long_outages_aggregate"] = {
        "count": int(len(out_runs)), "total_minutes": int(out_runs.sum()),
        "share_of_period_pct": round(100 * out_runs.sum() / len(clean), 2),
        "longest_days": round(out_runs.max() / 1440, 1)}
    log["minutes_sub_meter_sum_gt_aggregate"] = int(clean["sub_meter_sum_gt_aggregate"].sum())
    clean.to_parquet(INTERIM / "house1_clean_1min.parquet")

    # outage list for DATA.md
    o = clean["outage"]
    starts = clean.index[o & ~o.shift(fill_value=False)]
    ends = clean.index[o & ~o.shift(-1, fill_value=False)]
    pd.DataFrame({"start_utc": starts, "end_utc": ends,
                  "hours": ((ends - starts).total_seconds() / 3600 + 1 / 60).round(1)}
                 ).sort_values("hours", ascending=False).to_csv(OUT / "s1_outages.csv", index=False)

    with open(OUT / "s1_inspection.json", "w") as f:
        json.dump(report, f, indent=2)
    with open(OUT / "s1_cleaning_log.json", "w") as f:
        json.dump(log, f, indent=2)

    # ------------------------------------------------------------------------- plots
    raw_naive = pd.concat([load_part(1), load_part(2)]).rename(columns=H1_NAMES)
    raw_naive["ts"] = pd.to_datetime(raw_naive["Unix"], unit="s")   # as stored, no fixes
    # window chosen because it contains washing-machine use, impossible spikes and short gaps
    plot_window(raw_naive, clean, "2013-12-19", "2013-12-21", "s1_before_after_window.png")
    # window with the frozen-logger episode
    plot_window(raw_naive, clean, "2015-02-08 09:00", "2015-02-08 19:00", "s1_before_after_frozen.png")
    plot_overview(raw_naive, clean, "s1_before_after_overview.png")
    print(json.dumps(log, indent=2))


def plot_window(raw, clean, start, end, fname):
    """Raw 8-second readings (grey) vs cleaned 1-minute series (colour) for a few days."""
    r = raw[(raw["ts"] >= start) & (raw["ts"] < end)].sort_values("ts")
    c = clean.loc[start:end]
    c_local = c.set_index(c["ts_local"].dt.tz_localize(None))      # same clock as raw file
    fig, axes = plt.subplots(len(PLOT_CH), 1, figsize=(12, 9), sharex=True)
    for ax, ch in zip(axes, PLOT_CH):
        lim = AGG_MAX_W if ch == "Aggregate" else IAM_MAX_W
        top = min(lim, max(c[ch].max() * 1.15, 100))
        ok = r[ch] <= lim
        ax.plot(r.loc[ok, "ts"], r.loc[ok, ch], color="0.7", lw=0.6, label="before: raw readings (~8 s)")
        ax.plot(c_local.index, c_local[ch], color="C0", lw=1, label="after: cleaned 1-min mean")
        bad = r[~ok & r[ch].notna()]
        if len(bad):
            ax.scatter(bad["ts"], np.full(len(bad), top * 0.97), marker="v", color="red", s=25,
                       label=f"impossible value removed (> {lim:,} W)")
        gaps = c_local[f"{ch}_flag"] == 1
        if gaps.any():
            ax.scatter(c_local.index[gaps], np.zeros(gaps.sum()), marker="|", color="orange", s=40,
                       label="short gap filled")
        long_gap = c_local[f"{ch}_flag"] == 2
        if long_gap.any():
            ax.fill_between(c_local.index, 0, 1, where=long_gap, color="red", alpha=0.12,
                            transform=ax.get_xaxis_transform(), label="flagged outage / frozen (left missing)")
        ax.set_ylim(0, top)
        ax.set_ylabel(f"{ch}\n[W]")
        ax.legend(loc="upper right", fontsize=7)
    axes[0].set_title(f"House 1 raw vs cleaned, {start} to {end} (UK local time)")
    fig.tight_layout()
    fig.savefig(FIG / fname, dpi=120)
    plt.close(fig)


def plot_overview(raw, clean, fname):
    """Whole study, like-for-like: daily MEAN before cleaning vs daily MEAN after (same
    y-axis per row), plus the daily MAX of raw readings (log scale) to show error codes."""
    fig, axes = plt.subplots(len(PLOT_CH), 3, figsize=(18, 9), sharex=True)
    r = raw.set_index("ts")[PLOT_CH]
    raw_daily_mean = r.resample("1D").mean()
    raw_daily_max = r.resample("1D").max()
    clean_local = clean.set_index(clean["ts_local"].dt.tz_localize(None))
    clean_daily = clean_local[PLOT_CH].resample("1D").mean()
    outage_days = clean_local["outage"].resample("1D").mean()
    for i, ch in enumerate(PLOT_CH):
        a, b, c = axes[i]
        a.plot(raw_daily_mean.index, raw_daily_mean[ch], color="0.4", lw=0.8)
        b.plot(clean_daily.index, clean_daily[ch], color="C0", lw=0.8)
        b.fill_between(outage_days.index, 0, 1, where=outage_days > 0.5, color="red", alpha=0.15,
                       transform=b.get_xaxis_transform(), step="mid")
        top = np.nanmax([raw_daily_mean[ch].max(), clean_daily[ch].max()]) * 1.05
        a.set_ylim(0, top)
        b.set_ylim(0, top)
        a.set_ylabel(f"{ch}\n[W]")
        c.plot(raw_daily_max.index, raw_daily_max[ch], color="0.4", lw=0.8)
        c.set_yscale("log")
        c.axhline(AGG_MAX_W if ch == "Aggregate" else IAM_MAX_W, color="red", ls="--", lw=0.8)
    axes[0, 0].set_title("BEFORE: daily mean of raw readings, as stored")
    axes[0, 1].set_title("AFTER: daily mean of cleaned 1-min data\nred shading = flagged outage (>50% of day)")
    axes[0, 2].set_title("BEFORE: daily max of raw readings (log)\nred dashed = physical limit used")
    fig.tight_layout()
    fig.savefig(FIG / fname, dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    main()
