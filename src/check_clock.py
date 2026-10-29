"""Cross-check: our Section-1 House 1 vs the OFFICIAL cleaned House 1, and a clock test.

Run after s1 and s2:  python src/check_clock.py
Writes:
  outputs/check_official_vs_ours_monthly.csv  monthly agreement (correlation, mean |diff|,
                                              best time shift in minutes) of the aggregate
  outputs/check_morning_rise.csv              time of the steepest morning power rise per
                                              season, for our House 1, official House 1 and
                                              the 10 washing-machine houses

Why: the household's morning "wake-up" rise happens at the same LOCAL time all year.
If a file's summer timestamps are 1 h off, its summer rise moves by ~1 h.
"""
import pandas as pd

from config import CLEAN_DIR, INTERIM, OUT, LOCAL_TZ, WM_COLUMN
from refit import load_clean_1min

SEASONS = {"summer 2014": ("2014-04-01", "2014-06-20"),
           "winter 2014/15": ("2014-11-01", "2015-02-28"),
           "summer 2015": ("2015-04-01", "2015-06-30")}


def morning_rise(s: pd.Series, start: str, end: str) -> str:
    """Local clock time (HH:MM) of the steepest rise in the average daily profile, 05:00-11:00."""
    x = s[start:end]
    loc = x.index.tz_convert(LOCAL_TZ)
    prof = x.groupby(loc.hour * 60 + loc.minute).mean().rolling(31, center=True).mean()
    m = int(prof.loc[300:660].diff().idxmax())
    return f"{m // 60:02d}:{m % 60:02d}"


def main() -> None:
    ours = pd.read_parquet(INTERIM / "house1_clean_1min.parquet")["Aggregate"]
    ours.index = pd.to_datetime(ours.index, utc=True).as_unit("s")
    raw = pd.read_csv(CLEAN_DIR / "CLEAN_House1.csv", usecols=["Unix", "Aggregate"])
    off = raw.set_index(pd.to_datetime(raw["Unix"], unit="s", utc=True))["Aggregate"].resample("1min").mean()

    rows = []
    for month, o in ours.groupby(ours.index.strftime("%Y-%m")):
        best = max(range(-120, 121, 60), key=lambda k: o.corr(off.shift(k).reindex(o.index)))
        j = pd.concat([o, off.reindex(o.index)], axis=1, keys=["ours", "official"]).dropna()
        if len(j) < 1440:
            continue
        rows.append({"month": month, "minutes_compared": len(j),
                     "corr": round(j["ours"].corr(j["official"]), 3),
                     "mean_abs_diff_W": round((j["ours"] - j["official"]).abs().mean(), 1),
                     "best_shift_min (official shifted by)": best,
                     "corr_at_best_shift": round(o.corr(off.shift(best).reindex(o.index)), 3)})
    monthly = pd.DataFrame(rows)
    both = pd.concat([ours, off.reindex(ours.index)], axis=1, keys=["ours", "official"]).dropna()
    monthly.loc[len(monthly)] = {"month": "ALL (total energy ratio ours/official)",
                                 "minutes_compared": len(both),
                                 "corr": round(both["ours"].sum() / both["official"].sum(), 4)}
    monthly.to_csv(OUT / "check_official_vs_ours_monthly.csv", index=False)

    rise = [{"series": "House 1 (ours, from raw)", **{k: morning_rise(ours, *v) for k, v in SEASONS.items()}},
            {"series": "House 1 (official cleaned)", **{k: morning_rise(off, *v) for k, v in SEASONS.items()}}]
    for h in sorted(WM_COLUMN):
        a = load_clean_1min(h)["agg"]
        rise.append({"series": f"House {h} (official cleaned)", **{k: morning_rise(a, *v) for k, v in SEASONS.items()}})
    r = pd.DataFrame(rise)
    r.to_csv(OUT / "check_morning_rise.csv", index=False)

    # sensitivity: do the evening-peak numbers (Recommendation Q1) change if the
    # suspect summer-2015 period (UK clocks forward on 2015-03-29) is left out?
    cut = pd.Timestamp("2015-03-29 01:00", tz="UTC")
    cyc = pd.read_csv(OUT / "s2_cycles.csv")
    cyc["start"] = pd.to_datetime(cyc["start"], utc=True)
    sens = []
    for label, keep in [("all data", lambda s: s.notna()), ("without summer 2015", lambda s: s < cut)]:
        e_pk, e_all, n_pk, n_all = 0.0, 0.0, 0, 0
        for h in sorted(WM_COLUMN):
            m = load_clean_1min(h)
            ok = m["wm"].notna() & m["agg"].notna() & keep(m.index.to_series())
            loc = m.loc[ok, "ts_local"]
            pk = (loc.dt.dayofweek < 5) & loc.dt.hour.between(16, 18)
            e_pk += m.loc[ok, "wm"][pk].sum()
            e_all += m.loc[ok, "wm"].sum()
            st = cyc.loc[(cyc["house"] == h) & keep(cyc["start"]), "start"].dt.tz_convert(LOCAL_TZ)
            n_pk += int(((st.dt.dayofweek < 5) & st.dt.hour.between(16, 18)).sum())
            n_all += len(st)
        sens.append({"data": label, "wm_energy_in_weekday_16_19_pct (pooled)": round(100 * e_pk / e_all, 1),
                     "cycles_starting_in_weekday_16_19_pct (pooled)": round(100 * n_pk / n_all, 1),
                     "cycles": n_all})
    pd.DataFrame(sens).to_csv(OUT / "check_peak_without_summer2015.csv", index=False)
    print(pd.DataFrame(sens).to_string(index=False))
    print(pd.DataFrame(rows).to_string(index=False))
    print(r.to_string(index=False))


if __name__ == "__main__":
    main()
