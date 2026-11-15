"""Training load vs next-night recovery, grouped instead of scattered."""

import numpy as np
import pandas as pd

from src.metrics import health as hl


def test_rest_days_and_quartiles():
    idx = pd.date_range("2026-01-01", periods=200)
    load = pd.Series(np.r_[np.zeros(100), np.linspace(1, 200, 100)], index=idx)
    hrv = pd.Series(np.r_[np.zeros(100), -np.linspace(0, 8, 100)], index=idx)  # harder days -> lower HRV
    sleep = pd.Series(80.0, index=idx)
    out = hl.load_bins(load, hrv, sleep)
    assert [g["label"] for g in out["groups"]] == ["Rest day", "Easiest quarter", "Easy quarter", "Hard quarter", "Hardest quarter"]
    assert out["groups"][0]["n"] == 100
    assert out["finding"].startswith("After your hardest days the next night is HRV")


def test_too_few_nights_not_compared():
    idx = pd.date_range("2026-01-01", periods=10)
    out = hl.load_bins(pd.Series(0.0, index=idx), pd.Series(1.0, index=idx), pd.Series(80.0, index=idx))
    assert out["groups"][0]["hrv"] == 1.0 and out["finding"] is None
