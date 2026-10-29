"""Section 3 - estimate washing-machine power from the household aggregate (1-minute).

Approach (sequence-to-point idea, Zhang et al. 2018, with a tree model instead of a CNN):
  for every minute t, look at a window of the AGGREGATE signal around t
  (most features +-60 min, a few long summaries +-120 min) and predict washing-machine
  power AT t.
  Window summaries (levels, step changes, rolling max/min/std, "how many minutes
  near 2 kW", drum "agitation") are fed to LightGBM, a gradient-boosted decision-tree model.
  Variants (loss function, clock features, a two-stage on/off gate) are compared on the
  validation houses; the winner by validation F1 is evaluated once on the test houses.

Split by HOUSEHOLD (no house appears in more than one set):
  train = 5, 6, 7, 10, 16, 19   validation = 17, 20   test = 2, 15

Run:  python src/s3_model.py
Writes outputs/s3_metrics.csv, outputs/s3_validation_choice.csv, outputs/s3_feature_importance.csv,
       outputs/s3_cycle_level.csv, figures/s3_*.png, data/interim/s3_test_predictions.parquet
"""
import json
import warnings

import lightgbm as lgb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import FIG, OUT, INTERIM, TRAIN_HOUSES, VAL_HOUSES, TEST_HOUSES, WM_ON_W
from refit import load_clean_1min, detect_cycles

SEED = 0
WM_CLIP_W = 4000
warnings.filterwarnings("ignore", message=".*eval_set.*")   # LightGBM 4.7 API-rename notice


# ----------------------------------------------------------------------------- features
def make_features(m: pd.DataFrame) -> pd.DataFrame:
    """Features built ONLY from the aggregate signal (+ optional clock features).

    The 1-minute grid is regular, so shift(k) is exactly k minutes earlier/later even
    across missing minutes (missing stays NaN; LightGBM handles NaN natively).
    Windows are centred: the model may look up to 120 min before and after t. That is fine
    for after-the-fact disaggregation of meter data (not a real-time use case).
    """
    a = m["agg"].astype("float32")
    f = {"agg": a}
    for k in (1, 2, 3, 5, 10, 20, 30):
        f[f"lag{k}"] = a.shift(k)
        f[f"lead{k}"] = a.shift(-k)
    f["step_in"] = a - a.shift(1)                 # jump into this minute
    f["step_out"] = a.shift(-1) - a               # jump out of this minute
    big_step = (a.diff().abs() > 1000).astype("float32")
    near_2kw = ((a.diff(1).abs() > 1500) | (a > 1800)).astype("float32")
    for w in (5, 15, 31, 61, 121):
        r = a.rolling(w, center=True, min_periods=max(1, w // 2))
        f[f"mean{w}"] = r.mean()
        f[f"std{w}"] = r.std()
        f[f"max{w}"] = r.max()
        f[f"min{w}"] = r.min()
        f[f"above_min{w}"] = a - f[f"min{w}"]     # removes the house's base load
        f[f"n_bigstep{w}"] = big_step.rolling(w, center=True, min_periods=1).sum()
        f[f"n_high{w}"] = near_2kw.rolling(w, center=True, min_periods=1).sum()
    # "agitation": after heating, the drum turns back and forth for ~1 h, so the aggregate
    # flickers; a kettle is a short flat block. Mean |minute-to-minute change| captures it.
    absdiff = a.diff().abs()
    hi = a > 1800
    for w in (15, 31, 61, 121, 241):
        f[f"agitation{w}"] = absdiff.rolling(w, center=True, min_periods=1).mean()
        f[f"n_over1800_{w}"] = hi.astype("float32").rolling(w, center=True, min_periods=1).sum()
    run_id = (hi != hi.shift()).cumsum()
    f["high_block_len"] = hi.groupby(run_id).transform("size").where(hi, 0)   # minutes in this >1.8 kW block
    hour = m["ts_local"].dt.hour + m["ts_local"].dt.minute / 60
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    f["weekend"] = (m["ts_local"].dt.dayofweek >= 5).astype("float32")
    return pd.DataFrame(f, index=m.index).astype("float32")


CLOCK = ["hour_sin", "hour_cos", "weekend"]


def build(houses):
    """Stack houses. Keep minutes where aggregate and WM are measured and the official
    'Issues' flag is clear (sub-meters > aggregate -> REFIT readme says discard)."""
    parts = []
    for h in houses:
        m = load_clean_1min(h)
        X = make_features(m)
        keep = m["agg"].notna() & m["wm"].notna() & (m["issues"].fillna(0) == 0)
        X = X[keep]
        X["house"] = h
        X["y"] = m.loc[keep, "wm"].clip(0, WM_CLIP_W).astype("float32")
        X["ts_local"] = m.loc[keep, "ts_local"]
        parts.append(X)
    return pd.concat(parts)


# ----------------------------------------------------------------------------- metrics
def metrics(y, p, ts_local, house) -> dict:
    """MAE: average error in W per minute (standard NILM metric).
    SAE: |predicted total energy - true total energy| / true total energy.
    Daily energy MAE: average error in Wh per day (what a bill/insight would show).
    F1 (on/off at 20 W): did we get the 'is the machine running?' question right?
    MAE (on): error only in minutes where the machine really runs."""
    y, p = np.asarray(y), np.asarray(p)
    on_t, on_p = y > WM_ON_W, p > WM_ON_W
    tp = (on_t & on_p).sum()
    prec = tp / on_p.sum() if on_p.sum() else 0.0
    rec = tp / on_t.sum() if on_t.sum() else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    day = pd.DataFrame({"y": y, "p": p, "d": ts_local.dt.date.values, "h": np.asarray(house)})
    daily = day.groupby(["h", "d"])[["y", "p"]].sum() / 60          # Wh per day
    return {"MAE_W": np.abs(y - p).mean(),
            "MAE_on_W": np.abs(y - p)[on_t].mean(),
            "SAE": abs(p.sum() - y.sum()) / y.sum(),
            "daily_energy_MAE_Wh": (daily["y"] - daily["p"]).abs().mean(),
            "true_daily_energy_Wh": daily["y"].mean(),
            "precision_on": prec, "recall_on": rec, "F1_on": f1}


# ----------------------------------------------------------------------------- baselines
def profile_baseline(train: pd.DataFrame):
    """Average WM power for each hour-of-week in the TRAIN houses.
    Captures 'people wash on Saturday mornings' but ignores the aggregate."""
    how = train["ts_local"].dt.dayofweek * 24 + train["ts_local"].dt.hour
    prof = train.groupby(how.values)["y"].mean()
    return lambda df: prof.reindex(df["ts_local"].dt.dayofweek * 24 + df["ts_local"].dt.hour).to_numpy()


# ----------------------------------------------------------------------------- model
PARAMS = dict(objective="regression", learning_rate=0.05, num_leaves=63, min_child_samples=200,
              subsample=0.5, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
              n_estimators=3000, random_state=SEED, n_jobs=-1, verbose=-1)


def fit_regressor(train, val, feats, objective="regression", **extra):
    model = lgb.LGBMRegressor(**{**PARAMS, "objective": objective, **extra})
    # early stopping on the model's own training loss only (L2 / Huber / L1) on validation
    model.fit(train[feats], train["y"], eval_set=[(val[feats], val["y"])],
              callbacks=[lgb.early_stopping(100, first_metric_only=True, verbose=False)])
    return model


def fit_classifier(train, val, feats):
    """Stage 1 of the two-stage model: probability that the machine is running (> 20 W)."""
    model = lgb.LGBMClassifier(**{**PARAMS, "objective": "binary"})
    model.fit(train[feats], (train["y"] > WM_ON_W).astype(int),
              eval_set=[(val[feats], (val["y"] > WM_ON_W).astype(int))],
              callbacks=[lgb.early_stopping(100, first_metric_only=True, verbose=False)])
    return model


def predict(model, df, feats):
    return np.clip(model.predict(df[feats], num_iteration=model.best_iteration_), 0, WM_CLIP_W)


def p_on(model, df, feats):
    return model.predict_proba(df[feats], num_iteration=model.best_iteration_)[:, 1]


def main() -> None:
    train, val, test = build(TRAIN_HOUSES), build(VAL_HOUSES), build(TEST_HOUSES)
    all_feats = [c for c in train.columns if c not in ("house", "y", "ts_local")]
    agg_feats = [c for c in all_feats if c not in CLOCK]
    print({k: len(v) for k, v in [("train", train), ("val", val), ("test", test)]}, flush=True)

    # --- 1. compare model variants on the VALIDATION houses only ----------------------
    # Selection rule fixed in advance: highest validation F1 (is the machine running?),
    # because per-minute MAE rewards predicting 0 for a machine that is off ~95% of the time.
    variants, preds_val = {}, {}
    reg_agg = fit_regressor(train, val, agg_feats)
    variants["regressor L2, aggregate-only features"] = lambda d: predict(reg_agg, d, agg_feats)
    reg = fit_regressor(train, val, all_feats)
    variants["regressor L2, + clock features"] = lambda d: predict(reg, d, all_feats)
    reg_hub = fit_regressor(train, val, all_feats, objective="huber", alpha=200.0)
    variants["regressor Huber, + clock"] = lambda d: predict(reg_hub, d, all_feats)
    reg_l1 = fit_regressor(train, val, all_feats, objective="l1")
    variants["regressor L1, + clock"] = lambda d: predict(reg_l1, d, all_feats)
    clf = fit_classifier(train, val, all_feats)
    for t in (0.2, 0.3, 0.4, 0.5):
        variants[f"two-stage: P(on)>{t} x regressor"] = (
            lambda d, t=t: np.where(p_on(clf, d, all_feats) > t, predict(reg, d, all_feats), 0.0))
    choice = []
    for name, fn in variants.items():
        preds_val[name] = fn(val)
        choice.append({"variant": name, **metrics(val["y"], preds_val[name], val["ts_local"], val["house"])})
    choice = pd.DataFrame(choice).sort_values("F1_on", ascending=False)
    choice.to_csv(OUT / "s3_validation_choice.csv", index=False)
    best = choice.iloc[0]["variant"]
    print(choice.round(3).to_string(index=False), "\nchosen:", best, flush=True)

    # --- 2. evaluate on the untouched TEST houses ------------------------------------
    prof = profile_baseline(train)
    methods = {"always_off": lambda d: np.zeros(len(d)),
               "hour_of_week_profile": prof,
               "regressor L2, + clock features": variants["regressor L2, + clock features"]}
    methods[best] = variants[best]
    rows, test_preds = [], {}
    for split, df in [("validation", val), ("test", test)]:
        for name, fn in methods.items():
            p = fn(df)
            if split == "test":
                test_preds[name] = p
            for h in [None] + sorted(df["house"].unique()):
                sel = np.ones(len(df), bool) if h is None else (df["house"] == h).to_numpy()
                rows.append({"split": split, "house": "all" if h is None else h, "method": name,
                             **metrics(df["y"].to_numpy()[sel], p[sel], df["ts_local"][sel], df["house"][sel])})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "s3_metrics.csv", index=False)
    print(res[res.split == "test"].round(3).to_string(index=False), flush=True)

    imp = pd.DataFrame({"regressor_gain_pct": reg.booster_.feature_importance("gain"),
                        "classifier_gain_pct": clf.booster_.feature_importance("gain")}, index=all_feats)
    imp = (imp / imp.sum() * 100).round(2).sort_values("classifier_gain_pct", ascending=False)
    imp.to_csv(OUT / "s3_feature_importance.csv")

    out = test[["house", "ts_local", "y", "agg"]].copy()
    out["pred"] = test_preds[best]
    out["pred_regressor"] = test_preds["regressor L2, + clock features"]
    out["profile"] = test_preds["hour_of_week_profile"]
    out.to_parquet(INTERIM / "s3_test_predictions.parquet")

    cyc = cycle_level(out)
    cyc.to_csv(OUT / "s3_cycle_level.csv", index=False)
    print(cyc.to_string(index=False))
    plot_actual_vs_pred(out)
    plot_failures(out)
    plot_scatter_daily(out)
    with open(OUT / "s3_model_info.json", "w") as f:
        json.dump({"chosen_variant": best, "n_features": len(all_feats),
                   "regressor_best_iteration": int(reg.best_iteration_),
                   "classifier_best_iteration": int(clf.best_iteration_), "params": PARAMS,
                   "rows": {"train": len(train), "val": len(val), "test": len(test)}}, f, indent=2)


# ----------------------------------------------------------------------------- cycle level
def cycle_level(out: pd.DataFrame) -> pd.DataFrame:
    """Run the SAME cycle rule (Section 2) on true and predicted power.
    A true cycle counts as found if a predicted cycle overlaps it."""
    rows = []
    for h, d in out.groupby("house"):
        s = d.set_index(d["ts_local"])
        full = pd.date_range(s.index.min(), s.index.max(), freq="1min")
        t_c = detect_cycles(s["y"].reindex(full))
        p_c = detect_cycles(s["pred"].reindex(full))

        def overlaps(a, b):
            return np.array([((b["start"] <= r.end) & (b["end"] >= r.start)).any() for r in a.itertuples()])
        found = overlaps(t_c, p_c)
        real = overlaps(p_c, t_c)
        weeks = len(s) / 1440 / 7
        rows.append({"house": h, "true_cycles": len(t_c), "predicted_cycles": len(p_c),
                     "true_cycles_found_pct": round(100 * found.mean(), 1),
                     "predicted_cycles_that_are_real_pct": round(100 * real.mean(), 1),
                     "false_cycles_per_week": round((~real).sum() / weeks, 2),
                     "true_median_energy_wh": round(t_c["energy_wh"].median()),
                     "pred_median_energy_wh_of_found": round(p_c.loc[real, "energy_wh"].median())})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- plots
def plot_actual_vs_pred(out, house=2, days=3):
    d = out[out["house"] == house]
    # pick a stretch with several washes and complete data
    daily = d.groupby(d["ts_local"].dt.date)["y"].sum()
    start = pd.Timestamp(daily.rolling(days).sum().idxmax()) - pd.Timedelta(days=days - 1)
    w = d[(d["ts_local"].dt.tz_localize(None) >= start) &
          (d["ts_local"].dt.tz_localize(None) < start + pd.Timedelta(days=days))]
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(13, 6.5), sharex=True,
                                 gridspec_kw={"height_ratios": [1, 1.3]})
    a0.plot(w["ts_local"], w["agg"], color="0.5", lw=0.7)
    a0.set_ylabel("aggregate [W]")
    a0.set_title(f"Test house {house} (never seen in training) - {days} days")
    a1.plot(w["ts_local"], w["y"], color="k", lw=1, label="actual (sub-meter)")
    a1.plot(w["ts_local"], w["pred"], color="C1", lw=1, alpha=0.9, label="predicted from aggregate")
    a1.set_ylabel("washing machine [W]")
    a1.legend(loc="upper right")
    a1.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%a %H:%M", tz=w["ts_local"].dt.tz))
    fig.tight_layout()
    fig.savefig(FIG / "s3_actual_vs_predicted.png", dpi=120)
    plt.close(fig)


def plot_scatter_daily(out):
    daily = out.groupby(["house", out["ts_local"].dt.date])[["y", "pred"]].sum() / 60 / 1000
    fig, ax = plt.subplots(figsize=(5.5, 5.5))
    for h, d in daily.groupby(level=0):
        ax.scatter(d["y"], d["pred"], s=8, alpha=0.5, label=f"house {h}")
    lim = max(daily.max()) * 1.05
    ax.plot([0, lim], [0, lim], "k--", lw=0.8)
    ax.set_xlabel("actual WM energy per day [kWh]")
    ax.set_ylabel("predicted WM energy per day [kWh]")
    ax.set_title("Daily energy, test houses")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG / "s3_daily_energy_scatter.png", dpi=120)
    plt.close(fig)


def plot_failures(out):
    """Worst false alarm (model says WM runs, sub-meter says off) and worst miss."""
    out = out.reset_index(drop=True)          # timestamps repeat across houses
    out["err"] = out["pred"] - out["y"]
    roll = out.groupby("house")["err"].transform(lambda s: s.rolling(60, center=True, min_periods=1).sum())
    fa = out.loc[roll.idxmax()]
    miss = out.loc[roll.idxmin()]
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.5))
    for ax, row, title in [(axes[0], fa, "False alarm: predicted a wash that did not happen"),
                           (axes[1], miss, "Miss: real wash under-estimated")]:
        d = out[(out["house"] == row["house"]) &
                (out["ts_local"] >= row["ts_local"] - pd.Timedelta(hours=2)) &
                (out["ts_local"] <= row["ts_local"] + pd.Timedelta(hours=2))]
        ax.plot(d["ts_local"], d["agg"], color="0.6", lw=0.7, label="aggregate")
        ax.plot(d["ts_local"], d["y"], color="k", lw=1.1, label="actual WM")
        ax.plot(d["ts_local"], d["pred"], color="C1", lw=1.1, label="predicted WM")
        ax.set_title(f"House {int(row['house'])}, {row['ts_local']:%d %b %Y}: {title}", fontsize=10)
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M", tz=d["ts_local"].dt.tz))
        ax.set_ylabel("W")
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "s3_failure_cases.png", dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    main()
