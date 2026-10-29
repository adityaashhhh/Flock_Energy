# DATA.md — data coverage, quality findings and cleaning decisions

All numbers below are produced by the scripts and stored in `outputs/`.
If you re-run the pipeline they are regenerated.

---

## 1. The dataset

**REFIT Electrical Load Measurements** (University of Strathclyde, CC-BY 4.0). Murray et al., *Scientific Data* 4, 160122 (2017).

| Item | Value |
|---|---|
| Homes | 20 (numbered 1–21, there is no House 14), Loughborough area, UK |
| Period | 2013-09 to 2015-07 (≈ 15–21 months per house) |
| Sensors per house | 1 whole-house current clamp (*Aggregate*) + 9 plug monitors (*IAMs*, Appliance1–9) |
| Quantity | Active power only, in **watts (W)** |
| Sampling | Nominally every **8 s**. In practice 6–8 s, and the logger only writes a row when a load changes. Sensors are not synchronised: a plug value can be up to ~7 s old. |
| Raw files | `RAW_HouseN_Part1.csv` (unavailable sensors written as **0**) and `RAW_HouseN_Part2.csv` (unavailable sensors written as **NaN**). The two parts overlap in time. |
| Cleaned files | `CLEAN_HouseN.csv`. The official cleaning applied a daylight-saving fix, re-joined IAMs that were moved between plugs, forward-filled NaNs, replaced IAM spikes above 4,000 W with 0, and added an `Issues` column (= 1 when the sum of the IAMs is greater than the aggregate). |

**Files used**

| Section | Files | Homes | Period |
|---|---|---|---|
| 1 (raw cleaning) | `RAW_House1_Part1.csv`, `RAW_House1_Part2.csv` | House 1 | 2013-10-09 → 2015-07-10 |
| 2 (EDA) and 3 (model) | `CLEAN_House{2,5,6,7,10,15,16,17,19,20}.csv` | 10 homes | each home's full record (table in §4 below) |

**Known limitations of REFIT itself**
- From the paper: houses 3, 11 and 21 have solar panels that distort the aggregate.
- From the paper: the sensors measure current, not voltage, so there is about a 6% measurement error.
- Average uptime is only ~88%, with a big outage in Feb 2014.
- Householders sometimes moved plugs between appliances.
- Each IAM monitors one socket. An appliance labelled "Television Site" can be several devices on one socket.
- 20 homes from one English town in 2013–15 is a small and specific sample.

---

## 2. Section 1 — raw House 1: what we found (before cleaning)

Source: `outputs/s1_inspection.json`

| Check | Part1 (Oct 2013 – Oct 2014) | Part2 (Jun 2014 – Jul 2015) |
|---|---|---|
| Rows | 4,377,877 | 4,140,886 |
| Timestamp parsing | all `Time` strings parse; `Time` and `Unix` agree within 1 s | same |
| Ordering | **1 backward jump of ~1 h** (27 Oct 2013, 02:00 → 01:00) | sorted, but 26 Oct 2014 01:00–02:00 holds **two hours of data interleaved** |
| Time standard | **UK wall-clock time**: the clock repeats an hour in October and skips 01:00–02:00 in March | same |
| Rows sharing a timestamp | 1,833,597 | 29,510 |
| … of which exact extra copies | 884,307 | 393 |
| … same timestamp, *different* values | 210,762 | 28,724 |
| Sampling interval (median / 99th pct) | 3 s / 15 s (irregular because of duplicates) | 7 s / 9 s |
| Longest silence | 998 h (24 Jan – 6 Mar 2014) | 208 h |
| NaN cells | 0 (missing data written as 0) | e.g. aggregate 188,973; electric-heater plug 1.9 M |
| Aggregate = 0 W (impossible for a home) | 12 rows | 0 |
| Negative values | 0 | 0 |
| Aggregate > 23 kW | 26 rows (max 24,000 W, which looks like a sensor ceiling) | 51 rows (max 29,159 W) |
| Plug readings > 4 kW | e.g. Fridge 662, Washer-dryer 2,014, Washing machine 156 | e.g. Computer 371, Fridge 184 |
| Typical "impossible" values | 32,767 / 65,535 / 98,301 W: sensor error codes, not power | same |

**Overlap.** Part1 and Part2 both cover **27 Jun – 1 Oct 2014**. In that window the typical minute agrees (the median absolute difference of the 1-minute aggregate is 1.3 W), but the correlation is only 0.79, so some stretches disagree substantially. We keep Part2 for the reason given in step 3 below.

**Frozen logger (found while plotting).** Sometimes the logger kept re-sending identical values. For example, on 8 Feb 2015 the aggregate was stuck at exactly 12,505 W and the washing machine at 2,256 W for ~2.7 h. Detection uses the aggregate only, but in the episodes we inspected every channel froze together. A live whole-house meter always varies by a few watts, so these values are not real.

---

## 3. Section 1 — cleaning pipeline (`src/s1_clean_raw.py`)

The steps run in this order. Every count comes from `outputs/s1_cleaning_log.json`.

| # | Step | Rule | Effect |
|---|---|---|---|
| 1 | **Clock fix** | Read timestamps as `Europe/London` wall-clock time, then convert to UTC. The repeated autumn hour cannot be untangled once rows are sorted or interleaved, so those rows are dropped. | 2,185 rows dropped (≈ 2 h per year) |
| 2 | **Part1 fake zeros** | Aggregate = 0 W → missing (a home always has some base load) | 12 rows |
| 3 | **Merge parts** | Use Part1 only before Part2 starts. **In the overlap, keep Part2**, because it marks missing sensors as NaN instead of a misleading 0. | 1,257,865 Part1 rows dropped |
| 4 | **Impossible values → missing** | Plug > 4,000 W (a 13 A UK socket gives ≈ 3.1 kW; 4 kW is the official REFIT cut-off). Aggregate > 23,000 W (≈ 100 A main fuse × 230 V). Anything < 0. | 69 aggregate values, 2,889 plug values. We set them to **missing**, not 0 as the official cleaning does, because 0 would claim "appliance off". |
| 5 | **Sort + de-duplicate** | Stable sort by time. Rows sharing a timestamp are merged; for each channel we keep the **last non-missing value written** (a later poll means a newer value). | 7,258,713 → 6,662,139 rows |
| 5b | **Frozen logger** | Consecutive aggregate readings identical for ≥ 30 min (a silence of more than 2 min breaks the run) → all channels set missing for that stretch | 18 episodes, 62.0 h |
| 6 | **Resample to 1 min** | Mean of the readings inside each minute (mean W × 1 min = energy, so energy is preserved) | 920,031 minutes |
| 7 | **Short gaps** | Missing stretches of **≤ 15 min** are forward-filled (the last value is held) | aggregate: 4,423 min filled (0.48%) |
| 8 | **Long gaps** | Anything longer is **left missing** and flagged `outage` (`<channel>_flag = 2`) | 74 outages, 118,881 min = **12.9%** of the period; the longest is 41.6 days (24 Jan – 6 Mar 2014) |
| 9 | **Consistency flag** | `sub_meter_sum_gt_aggregate`: the plugs add up to more than the house total | 6,927 minutes (0.75%) |

**Why forward-fill, and why 15 minutes?**
- The logger writes a row only when the load changes, so "hold the last value" is the logger's own convention.
- The gap lengths have a natural break at 15 minutes (`outputs/s1_gap_sensitivity.csv`). Gaps of ≤ 10 min number 468; gaps of ≤ 15 min number 758; going up to 30 min adds only 25 more. So 11–15 minute silences are a frequent logger pattern, not real outages.
- Filling them changes only 0.5% of minutes. Gaps longer than 15 min are where holding a value would start inventing appliance behaviour (a fridge cycle is ~20–40 min), so those are flagged instead.

**Output.** `data/interim/house1_clean_1min.parquet` contains a UTC index, `ts_local`, the 10 channels, a flag per channel (0 = measured, 1 = short gap filled, 2 = long outage), plus `outage` and `sub_meter_sum_gt_aggregate`.

**Plots**
- `figures/s1_before_after_overview.png`: whole period, like-for-like. The daily **mean** before cleaning (raw readings as stored) and after cleaning are shown on the same axis, with outages shaded in the AFTER panel. A third panel shows the daily **maximum** of the raw readings on a log scale: error codes reach 10⁴–10⁵ W, above the red physical-limit line.
- `figures/s1_before_after_window.png`: 19–21 Dec 2013, with aggregate, washing machine and fridge. Grey is raw, blue is cleaned, red triangles are removed values, orange ticks are filled gaps.
- `figures/s1_before_after_frozen.png`: the frozen-logger episode, removed.

### Check against the official cleaned House 1
`src/check_clock.py` compares our 1-minute aggregate with the official `CLEAN_House1.csv` (`outputs/check_official_vs_ours_monthly.csv`):
- **Total energy over the common minutes agrees within 0.1%.**
- Oct 2013 – Jun 2014 and Nov 2014 – Feb 2015 line up minute by minute (monthly r ≥ 0.99, mean difference 2–9 W).
- **Jul – Sep 2014** (r 0.70–0.91): this is the Part1/Part2 overlap. The official file appears to use Part1 there, while we use Part2. Part1 and Part2 themselves correlate only 0.79 at 1-minute resolution in this window.
- **Apr – Jul 2015:** the two series are offset by **exactly 1 hour**. Shifting the official series by 60 min raises r from about 0.05 to 1.000. Oct 2014 (summer time until the 26th) also matches best with a 60-min shift.
- To decide which is right we used a test that does not depend on either file's timestamps: the household's morning "wake-up" power rise, measured as the steepest rise in the average daily profile between 05:00 and 11:00 (`outputs/check_morning_rise.csv`).
  - In our data it happens at 09:53 (winter 2014/15) and 09:57 (summer 2015). That is the same local time year-round, as expected.
  - In the official data it moves from 09:53 to **10:57** in summer 2015.
- A second, independent clue: the study-wide outage of May 2015 starts at 22:37 UTC in our House 1 data, but at 23:37 in the official cleaned files of every house we checked (5, 10, 17, 20). That is the same 1-hour offset.
- **Conclusion:** the official cleaned file appears to keep **local time** (not UTC) in the 2015 summer-time period, so its timestamps there are 1 h late.

---

## 4. Sections 2–3 — official cleaned data: coverage and quality

Source: `outputs/s2_availability.csv`. "Missing" = 1-minute slots with no reading (the cleaned files leave long outages as absent rows).

| House | WM channel | First – last (UTC) | Days | Missing minutes* | Gaps ≥ 1 h | Longest gap (days) | `Issues` minutes (of measured) | WM stuck minutes removed | WM exactly 0 W | WM ≤ 20 W (off) | Cycles | Cycles / week |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | Appliance2 | 2013-09-17 – 2015-05-28 | 617 | 24.6% | 80 | 62.0 | 2.0% | 0 | 94.4% | 95.3% | 335 | 5.0 |
| 5 | Appliance3 | 2013-09-26 – 2015-07-06 | 648 | 11.4% | 274 | 8.6 | 8.4% | **19,599** | 53.4% | 94.4% | 712 | 8.7 |
| 6 | Appliance2 | 2013-11-28 – 2015-06-28 | 577 | 15.2% | 22 | 33.6 | 2.1% | 0 | 97.4% | 98.1% | 123 | 1.8 |
| 7 | Appliance5 | 2013-11-01 – 2015-07-08 | 613 | 12.6% | 27 | 41.6 | 4.6% | 0 | 74.9% | 91.6% | 855 | 11.2 |
| 10 | Appliance5 | 2013-11-20 – 2015-06-30 | 587 | 11.2% | 214 | 8.6 | 1.9% | 0 | 90.2% | 91.3% | 603 | 8.1 |
| 15 | Appliance3 | 2013-12-17 – 2015-07-08 | 567 | 13.7% | 205 | 8.6 | 1.6% | 225 | 96.2% | 97.1% | 257 | 3.7 |
| 16 | Appliance5 | 2014-01-10 – 2015-07-08 | 544 | 17.3% | 205 | 18.1 | 1.0% | 0 | 93.3% | 96.3% | 315 | 4.9 |
| 17 | Appliance4 | 2014-03-06 – 2015-06-19 | 470 | 6.2% | 16 | 8.6 | 4.3% | 0 | 96.6% | 97.6% | 283 | 4.5 |
| 19 | Appliance2 | 2014-03-06 – 2015-06-20 | 471 | 6.5% | 15 | 8.6 | 4.1% | 0 | 86.6% | 96.1% | 255 | 4.1 |
| 20 | Appliance4 | 2014-03-20 – 2015-06-23 | 460 | 6.0% | 13 | 8.6 | 1.6% | 98 | 97.2% | 97.6% | 193 | 3.1 |

\* A minute counts as missing if either the aggregate or the washing-machine value is missing, including stuck-sensor minutes. Many houses share the same 8.6-day outage (10–19 May 2015), so it was study-wide; House 1 has it too.

**Stuck sub-meter.** We found minutes where the washing-machine plug repeats the **identical non-zero value for ≥ 60 minutes**, for example exactly 2,046 W for 12 h in House 5. A running machine's power changes every minute, so these are sensor faults. They are set to missing (`src/refit.py → stuck_wm`). In addition, 8 groups in House 5 lasted more than 6 h. These are mostly back-to-back loads with pauses of ≤ 10 min, and they are **not counted** as cycles; the cycle rule caps a cycle at 6 h.

**Why these 10 houses.** 19 homes list a washing machine. We excluded:
- **3, 11 and 21**: solar panels distort the aggregate.
- **1, 8, 9 and 18**: they also own a washer-dryer, a second load that looks almost identical and would blur both the target and the "off" periods.
- **4**: two washing machines.
- **13**: the machine was replaced on 25 Mar 2015.

**Handling in Sections 2–3**
- The 1-minute value is the mean of the ~8-second readings in that minute.
- Minutes with no reading stay missing; we do not fill anything on top of the official cleaning.
- Minutes with `Issues` > 0 are **excluded from model training and evaluation**, as the REFIT readme advises. They are kept in the EDA counts.
- Stuck washing-machine minutes are missing everywhere: in EDA, and as model labels.
- WM "exactly 0 W" differs from "≤ 20 W" in houses 5, 7 and 19 because their machines draw 1–5 W in standby.

---

## 5. Remaining concerns

1. **Clock offset in the official cleaned data.** See §3: about 1 h during summer time in 2015 for House 1. The same morning-rise test on the 10 houses (`outputs/check_morning_rise.csv`) points the same way for several of them:
  - In houses 2, 5, 10, 16, 17 and 20 the summer-2015 rise is **50–80 min later than in winter 2014/15**.
  - In houses 2, 5, 10, 17 and 20, summer 2014 and winter 2014/15 differ by less than 20 min (House 16 is the exception). If summer itself changed people's habits, the same shift would show up in 2014, so this points to a clock offset in the 2015 data.

  As a result, **Section 2's start-hour patterns may be shifted by up to +1 h for summer-2015 data** (14.4% of detected cycles start after 29 Mar 2015). Weekday patterns are barely affected. The model's hour-of-day features inherit the same offset for those months and the hour-of-week baseline is affected similarly; the model's main signals come from the shape of the aggregate. Fixing this properly means rebuilding each house from raw data with the Section 1 clock fix; this is listed as a next step in README.md.
2. **The official clean data still has daylight-saving artefacts at the switch-over hours.** For example, House 2 has a missing hour on 30 Mar 2014 and two hours of readings stacked into 01:00–02:00 on 26 Oct 2014. This affects a few hours per year.
3. **Part1 zeros for appliances.** For plug channels, "sensor offline" and "appliance off" both appear as 0 in Part1, and nothing in the data can tell them apart. Only the aggregate zeros could be fixed.
4. **Short-gap filling** holds the last value. If an appliance switched on or off inside a ≤ 15-min gap, the timing of that change is off by up to 15 min.
5. **The 23 kW aggregate cap is a judgement call.** A few genuine peaks just under it may still be sensor errors.
6. **The electric-heater plug in House 1 is missing 36% of the time.** Its readings should not be used for heating analysis without care.
