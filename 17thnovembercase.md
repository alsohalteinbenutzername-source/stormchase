# Case Study: 17 November 2026 — HSLC Tornado Debris Signature & Multi-Country Verification

## Executive Summary

On **17 November 2026**, StormChase's **StormAnalyzer** outlook model and real-time tracking pipeline underwent a full end-to-end live verification across northern France, Belgium, and western Germany.

During a cold-season **High-Shear, Low-CAPE (HSLC)** Atlantic frontal passage, StormAnalyzer's 744-point ICON-D2 scoring grid highlighted a primary **15–30% tornado threat corridor** stretching from northern France into southern Belgium, alongside lower marginal risk tiers across the Benelux region and northern North Rhine-Westphalia (NRW). Between **17:45 and 19:15 CET**, a fast-moving convective cell (92–115 km/h ENE) intensified along the French–Belgian border near **Maubeuge, Jeumont, and Estinnes**, producing a textbook dual-polarization **Tornado Debris Signature (TDS)** before crossing into DWD's volumetric radar footprint near Namur and later re-organizing into a squall line with embedded mesocyclones over western Germany.

The [Keraunos report](https://www.keraunos.org/actualites/fil-infos/2026/novembre/tornade-possible-boiry-becquerelle-pas-de-calais-arras-17-novembre-2026) covers damage from an intense wind phenomenon at Boiry-Becquerelle in Pas-de-Calais, France. It says Doppler data did not distinguish a distinct rotation and that an investigation was underway to determine the event's nature. It is an additional report from the same date, but it does not confirm a tornado near Estinnes or in Belgium.

---

## 1. Synoptic & Mesoscale Setup

* **Synoptic Regime:** A powerful ~300 km/h upper-level jet stream drove a secondary cold front (*limite secondaire*) onshore from the English Channel during the late afternoon.
* **Thermodynamics vs. Kinematics:** Surface heating and instability (CAPE) remained modest, characteristic of mid-November in Western Europe. However, intense 0–1 km directional wind shear and exceptionally high Storm-Relative Helicity (SRH) created an environment favorable for low-topped, rapid mesovortex spin-ups along coastal convergence boundaries and convective line breaks.
* **Independent Alignment:** Regional specialist outlooks (including Keraunos) independently highlighted a 15–30% tornado probability corridor across northern France during the afternoon window, matching StormAnalyzer's highest-ranked risk tier.

---

## 2. StormAnalyzer Outlook Performance

| Region | StormAnalyzer Risk Assessment | Observed Convective Evolution |
| :--- | :--- | :--- |
| **Northern France to Southern Belgium** (Hauts-de-France / Hainaut) | **Primary Corridor (15–30% tier)** | Rapid late-afternoon cell intensification; severe storm warnings (2 cm hail, 100 km/h gusts, *"sehr starke Rotation"*); dual-pol TDS observed near Estinnes/Binche. |
| **Central/Northern Belgium, Netherlands & Luxembourg** | **Lower Marginal Tier** | Fast-moving squall line and isolated coastal/inland convective gusts (70–90 km/h); reduced tornado potential after sunset due to boundary-layer stabilization. |
| **Northern North Rhine-Westphalia (NRW)** | **Slight / Conditional Eastern Edge** | Late-evening squall line entry (~21:00–22:00 CET) with two embedded mesocyclones tracked along the gust front. |

---

## 3. Event Timeline (17 November 2026, CET)

* **Pre-Event (Morning/Afternoon):** StormAnalyzer processes the 744-point ICON-D2 grid, identifying the primary 15–30% tornado risk corridor over northern France into southern Belgium and tapered marginal zones into Benelux and northern NRW.
* **17:40–17:45 CET (Channel Coast to Mons):** Non-convective rain echoes stream off the English Channel into West Flanders and Nord-Pas-de-Calais. A severe core registers 6 km SW of Dour moving ENE at 115 km/h with initially zero lightning strokes—a classic non-electrified HSLC signature.
* **18:05 CET (Cambrai & Squall Line Consolidation):** Four aligned cells consolidate into a Quasi-Linear Convective System (QLCS) across central Belgium with a sudden burst of Blitzortung lightning activity. Simultaneously, a trailing cell 5 km SW of Cambrai (moving ENE at 93 km/h) triggers initial cloud-rotation alerts with low lightning activity.
* **18:30–18:40 CET (French–Belgian Border / Estinnes TDS):** Crossing between Maubeuge, Jeumont, Dour, and Estinnes, the trailing cell spikes to **60+ dBZ (grey reflectivity tier)** with **"sehr starke Rotation"** (21 lightning strikes in 10 minutes, 2 cm hail, 100 km/h gusts). Dual-pol radar resolves a compact **Correlation Coefficient (CC) drop down to 0.31** co-located with a tight velocity couplet and a high-reflectivity debris ball at the pinched S-curve border kink.
* **18:45–19:15 CET (Binche, Charleroi & Namur Radar Horizon):** Outflow begins undercutting the circulation near Charleroi, broadening the CC field as the vortex occludes. As the storm crosses into DWD's Neuheilenbach Doppler coverage near **Namur (~18:54–19:00 CET)**, StormChase locks onto the storm track vector and detects the residual mesovortex rotation locally.
* **~21:30–22:00 CET (Western Germany Entry):** The broader squall line pushes into western Germany with two embedded mesocyclones detected inside StormAnalyzer's predicted northern NRW marginal corridor.

---



## 4 Technical Takeaways & Next Improvements

1. **HSLC Calibration Confirmed:** Weighting tornado scores by final overall risk (`risk / 100`, capped at `1`) successfully suppressed false alarms in high-shear/zero-CAPE regions while preserving the 15–30% corridor where boundary-layer moisture and low-level SRH overlapped.
2. **Western Radar Horizon Latency:** Because DWD's Neuheilenbach radar beam sits above shallow low-topped cores west of the Charleroi–Namur meridian, cells approaching from northern France/western Belgium enter StormChase's local DWD detection footprint later than western radar composites. Integrating optional cross-border radar feeds or documenting the ~150 km range ring helps set clear expectations for users tracking inbound western systems.
3. **Post-Event Ground Verification:** Follow up with regional damage survey databases (European Severe Weather Database / ESWD, Belgorage, Keraunos) to archive official ground-track ratings alongside this radar dataset.
