"""
Flow absorption signal.

The math in compute() is copied unchanged from the validated study /
flow_monitor.py. Do not change it without re-validating.

Two fixes vs. the old monitor page (neither changes the signal itself):
  1. "Last completed bar" is decided by the clock, not by assuming the
     newest bar is still forming. After the close, the old page judged
     the 14:30 bar instead of the 15:30 bar.
  2. Historical fires use the daily trend gate AS OF THAT DAY, not
     today's gate painted over the whole history.
"""

import numpy as np
import pandas as pd

VOL_WINDOW = 168
IMB_THR = 0.4      # validated
VOLZ_THR = 1.0     # validated
MA_LEN = 200
STANDOUT_VOLZ = 4.0


def compute(df, vol_window=VOL_WINDOW):
    """CLV-weighted imbalance and volume z-score. Same math as the
    validated study scripts — do not change without re-validating."""
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    clv = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / rng
    clv = clv.fillna(0.0)
    imbalance = ((clv * df["Volume"]) / df["Volume"].replace(0, np.nan)).fillna(0.0)
    vmean = df["Volume"].rolling(vol_window).mean()
    vstd = df["Volume"].rolling(vol_window).std()
    vol_z = (df["Volume"] - vmean) / vstd.replace(0, np.nan)
    return imbalance, vol_z


def gate_series(daily, ma_len=MA_LEN):
    """Daily close above its 200-day MA, indexed by date."""
    ma = daily["Close"].rolling(ma_len).mean()
    g = (daily["Close"] > ma).where(ma.notna())
    g.index = pd.to_datetime(g.index).normalize()
    return g[~g.index.duplicated(keep="last")].sort_index()


def bar_end(ts):
    """Hourly bars start at :30; the last one of the day is cut at 16:00."""
    return min(ts + pd.Timedelta(hours=1), ts.normalize() + pd.Timedelta(hours=16))


def now_et():
    return pd.Timestamp.now(tz="America/New_York").tz_localize(None)


def scan(hourly, daily, now=None, imb_thr=IMB_THR, volz_thr=VOLZ_THR):
    """Add imbalance / vol_z / gate / completed / fire columns."""
    h = hourly.copy()
    imb, vz = compute(h)
    g = gate_series(daily)
    gate = g.reindex(h.index.normalize(), method="ffill").to_numpy()
    now = now if now is not None else now_et()
    ends = pd.DatetimeIndex([bar_end(t) for t in h.index])
    h["imbalance"] = imb
    h["vol_z"] = vz
    h["gate"] = pd.array(gate, dtype="boolean")
    h["bar_end"] = ends
    h["completed"] = ends <= now
    h["fire"] = ((h.imbalance < -imb_thr) & (h.vol_z > volz_thr)
                 & h.gate.fillna(False).astype(bool) & h.completed).fillna(False)
    return h


def latest(h):
    """Last completed bar (the one to judge) and the forming bar, if any."""
    done = h[h.completed]
    last = done.iloc[-1] if len(done) else None
    forming = h.iloc[-1] if not bool(h.completed.iloc[-1]) else None
    return last, forming


def volz_class(v):
    if v is None or pd.isna(v):
        return "—"
    if v >= STANDOUT_VOLZ:
        return "standout"
    if v >= 2.0:
        return "elevated"
    return "typical"


def forward_table(h, horizons=((1, "+1h"), (4, "+4h"), (8, "+8h"), (16, "+16h"))):
    closes = h["Close"]
    rows = []
    for ts in h.index[h.fire]:
        loc = closes.index.get_loc(ts)
        r = {"bar": ts, "price": round(float(closes.iloc[loc]), 2),
             "imbalance": round(float(h.at[ts, "imbalance"]), 2),
             "vol z": round(float(h.at[ts, "vol_z"]), 2)}
        for k, lab in horizons:
            r[lab] = ((float(closes.iloc[loc + k]) / float(closes.iloc[loc]) - 1) * 100
                      if loc + k < len(closes) else np.nan)
        rows.append(r)
    return pd.DataFrame(rows)


def map_to_bars(fire_times, bars_index):
    """Place each hourly fire on the bar of another timeframe that contains
    the moment the hourly bar CLOSED, so a fire never appears early."""
    idx = pd.DatetimeIndex(bars_index)
    out = []
    for ts in fire_times:
        moment = bar_end(ts) - pd.Timedelta(seconds=1)
        i = idx.searchsorted(moment, side="right") - 1
        if 0 <= i < len(idx) and idx[i].normalize() == moment.normalize():
            out.append(idx[i])
    return sorted(set(out))
