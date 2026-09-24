"""
News for trading SPY.

SPY barely has news of its own. It moves on scheduled macro releases, the
Fed, and its biggest holdings. So "SPY mode" = economic calendar +
heavyweight earnings + macro headlines. Insider buys and small caps are
left out on purpose.

Every source here is free and public. Nothing is scraped from paid feeds.
"""

import email.utils
import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (absorption-desk personal dashboard)"}

# Free weekly calendar feed (ForexFactory mirror). USD rows only.
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

FEEDS = {
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
}

# Largest SPY holdings -> words that identify them in a headline
HEAVYWEIGHTS = {
    "NVDA": ["nvidia"], "MSFT": ["microsoft"], "AAPL": ["apple"],
    "AMZN": ["amazon"], "META": ["meta ", "facebook", "zuckerberg"],
    "GOOGL": ["alphabet", "google"], "AVGO": ["broadcom"],
    "TSLA": ["tesla", "musk"], "BRK-B": ["berkshire", "buffett"],
    "JPM": ["jpmorgan", "dimon"], "LLY": ["eli lilly", "lilly"],
    "COST": ["costco"], "NFLX": ["netflix"],
}

MACRO_WORDS = [
    "fed", "fomc", "powell", "rate", "yield", "treasury", "inflation", "cpi",
    "ppi", "pce", "jobs", "payroll", "jobless", "unemployment", "gdp",
    "recession", "tariff", "trade war", "oil", "opec", "war", "missile",
    "china", "russia", "iran", "israel", "nuclear", "sanction", "stocks",
    "s&p", "nasdaq", "dow", "wall street", "market", "shutdown", "debt",
    "white house", "trump", "dollar", "bond",
]


def _word_hit(text, words):
    t = f" {text.lower()} "
    return any(re.search(rf"(?<![a-z]){re.escape(w.strip())}(?![a-z])", t) for w in words)


# ------------------------------------------------------------- calendar
def econ_calendar():
    """USD high/medium impact releases for this week, New York time."""
    r = requests.get(CALENDAR_URL, headers=UA, timeout=15)
    r.raise_for_status()
    rows = []
    for e in r.json():
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        try:
            ts = pd.Timestamp(e["date"]).tz_convert("America/New_York").tz_localize(None)
        except Exception:
            continue
        rows.append({"time": ts, "event": e.get("title", ""),
                     "impact": e.get("impact"),
                     "forecast": e.get("forecast") or "",
                     "previous": e.get("previous") or "",
                     "fed": _word_hit(e.get("title", ""), ["fed", "fomc", "powell"])})
    return pd.DataFrame(rows).sort_values("time") if rows else \
        pd.DataFrame(columns=["time", "event", "impact", "forecast", "previous", "fed"])


# ------------------------------------------------------------ headlines
def _rss(source, url):
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        try:
            ts = pd.Timestamp(email.utils.parsedate_to_datetime(pub)) \
                .tz_convert("America/New_York").tz_localize(None)
        except Exception:
            ts = pd.NaT
        if title:
            out.append({"time": ts, "source": source, "title": title, "link": link})
    return out


def headlines_spy():
    """Macro + heavyweight headlines from free RSS feeds."""
    items, errors = [], []
    for src, url in FEEDS.items():
        try:
            items += _rss(src, url)
        except Exception as e:
            errors.append(f"{src}: {type(e).__name__}")
    df = pd.DataFrame(items)
    if df.empty:
        return df, errors

    def tag(row):
        if row.source == "Federal Reserve":
            return "Fed"
        for tk, words in HEAVYWEIGHTS.items():
            if _word_hit(row.title, words):
                return tk
        return "macro" if _word_hit(row.title, MACRO_WORDS) else None

    df["tag"] = df.apply(tag, axis=1)
    df = df[df.tag.notna()].drop_duplicates("title")
    return df.sort_values("time", ascending=False), errors


def headlines_tickers(tickers):
    """Per-ticker news from Yahoo (handles both old and new yfinance shapes)."""
    rows = []
    for tk in tickers:
        try:
            news = yf.Ticker(tk).news or []
        except Exception:
            continue
        for n in news:
            c = n.get("content", n)
            title = c.get("title")
            link = (c.get("canonicalUrl") or {}).get("url") or c.get("link", "")
            src = (c.get("provider") or {}).get("displayName") or c.get("publisher", "")
            ts = c.get("pubDate") or c.get("providerPublishTime")
            try:
                ts = (pd.Timestamp(ts, unit="s", tz="UTC") if isinstance(ts, (int, float))
                      else pd.Timestamp(ts)).tz_convert("America/New_York").tz_localize(None)
            except Exception:
                ts = pd.NaT
            if title:
                rows.append({"time": ts, "source": src, "title": title,
                             "link": link, "tag": tk})
    df = pd.DataFrame(rows)
    return df.drop_duplicates("title").sort_values("time", ascending=False) if len(df) else df


# ------------------------------------------------------------- earnings
def heavyweight_earnings(days=14):
    today = pd.Timestamp.now().normalize()
    rows = []
    for tk in HEAVYWEIGHTS:
        try:
            cal = yf.Ticker(tk).calendar
            dates = cal.get("Earnings Date", []) if isinstance(cal, dict) else []
        except Exception:
            continue
        for d in dates if isinstance(dates, list) else [dates]:
            d = pd.Timestamp(d).normalize()
            if today <= d <= today + pd.Timedelta(days=days):
                rows.append({"ticker": tk, "date": d})
                break
    return pd.DataFrame(rows).sort_values("date") if rows else pd.DataFrame(columns=["ticker", "date"])
