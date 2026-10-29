"""Section 2 - exploratory analysis of washing-machine use (official cleaned REFIT data).

Run:  python src/s2_wm_eda.py
Writes:
  outputs/s2_availability.csv   per-house coverage, missing data, off time, cycle counts
  outputs/s2_cycles.csv         every detected cycle
  outputs/s2_cycle_stats.csv    per-house medians of duration / peak / energy
  figures/s2_*.png              the required plots
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import FIG, OUT, WM_COLUMN, WM_ON_W, LOCAL_TZ
from refit import load_clean_1min, detect_cycles, runs_of_true

HOUSES = sorted(WM_COLUMN)
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def availability(h: int, m: pd.DataFrame, cyc: pd.DataFrame) -> dict:
    miss = m["agg"].isna() | m["wm"].isna()
    gaps = runs_of_true(miss)
    measured = m.loc[~miss, "wm"]
    days = len(m) / 1440
    return {
        "house": h, "wm_channel": WM_COLUMN[h],
        "first_utc": m.index[0].strftime("%Y-%m-%d"), "last_utc": m.index[-1].strftime("%Y-%m-%d"),
        "span_days": round(days, 1),
        "missing_minutes_pct": round(100 * miss.mean(), 1),
        "missing_gaps_>=1h": int((gaps >= 60).sum()),
        "longest_gap_days": round(gaps.max() / 1440, 1) if len(gaps) else 0,
        "issues_flag_minutes_pct": round(100 * (m["issues"] > 0).sum() / m["agg"].notna().sum(), 2),
        "wm_stuck_minutes_removed": int(m["wm_stuck"].sum()),
        "groups_over_6h_not_counted": int(len(detect_cycles(m["wm"], return_too_long=True))),
        "wm_exactly_zero_pct": round(100 * (measured == 0).mean(), 1),
        f"wm_off_<=_{WM_ON_W}W_pct": round(100 * (measured <= WM_ON_W).mean(), 1),
        "cycles_detected": int(len(cyc)),
        "cycles_per_week": round(len(cyc) / ((~miss).sum() / 1440 / 7), 2),
        "wm_share_of_house_energy_pct": round(100 * m.loc[~miss, "wm"].sum() / m.loc[~miss, "agg"].sum(), 2),
    }


def main() -> None:
    data, cycles, avail = {}, [], []
    for h in HOUSES:
        m = load_clean_1min(h)
        c = detect_cycles(m["wm"])
        c["house"] = h
        c["start_local"] = c["start"].dt.tz_convert(LOCAL_TZ)
        data[h] = m
        cycles.append(c)
        avail.append(availability(h, m, c))
        print(f"house {h}: {len(c)} cycles")
    cyc = pd.concat(cycles, ignore_index=True)
    av = pd.DataFrame(avail)
    av.to_csv(OUT / "s2_availability.csv", index=False)
    cyc.to_csv(OUT / "s2_cycles.csv", index=False)
    stats = cyc.groupby("house").agg(
        cycles=("energy_wh", "size"),
        duration_median_min=("duration_min", "median"),
        duration_iqr_min=("duration_min", lambda s: s.quantile(.75) - s.quantile(.25)),
        peak_median_w=("peak_w", "median"),
        energy_median_wh=("energy_wh", "median"),
        energy_iqr_wh=("energy_wh", lambda s: s.quantile(.75) - s.quantile(.25)),
    ).round(0)
    stats.to_csv(OUT / "s2_cycle_stats.csv")
    print(av.to_string(index=False))
    print(stats.to_string())

    # evening-peak exposure (used in Recommendation.md): weekday 16:00-19:00 UK time
    peak_rows = []
    for h, m in data.items():
        ok = m["wm"].notna() & m["agg"].notna()
        loc = m.loc[ok, "ts_local"]
        pk = (loc.dt.dayofweek < 5) & loc.dt.hour.between(16, 18)
        wm, agg = m.loc[ok, "wm"], m.loc[ok, "agg"]
        st = cyc.loc[cyc["house"] == h, "start_local"]
        peak_rows.append({"house": h,
                          "wm_energy_in_peak_pct": round(100 * wm[pk].sum() / wm.sum(), 1),
                          "wm_share_of_house_peak_energy_pct": round(100 * wm[pk].sum() / agg[pk].sum(), 2),
                          "cycles_starting_in_peak_pct": round(100 * ((st.dt.dayofweek < 5) & st.dt.hour.between(16, 18)).mean(), 1),
                          "cycles_starting_21_to_06_pct": round(100 * ((st.dt.hour >= 21) | (st.dt.hour < 6)).mean(), 1),
                          "wm_kwh_per_year": round(wm.sum() / 60 / 1000 / (ok.sum() / 1440) * 365)})
    peak = pd.DataFrame(peak_rows)
    peak.loc[len(peak)] = ["mean"] + peak.drop(columns="house").mean().round(2).tolist()
    peak.to_csv(OUT / "s2_peak_exposure.csv", index=False)
    print(peak.to_string(index=False))

    plot_trace(data, cyc)
    plot_cycles(data, cyc)
    plot_patterns(cyc)
    plot_cross_house(cyc)


# ----------------------------------------------------------------------------- plots
def pick_typical_day(data, cyc, h):
    """A day with exactly one cycle whose energy is closest to that house's median,
    and no missing minutes - i.e. an ordinary, complete day."""
    c = cyc[cyc["house"] == h].copy()
    c["day"] = c["start_local"].dt.normalize()
    one = c.groupby("day").filter(lambda g: len(g) == 1)
    one = one.iloc[(one["energy_wh"] - c["energy_wh"].median()).abs().argsort()]
    m = data[h]
    for day in one["day"]:
        d = m[(m["ts_local"] >= day) & (m["ts_local"] < day + pd.Timedelta(days=1))]
        if len(d) >= 1380 and d[["agg", "wm"]].notna().all(axis=None):
            return day, d
    raise RuntimeError("no complete day found")


def plot_trace(data, cyc, h=16):
    day, d = pick_typical_day(data, cyc, h)
    t = d["ts_local"]
    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.plot(t, d["agg"], color="0.55", lw=0.8, label="household aggregate")
    ax.fill_between(t, 0, d["wm"], color="C0", alpha=0.6, label="washing machine (sub-meter)")
    ax.set_ylabel("Power [W]")
    ax.set_title(f"House {h}, {day:%a %d %b %Y} - one ordinary day (UK local time)")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M", tz=t.dt.tz))
    fig.tight_layout()
    fig.savefig(FIG / "s2_trace_24h.png", dpi=120)
    plt.close(fig)


def plot_cycles(data, cyc, houses=(5, 7, 16, 19)):
    """One detected cycle per house: the median-energy cycle, with the 20 W rule drawn."""
    fig, axes = plt.subplots(2, 2, figsize=(12, 7))
    for ax, h in zip(axes.flat, houses):
        c = cyc[cyc["house"] == h]
        row = c.iloc[(c["energy_wh"] - c["energy_wh"].median()).abs().argsort().iloc[0]]
        pad = pd.Timedelta(minutes=20)
        d = data[h].loc[row["start"] - pad: row["end"] + pad]
        mins = (d.index - row["start"]).total_seconds() / 60
        ax.plot(mins, d["agg"], color="0.6", lw=0.8, label="aggregate")
        ax.plot(mins, d["wm"], color="C0", lw=1.2, label="washing machine")
        ax.axhline(WM_ON_W, color="red", ls=":", lw=1, label=f"on threshold {WM_ON_W} W")
        ax.axvspan(0, row["duration_min"], color="C2", alpha=0.12, label="detected cycle")
        ax.set_title(f"House {h}: {row['duration_min']} min, peak {row['peak_w']:.0f} W, "
                     f"{row['energy_wh']:.0f} Wh", fontsize=10)
        ax.set_xlabel("minutes from cycle start")
        ax.set_ylabel("W")
    axes.flat[0].legend(fontsize=8)
    fig.suptitle("Detected washing-machine cycles (median-energy cycle of each house)")
    fig.tight_layout()
    fig.savefig(FIG / "s2_detected_cycles.png", dpi=120)
    plt.close(fig)


def plot_patterns(cyc):
    hour = pd.crosstab(cyc["house"], cyc["start_local"].dt.hour, normalize="index") * 100
    dow = pd.crosstab(cyc["house"], cyc["start_local"].dt.dayofweek, normalize="index") * 100
    fig, (a, b) = plt.subplots(1, 2, figsize=(14, 4.8), gridspec_kw={"width_ratios": [2.2, 1]})
    im = a.imshow(hour.reindex(columns=range(24), fill_value=0), aspect="auto", cmap="Blues")
    a.set_xticks(range(24)); a.set_yticks(range(len(hour))); a.set_yticklabels([f"H{h}" for h in hour.index])
    a.set_xlabel("start hour (UK local)"); a.set_title("Share of a house's cycles starting in each hour [%]")
    fig.colorbar(im, ax=a, fraction=0.03)
    im2 = b.imshow(dow.reindex(columns=range(7), fill_value=0), aspect="auto", cmap="Blues")
    b.set_xticks(range(7)); b.set_xticklabels(DOW); b.set_yticks(range(len(dow)))
    b.set_yticklabels([f"H{h}" for h in dow.index]); b.set_title("... on each weekday [%]")
    fig.colorbar(im2, ax=b, fraction=0.05)
    fig.tight_layout()
    fig.savefig(FIG / "s2_usage_hour_dow.png", dpi=120)
    plt.close(fig)
    hour.round(1).to_csv(OUT / "s2_start_hour_pct.csv")
    dow.round(1).rename(columns=dict(enumerate(DOW))).to_csv(OUT / "s2_weekday_pct.csv")


def plot_cross_house(cyc):
    houses = sorted(cyc["house"].unique())
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for ax, col, lab in zip(axes, ["duration_min", "energy_wh", "peak_w"],
                            ["cycle duration [min]", "energy per cycle [Wh]", "peak power [W]"]):
        ax.boxplot([cyc.loc[cyc["house"] == h, col] for h in houses], showfliers=False)
        ax.set_xticks(range(1, len(houses) + 1), [f"H{h}" for h in houses])
        ax.set_title(lab)
    fig.suptitle("Cycle characteristics across houses (boxes = middle 50%, line = median)")
    fig.tight_layout()
    fig.savefig(FIG / "s2_cross_house.png", dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    main()
