"""Price data from Yahoo. All intraday timestamps are New York time, tz-naive."""

import numpy as np
import pandas as pd
import yfinance as yf

# timeframe -> (yahoo interval, how much to download, how much to show)
TIMEFRAMES = {
    "5m":  ("5m",  "10d",  pd.Timedelta(days=3)),
    "15m": ("15m", "30d",  pd.Timedelta(days=8)),
    "30m": ("30m", "60d",  pd.Timedelta(days=15)),
    "1h":  ("1h",  "90d",  pd.Timedelta(days=22)),
    "4h":  ("1h",  "730d", pd.Timedelta(days=120)),
    "D":   ("1d",  "2y",   pd.Timedelta(days=365)),
}


def _clean(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    try:
        if df.index.tz is not None:
            df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return df[~df.index.duplicated(keep="last")].sort_index()


def download(ticker, interval, period):
    df = yf.download(ticker, period=period, interval=interval,
                     progress=False, auto_adjust=True)
    return _clean(df)


def hourly(ticker, period="90d"):
    return download(ticker, "1h", period)


def daily(ticker, period="2y"):
    df = download(ticker, "1d", period)
    df.index = pd.to_datetime(df.index).normalize()
    return df


def resample_4h(h):
    """Two bars per session: 9:30–13:30 and 13:30–16:00."""
    if h.empty:
        return h
    mins = h.index.hour * 60 + h.index.minute
    start = np.where(mins < 13 * 60 + 30,
                     pd.Timedelta(hours=9, minutes=30),
                     pd.Timedelta(hours=13, minutes=30))
    key = h.index.normalize() + pd.to_timedelta(start)
    g = h.groupby(key)
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                        "Low": g["Low"].min(), "Close": g["Close"].last(),
                        "Volume": g["Volume"].sum()})
    out.index.name = None
    return out


def bars(ticker, tf):
    interval, period, _ = TIMEFRAMES[tf]
    if tf == "D":
        return daily(ticker, period)
    df = download(ticker, interval, period)
    return resample_4h(df) if tf == "4h" else df
