"""Health metrics (plan sections 6.3, 7.7-7.9). Baselines themselves live in common.rolling_baseline."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def sleep_midpoint_minutes(bed_ts, total_s) -> float:
    """Sleep midpoint (7.8) = bed_ts + total duration / 2, as minutes from midnight on a continuous scale
    (-720..720, so 23:30 -> -30 and 02:00 -> 120; no wrap-around jump at midnight)."""
    if bed_ts is None or pd.isna(bed_ts) or total_s is None or pd.isna(total_s):
        return float("nan")
    mid = pd.Timestamp(bed_ts) + pd.to_timedelta(float(total_s) / 2, unit="s")
    m = mid.hour * 60 + mid.minute + mid.second / 60
    return m - 1440 if m >= 720 else m


def clock_minutes(ts) -> float:
    """Clock time as minutes on the same continuous scale (for bed/wake range plots)."""
    if ts is None or pd.isna(ts):
        return float("nan")
    t = pd.Timestamp(ts)
    m = t.hour * 60 + t.minute
    return m - 1440 if m >= 720 else m


def regularity_sd(midpoints: pd.Series, nights: int = 14) -> pd.Series:
    """SD of sleep midpoint over the trailing `nights` nights (minutes)."""
    return midpoints.rolling(nights, min_periods=max(5, nights // 2)).std()


def weight_ema(daily: pd.Series, alpha: float = 0.1) -> pd.Series:
    """EMA with alpha 0.1 on daily values; missing days carry the last value forward (for the EMA only)."""
    if daily.empty:
        return daily
    idx = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    x = daily.groupby(level=0).mean().reindex(idx).ffill()
    return x.ewm(alpha=alpha, adjust=False).mean()


def strain_days(rhr_z: pd.Series, hrv_z: pd.Series, resp_z: pd.Series, min_run: int = 2) -> pd.Series:
    """Days where resting HR is above its band, HRV below and respiration above (|z| > 1 each, day values),
    for >= min_run consecutive days. Descriptive pattern only."""
    both = (rhr_z > 1) & (hrv_z < -1) & (resp_z > 1)
    both = both.fillna(False)
    out = pd.Series(False, index=both.index)
    run = []
    for d, v in both.items():
        if v:
            run.append(d)
        else:
            if len(run) >= min_run:
                out[run] = True
            run = []
    if len(run) >= min_run:
        out[run] = True
    return out


def correlation(x: pd.Series, y: pd.Series) -> dict:
    """Pearson r with n on aligned, non-null pairs."""
    df = pd.concat([x, y], axis=1).dropna()
    n = len(df)
    if n < 3 or df.iloc[:, 0].std() == 0 or df.iloc[:, 1].std() == 0:
        return {"n": n, "r": None}
    r = float(np.corrcoef(df.iloc[:, 0], df.iloc[:, 1])[0, 1])
    return {"n": n, "r": None if math.isnan(r) else r}


def load_bins(load: pd.Series, hrv_dev: pd.Series, sleep_score: pd.Series, min_n: int = 8) -> dict:
    """Next-night recovery after rest days vs training days split into load quartiles.

    A scatter of all days piles most points at load 0 and shows r near zero; grouping answers the actual question
    ("do my hard days cost me sleep or HRV?") directly. Groups with fewer than `min_n` nights are reported but
    not compared.
    """
    df = pd.DataFrame({"load": load, "hrv": hrv_dev, "sleep": sleep_score}).dropna(subset=["load"])
    rest = df[df["load"] <= 0]
    train = df[df["load"] > 0]
    groups = [("Rest day", rest, None, None)]
    if len(train) >= 4 * min_n:
        q = pd.qcut(train["load"], 4, labels=False, duplicates="drop")
        for k in sorted(q.dropna().unique()):
            g = train[q == k]
            groups.append((["Easiest", "Easy", "Hard", "Hardest"][int(k)] + " quarter", g, float(g["load"].min()), float(g["load"].max())))
    elif len(train):
        groups.append(("Training day", train, float(train["load"].min()), float(train["load"].max())))
    out = []
    for name, g, lo, hi in groups:
        out.append({"label": name, "lo": None if lo is None else round(lo), "hi": None if hi is None else round(hi),
                    "n": int(g["hrv"].notna().sum()),
                    "hrv": None if g["hrv"].notna().sum() < min_n else round(float(g["hrv"].mean()), 1),
                    "sleep": None if g["sleep"].notna().sum() < min_n else round(float(g["sleep"].mean()), 1)})
    finding = None
    if len(out) > 1 and out[0]["hrv"] is not None and out[-1]["hrv"] is not None and out[-1]["sleep"] is not None and out[0]["sleep"] is not None:
        dh, ds = out[-1]["hrv"] - out[0]["hrv"], out[-1]["sleep"] - out[0]["sleep"]
        if abs(dh) < 2 and abs(ds) < 3:
            sign = lambda v: f"{v:+.1f}".replace("-", "−")
            finding = (f"Your hardest days barely move the next night: HRV {sign(dh)} ms and sleep score {sign(ds)} "
                       "compared with rest days.")
        else:
            parts = []
            if abs(dh) >= 2:
                parts.append(f"HRV {abs(dh):.1f} ms {'lower' if dh < 0 else 'higher'}")
            if abs(ds) >= 3:
                parts.append(f"sleep score {abs(ds):.0f} points {'lower' if ds < 0 else 'higher'}")
            finding = "After your hardest days the next night is " + " and ".join(parts) + " than after rest days."
    return {"groups": out, "finding": finding, "min_n": min_n}
