# Recommendation: washing-machine disaggregation for a utility

*One-page summary. All numbers are from `outputs/`; method and evidence are in `RESULTS.md`.*

**What it is.** A model that estimates when a home's washing machine runs, and roughly how much energy it uses, from the whole-house meter signal alone (1-minute data). It was trained on 6 UK homes and tested on 2 homes it had never seen.

**Suitable uses**
- **Finding *when* homes wash, across a group of customers.** This supports targeting demand-shift offers, segmenting customers (evening, weekend or night washers), and estimating washing load in the evening peak. Results for a group of homes are more reliable than for a single home.
- **Tracking change in a home's own pattern over time**, for example whether washes moved to after 21:00.
- **A rough, clearly labelled estimate of washing energy** for customer insights.

**Not suitable for**
- billing, or any figure shown to a customer as exact
- homes with a washer-dryer, a second machine or solar panels (untested)
- real-time control (the model looks up to 2 h ahead in the data)
- 15- or 30-minute meter data without retraining

**Measured limits (test homes)**

| | House 15 | House 2 |
|---|---|---|
| Real washes found | 62% | 54% |
| Found washes that are real | 92% | 39% |
| False washes per week | 0.2 | 4.4 |

- Energy is under-estimated by about half: the total-energy error is 56%, or 29% with the simpler single-stage regressor.
- The model beats both baselines (always off, average weekly profile) on error per minute, daily energy error and on/off F1.
- Accuracy varies a lot between homes. Validate on sub-metered homes from the target population before any customer-facing use.

---

## Section 4 answers

**Q1 – Opportunity: targeted shifting of washes out of the evening peak.**

On weekdays, 16:00–19:00 holds 9.6% of a home's washing-machine energy (average across the 10 homes). This varies widely: House 16 has 26%, House 19 has 4% and runs 71% of its washes at night. Washing is only about 3% of household energy in that window, so the benefit is modest and only worth chasing in the minority of homes that wash in the peak.

The offer: use the model to find "peak washers" and send only them a delay-start or time-of-use message.

**Measure it with a randomised trial.** Split eligible homes into a message group and a control group.
- **Primary outcome:** the share of detected wash starts in 16:00–19:00. Compare the change in the message group with the change in the control group (difference-in-differences). Model errors affect both groups equally.
- **Model-free check:** peak-window kWh straight from the meter.
- **Ground truth:** plug monitors on a sub-sample.
- **Success threshold, fixed in advance:** for example, at least a 20% relative drop in peak wash starts, with a confidence interval that excludes zero.

**Does the clock issue change this?** Pooled across all 10 homes, the peak window holds 10.2% of washing energy and 10.6% of wash starts. Leaving out the suspect summer-2015 data (`DATA.md` §5) gives 10.8% and 11.2%, so the conclusion is unchanged (`outputs/check_peak_without_summer2015.csv`).

**Q2 – Limitations**
- **Overlapping signatures.** The roughly 2 kW heater block appears in 93% of washes, but kettles, dishwashers, tumble dryers, ovens and showers draw the same power. These loads are the likely cause of House 2's false washes. Low-temperature washes (the 7% of cycles that never reach 1.5 kW) are hard to see at all.
- **Missing data.** Homes are missing 6–25% of minutes. We also found stuck sensors (House 5's plug, about 13.6 days in total) and a probable 1-hour clock error in the official 2015 summer data. Automatic fault checks must run before any modelling.
- **Household differences.** Median energy per wash ranges from 243 to 711 Wh, and homes run between 1.8 and 11.2 washes per week. With only 6 training homes, accuracy on a new home is unpredictable.
- **UK → India.**
  - About 64% of Indian washing-machine sales in 2025 were fully automatic ([IMARC](https://imarcgroup.com/india-washing-machine-market)); the rest are semi-automatic, with a different power pattern.
  - We could not verify how many Indian machines heat water internally. Without that heater cue, the remaining motor load overlaps with fans and pumps.
  - Geysers, air-conditioners, pumps and inverter charging add more look-alike loads.
  - Outages and voltage swings fragment the signal.
  - Indian smart meters typically record every 15 minutes ([ISGF, 2023](https://www.directorstalkinterviews.com/wp-content/uploads/2023/11/ISGF-White-Paper-on-Smart-Metering-in-India-A-Critical-Assessment-Revision-1-04-October-2023.pdf)).
  - The method transfers, but the model must be retrained and validated on sub-metered Indian homes.

**Q3 – With only 15- or 30-minute data**

The features that identify a wash at 1-minute resolution disappear: the 15-minute heater block and the hour of flickering drum motion after it. A 15-minute average turns the heater into a single 0.5–2 kW bump, which looks like a kettle plus an oven. At 30 minutes the drum flicker that separates a wash from other heating loads is averaged away completely. Counting washes, separating back-to-back loads and timing a wash start (for example 18:35 versus 18:55, which matters for peak shifting) all become unreliable. What remains possible are statistical estimates across many days and many homes, such as a home's typical weekly washing energy or whether a group shifted its evening use, and these come with wider uncertainty.
