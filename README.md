# REFIT: data cleaning and washing-machine disaggregation

This is a take-home assignment for Flock Energy. It is a reproducible pipeline that:

1. **Cleans raw REFIT data for House 1** and resamples it to 1 minute (Section 1).
2. **Explores washing-machine use** in 10 homes from the official cleaned data (Section 2).
3. **Estimates washing-machine power from the whole-house signal** and compares the model against two baselines (Section 3).
4. Discusses **practical implications** for a utility (Section 4, in `Recommendation.md`).

| Document | What is in it |
|---|---|
| `DATA.md` | Raw-data coverage and quality findings, every cleaning step and its effect, gap and outlier decisions, remaining concerns |
| `RESULTS.md` | Washing-machine EDA, plots, model results, validation design, failure analysis |
| `Recommendation.md` | One-page utility summary, plus the Section 4 answers |

---

## How to run

**What you need**
- Python 3.12 or newer (the pinned numpy needs it)
- About 7 GB of free disk space (the archives are 1.2 GB; the extracted CSVs are about 4.2 GB; the Python environment is about 1 GB)
- About 8 GB of RAM
- macOS only: LightGBM needs OpenMP. Install it with `brew install libomp`; the launcher tries this for you.
- Windows only: if LightGBM fails to load, install the Microsoft Visual C++ Redistributable. The launcher prints the link.

**One click**
- Windows: double-click `run_all.bat`
- macOS: double-click `run_all.command`

Both launchers create a private Python environment (`.venv`), install the exact package versions from `requirements.txt`, and run everything.

**Manually**

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run_all.py          # all steps, each in its own process (~20-40 min); or one at a time:
python src/download.py     # downloads + extracts only the files used (skips if present)
python src/s1_clean_raw.py # Section 1  (~1 min)
python src/s2_wm_eda.py    # Section 2  (~1 min)
python src/check_clock.py  # cross-check vs official House 1 + clock-offset test (~1 min)
python src/s3_model.py     # Section 3  (~20-30 min on 2 CPU cores: 6 model fits)
```

**Output locations**
- Figures go to `figures/`.
- Tables and metrics go to `outputs/` (CSV and JSON).
- Cleaned and intermediate data go to `data/interim/`. This folder is not committed to git; it is rebuilt by the scripts.

**Data download note.** These are exactly the archives named in the brief: `REFIT_RAW_081116.7z`, `CLEAN_REFIT_081116.7z` and `CLEAN_READ_ME_081116.txt`. `src/download.py` tries the brief's `pureportal.strath.ac.uk/files/...` link first. That link sits behind a browser check (Cloudflare) that blocks scripts, so the script falls back to the same file ID on the university's direct file server, `pure.strath.ac.uk/ws/portalfiles/portal/<id>/...`. Every file's **size and SHA-256 checksum are verified** against `config.EXPECTED`, so you can confirm the files are identical.

---

## Repository layout

```
run_all.py / run_all.bat / run_all.command   end-to-end runners (any OS)
requirements.txt                             pinned package versions
src/config.py        data-handling rules, cycle rule and house split in one place
src/download.py      download + selective extraction of the .7z archives
src/s1_clean_raw.py  Section 1: inspection + cleaning of raw House 1
src/refit.py         shared loader for cleaned data + the washing-machine cycle detector
src/check_clock.py   cross-check of Section 1 vs the official cleaned House 1; clock-offset test
src/s2_wm_eda.py     Section 2: availability, cycles, usage patterns, plots
src/s3_model.py      Section 3: features, baselines, LightGBM model, metrics, plots
figures/  outputs/   results (committed so they can be read without re-running)
```

---

## Tech stack, and why

| Tool | Used for | Why this one |
|---|---|---|
| **Python** | everything | The standard language for data work; every tool needed exists for it |
| **pandas** | tables, time series | Built-in time-zone handling (the UK clock change matters here), de-duplication, resampling to 1 minute, gap handling |
| **NumPy** | fast array maths | Cycle detection and metrics on millions of minutes |
| **matplotlib** | plots | Static PNGs that display on GitHub. Interactive plots (e.g. Plotly) do not render on GitHub. |
| **pyarrow / Parquet** | intermediate files | About 10× faster to load than CSV, and it keeps data types and time zones intact |
| **py7zr** | opening `.7z` archives | Pure Python, so no separate 7-Zip install is needed on Windows or Mac |
| **LightGBM** | the model | See "Modelling choice" below |
| **scikit-learn** | required by LightGBM's model interface | LightGBM's `LGBMRegressor` / `LGBMClassifier` are built on it |

We deliberately chose plain scripts over notebooks. They run top to bottom with one command, cannot depend on cells being run out of order, and diff cleanly in git.

---

## Key assumptions and preprocessing decisions

These are summarised here; full detail and counts are in `DATA.md`.

- **Time.** Raw REFIT timestamps are UK wall-clock time; the clock repeats an hour in October and skips one in March. We convert to UTC for processing and to UK local time for hour-of-day analysis.
- **Raw House 1, Part1 vs Part2 overlap.** Part2 is kept, because it marks missing sensors as NaN rather than 0.
- **Duplicates.** Rows with the same timestamp are merged; for each channel we keep the last non-missing value.
- **Impossible values become missing, not 0.**
  - Plug > 4 kW (a UK 13 A socket gives about 3.1 kW)
  - Aggregate > 23 kW (about the 100 A main-fuse limit) or exactly 0 W
  - A "frozen" logger (identical aggregate for 30 min or more)
- **Gaps.** Gaps of 15 min or less are forward-filled. Longer gaps are flagged as outages and not filled. 15 min is a natural break in the gap-length distribution.
- **Resampling.** The 1-minute value is the mean of the readings in that minute, which preserves energy.
- **House choice for Sections 2–3.**
  - Kept: 10 homes with one unambiguous washing machine.
  - Excluded: solar homes (3, 11, 21), homes that also have a washer-dryer (1, 8, 9, 18), a home with two washing machines (4), and a home whose machine was replaced (13).
- **Washing-machine cycle.** At least 20 W; pauses of 10 min or less are joined; the result must last at least 10 min and use at least 50 Wh; groups over 6 h are not counted. A plug stuck at one identical non-zero value for 60 min or more is treated as missing.
- **`Issues` minutes** (plug sum > aggregate) are excluded from model training and evaluation, as the REFIT readme advises.

---

## Validation design

- **The split is by household:**
  - train: houses 5, 6, 7, 10, 16, 19
  - validation: houses 17, 20
  - **test: houses 2, 15**
- **Why not a random split of minutes?**
  - Minute *t* and minute *t+1* are almost identical.
  - Each prediction uses a window of up to ±120 minutes, so neighbouring examples share almost all of their input.
  - A random split would put near-copies of test examples in training (data leakage) and overstate accuracy.
- **What the household split protects.** No minute, window or habit from a test home is ever seen in training.
- **What it tests.** The split answers the question a utility actually has: *does the model work on a customer it has never sub-metered?*
- **How each set is used.**
  - Validation houses are used for early stopping and to pick one of 8 model variants (loss function, feature set, on/off gate threshold), using a rule fixed in advance: best validation F1.
  - Test houses are used once, at the end.

---

## Modelling choice

The model follows the **sequence-to-point** idea (Zhang et al., 2018): to estimate the appliance's power at minute *t*, look at a window of the aggregate around *t* (most features ±60 min; a few long summaries ±120 min).

Instead of a convolutional neural network, we summarise the window into 66 features and fit **LightGBM**, a gradient-boosted decision-tree model. The features cover:
- levels and lags
- step changes
- rolling max, min and spread over 5–121 minutes
- power above the window minimum (removes each home's base load)
- counts of minutes near the ~2 kW heater level
- drum "agitation" (minute-to-minute flicker) and counts of minutes above 1.8 kW, over 15–241 minutes
- hour of day

**The final model is two-stage:**
- Stage 1, a LightGBM classifier, estimates the probability that the machine is running.
- Stage 2, a LightGBM regressor, estimates the watts.
- Power is predicted only when P(on) > 0.2.

This variant was chosen on the validation homes by F1, from 8 variants covering different losses, feature sets and gate thresholds (`RESULTS.md`).

**Why LightGBM?**
- It trains in minutes on a laptop CPU. A CNN on about 4.2 million training examples needs a GPU to be practical.
- It handles missing minutes natively.
- It shows which inputs it relied on.
- The brief says model complexity is not assessed; a simple, well-validated model is preferred.

**Alternatives considered**
- *Seq2Point CNN:* likely more accurate with a GPU; this is the next step listed below.
- *Random forest:* typically less accurate than boosting on this kind of table data, and slower on millions of rows.
- *Factorial HMM:* the classic NILM method. It needs a model of every appliance in the home, which we do not have for unseen homes.

**Baselines**
1. **always off**
2. **hour-of-week profile**: the average washing-machine power at that hour of the week, from the training homes

**Headline test result** (homes 2 and 15, never seen in training):

| | MAE (W) | Daily energy error (Wh/day) | F1, on/off | False washes / week |
|---|---|---|---|---|
| Always off | 14.0 | 308 | 0 | – |
| Hour-of-week profile | 30.6 | 382 | 0.07 | – |
| **Two-stage LightGBM** | **13.7** | **237** | **0.46** | 0.2 (House 15) / 4.4 (House 2) |

The model beats both baselines on every metric except total energy (SAE 0.56): it under-estimates energy. The single-stage regressor is better on that (SAE 0.29). Full results and the trade-off are in `RESULTS.md`.

---

## Trade-offs

- **Centred windows** use up to 120 minutes of *future* data. That is fine for analysing meter data after the fact (billing insights, reports), but the model cannot run in real time without retraining on past-only windows.
- **Strict house exclusion** gives a clean target but leaves out homes with washer-dryers, which are common in real portfolios.
- **Forward-filling short gaps** keeps continuity but can shift appliance on/off times by up to 15 minutes.
- **No per-house calibration**: the model is applied as-is to new homes. That is the honest setting, but less accurate than fine-tuning with a few labelled weeks.

---

## AI tools used

The analysis was carried out with **Claude** (Anthropic), working as an AI assistant in a cloud workspace. It did the following:
- downloaded and inspected the data
- wrote and ran the scripts in this repository
- generated the plots
- drafted the markdown reports

All numbers in the documents are read from the script outputs in `outputs/`. An AI reviewer (a separate Claude instance that had not seen the work) checked the code for bugs and checked the repository against every requirement in the brief; its findings were fixed. Literature details (Seq2Point and the REFIT paper) were checked against the papers themselves.

---

## Possible improvements

1. **Rebuild Sections 2–3 from raw data with the Section 1 clock fix.** We found a probable 1-hour timestamp offset in the official cleaned data during summer 2015 (`DATA.md` §5).
2. **Train a Seq2Point CNN on a GPU**, and compare it with LightGBM on exactly the same house split.
3. **Leave-one-house-out cross-validation** over all 10 homes, to report an average and spread rather than two test homes.
4. **Fix the energy under-estimate** of the two-stage model by re-fitting the heater level inside each detected wash. Add dishwasher and tumble-dryer periods as "hard negatives" (examples the model must learn to reject), to cut false alarms like House 2's.
5. **Include washer-dryer homes**, with a target that combines both machines, to reflect real customers.
6. **Re-test at 15- and 30-minute resolution** to see what a standard smart meter could support (`Recommendation.md`).

---

## References

- Murray, D., Stankovic, L. & Stankovic, V. (2017). *An electrical load measurements dataset of United Kingdom households from a two-year longitudinal study.* Scientific Data 4, 160122. https://www.nature.com/articles/sdata2016122
  - Used for: dataset description, sampling, solar homes, known issues.
- Zhang, C., Zhong, M., Wang, Z., Goddard, N. & Sutton, C. (2018). *Sequence-to-point learning with neural networks for non-intrusive load monitoring.* AAAI. https://arxiv.org/abs/1612.09106
  - Adopted: predict the appliance at one point from an aggregate window; the 20 W washing-machine on-threshold; MAE and SAE metrics; testing on an unseen house.
- D'Incecco, M., Squartini, S. & Zhong, M. (2020). *Transfer learning for non-intrusive load monitoring.* IEEE Transactions on Smart Grid 11(2), 1419–1429 (2020); arXiv:1902.08835. https://arxiv.org/abs/1902.08835
  - Adopted: splitting REFIT by household (they train on REFIT homes and test on a different home); MAE and SAE as the reporting pair.
  - Their REFIT washing-machine result (MAE 16.9 W, SAE 2.61 at 8-second resolution, test house 8) gives a rough reference point.
