"""Shared formulas: load EWMAs, rolling personal baselines and flags, week bucketing (plan sections 7.2, 7.7)."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd


def to_date(x) -> date:
    return pd.Timestamp(x).date()


def week_start(d) -> date:
    """ISO week bucket: the Monday of the week (dates are already local, Europe/Berlin)."""
    d = to_date(d)
    return d - timedelta(days=d.weekday())


def daily_index(start, end) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")


def fitness_fatigue(load: pd.Series, ctl_days: int = 42, atl_days: int = 7, seed_days: int = 14) -> pd.DataFrame:
    """CTL/ATL/TSB (7.2). `load` is a daily series (DatetimeIndex); missing days count as 0.
    CTL_t = CTL_{t-1} + (load_t - CTL_{t-1}) / 42; ATL likewise with 7; TSB_t = CTL_{t-1} - ATL_{t-1}.
    Both are seeded with the mean of the first `seed_days` days."""
    if load.empty:
        return pd.DataFrame(columns=["load", "ctl", "atl", "tsb"])
    idx = daily_index(load.index.min(), load.index.max())
    x = load.reindex(idx, fill_value=0.0).fillna(0.0).astype(float)
    seed = float(x.iloc[:seed_days].mean())
    ctl_prev = atl_prev = seed
    ctl, atl, tsb = [], [], []
    for v in x.to_numpy():
        tsb.append(ctl_prev - atl_prev)
        ctl_prev = ctl_prev + (v - ctl_prev) / ctl_days
        atl_prev = atl_prev + (v - atl_prev) / atl_days
        ctl.append(ctl_prev)
        atl.append(atl_prev)
    return pd.DataFrame({"load": x, "ctl": ctl, "atl": atl, "tsb": tsb}, index=idx)


def rolling_baseline(values: pd.Series, baseline_days: int = 60, short_days: int = 7,
                     min_valid: int = 30) -> pd.DataFrame:
    """Personal baseline per day (7.7). For day d: baseline / sd over the `baseline_days` days that end just before
    the current `short_days` window, short = mean of the last `short_days` days (incl. d),
    z = (short - baseline) / sd. Needs >= min_valid valid baseline days, else NaN."""
    if values.empty:
        return pd.DataFrame(columns=["value", "baseline", "sd", "short", "z", "n_baseline"])
    idx = daily_index(values.index.min(), values.index.max())
    v = values.reindex(idx).astype(float)
    shifted = v.shift(short_days)
    base = shifted.rolling(baseline_days, min_periods=min_valid).mean()
    sd = shifted.rolling(baseline_days, min_periods=min_valid).std()
    n = shifted.rolling(baseline_days, min_periods=1).count()
    short = v.rolling(short_days, min_periods=1).mean()
    z = (short - base) / sd.replace(0, np.nan)
    return pd.DataFrame({"value": v, "baseline": base, "sd": sd, "short": short, "z": z, "n_baseline": n}, index=idx)


def flag(z: float | None, unfavorable: str = "both") -> str:
    """Three-state chip from z (7.7): in_range |z|<1, watch 1..2, out >2.
    unfavorable: 'low' (e.g. HRV, sleep), 'high' (e.g. resting HR), 'both'. Deviations in the
    favorable direction stay in_range for one-sided metrics."""
    if z is None or (isinstance(z, float) and math.isnan(z)):
        return "no_baseline"
    if unfavorable == "low" and z > 0:
        return "in_range"
    if unfavorable == "high" and z < 0:
        return "in_range"
    a = abs(z)
    return "in_range" if a < 1 else ("watch" if a <= 2 else "out_of_range")


def config_hash(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:10]


def clean(obj):
    """Make a structure JSON-safe: NaN/inf -> None, numpy scalars -> python, dates -> ISO strings."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, date)):
        return obj.isoformat()[:10] if not isinstance(obj, pd.Timestamp) or obj == obj.normalize() \
            else obj.isoformat()
    if obj is pd.NaT:
        return None
    return obj


def series_points(s: pd.Series, digits: int = 2) -> list:
    """[[date, value], ...] for ECharts time axes, skipping NaN."""
    out = []
    for k, v in s.items():
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        out.append([pd.Timestamp(k).date().isoformat(), round(float(v), digits)])
    return out
