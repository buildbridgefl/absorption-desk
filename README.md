# Absorption Desk

The flow absorption monitor, spun out of RSI Desk into its own app.
Scanner · Chart (5m / 15m / 30m / 1h / 4h / D) · News for SPY · Journal with no typing.

**Not financial advice.** The signal is validated as a hypothesis (hourly bars,
regime-matched permutation null, p=0.006 out-of-sample, +0.09% to +0.31% over
drift). A strategy built on it is not. This is a monitor.

## Run it
    pip install -r requirements.txt
    streamlit run app.py

## Deploy (phone access)
1. New GitHub repo, e.g. `buildbridgefl/absorption-desk`, push this folder
2. share.streamlit.io → New app → pick the repo → `app.py` → Deploy
3. App → Settings → Secrets → paste from `.streamlit/secrets.toml.example`
4. Open it on your phone → Share → Add to Home Screen

## Secrets
| key | what it's for |
|---|---|
| `ANTHROPIC_API_KEY` | reading Schwab screenshots (a few cents per image) |
| `GITHUB_TOKEN`, `DATA_REPO` | journal survives restarts; reuse the private `rsi-desk-data` repo |

Without the GitHub pair the journal is a local file that Streamlit Cloud wipes when the app sleeps.

## The signal (core/signal.py)
Fires on a COMPLETED hourly bar when imbalance < −0.4, volume z > 1.0 (168-bar window),
and the daily close is above its 200-day MA. `compute()` is copied unchanged from the
validated study. Thresholds are fixed in code on purpose.

Two fixes vs. the old `pages/flow_monitor.py` (signal math unchanged):
- the last completed bar is decided by the clock — after the close the old page judged
  the 14:30 bar instead of the 15:30 bar
- historical fires use each day's own trend gate, not today's

Other timeframes don't compute their own fires. Hourly fires are drawn on the bar where
that hour closed, so a fire never shows up early.

## Journal
- **Screenshot**: upload a Schwab fill/confirmation → Claude reads it → you check the
  table → Save. Exact entry time means exact context: fire on the bar before entry,
  imbalance, vol z, gate, which other tickers fired, releases within 90 min.
- **Schwab CSV** (Accounts → History → Export): bulk import, de-duplicated. Schwab's
  export has no times, so these rows get day-level context only.
- Log each trade one way, not both — a CSV row and a screenshot row of the same fill
  won't match as duplicates.
- Scorecard compares closed trades opened on a fire vs. without. Averages stay hidden
  until each side has 20+ trades.

## News sources (all free, public)
Economic calendar: ForexFactory weekly JSON (USD, high/medium impact).
Headlines: CNBC, MarketWatch, Federal Reserve RSS — filtered to macro and SPY heavyweights.
Earnings: Yahoo, for the largest SPY holdings.
